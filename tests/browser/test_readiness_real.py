"""readiness.finalize / retry_in_place against the real engines: hanging fonts,
broken images and stylesheets, the activity tracker and its fast path.

The CaptureRun is built the way pipeline.run_attempt builds it: capture
context with the init script, the production NetworkDiagnostics (whose
resource registry classifies the issues) and InflightTracker attached before
navigation.
"""

import contextlib
import struct
import time
import zlib
from collections.abc import AsyncIterator
from typing import Any

import pytest
from playwright.async_api import Browser

from app.screenshots.capture import network, profiles, readiness
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureRun, Deadline
from tests.browser.helpers import ENGINES, LocalSite, Response, make_config


@contextlib.asynccontextmanager
async def opened(
    browser: Browser,
    engine: str,
    url: str,
    *,
    wait_until: str = "load",
) -> AsyncIterator[CaptureRun]:
    """*url* opened like pipeline.run_attempt opens a capture page."""
    viewport = {"width": 1280, "height": 800}
    profile = profiles.build_profile(
        engine,
        profiles.resolve_device_type(viewport),
        browser_version=browser.version,
    )
    context = await profiles.open_capture_context(browser, viewport, profile)
    try:
        page = await context.new_page()
        net = network.NetworkDiagnostics()
        net.attach(page)
        tracker = network.InflightTracker()
        tracker.attach(page)
        run = CaptureRun(
            url=url,
            viewport={"width": 1280, "height": 800},
            config=make_config(engine),
            profile=profile,
            page=page,
            tracker=tracker,
            net=net,
            report=CaptureReport(),
            deadline=Deadline.start(),
        )
        await page.goto(url, wait_until=wait_until, timeout=30000)
        yield run
    finally:
        await context.close()


def png(width: int, height: int, rgb: tuple[int, int, int] = (0, 128, 255)) -> bytes:
    def chunk(kind: bytes, data: bytes) -> bytes:
        crc = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)

    rows = b"".join(b"\x00" + bytes(rgb) * width for _ in range(height))
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", header)
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def page_html(head: str = "", body: str = "<p>readiness test page</p>") -> str:
    return (
        "<!doctype html><html><head><meta charset=utf-8><title>readiness</title>"
        f"{head}</head><body style='margin:0;font:16px sans-serif'>{body}"
        "</body></html>"
    )


IMG_STYLE = "display:block;width:200px;height:100px"


async def _finalize(run: CaptureRun, **kwargs: Any) -> tuple[Any, float]:
    started = time.monotonic()
    result = await readiness.finalize(run, window_ms=8000, **kwargs)
    return result, time.monotonic() - started


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_font_that_never_arrives_is_only_a_warning(
    engine: str, browsers, site: LocalSite
) -> None:
    site.add("/hang.woff2", b"", content_type="font/woff2", hang=True)
    url = site.add(
        "/",
        page_html(
            "<style>@font-face{font-family:Hang;src:url(/hang.woff2) "
            "format('woff2')} body{font-family:Hang,sans-serif!important}</style>"
        ),
    )
    browser = await browsers(engine)

    # The pending font holds back the load event: navigate like the
    # domcontentloaded fallback does and accept readyState "interactive".
    async with opened(browser, engine, url, wait_until="domcontentloaded") as run:
        result, elapsed = await _finalize(run, allow_interactive_ready_state=True)

    assert result.fonts_ready is False
    assert result.stable is True, result
    assert result.unstable_reason is None
    # 5 s fonts cap + the three-sample stability rule (the hanging request
    # keeps the fast path closed).
    assert elapsed < 8.5, elapsed
    warning = run.report.warning("fonts_timeout")
    assert warning is not None
    assert warning.details["timeout_ms"] == 5000
    assert warning.details["families"] == ["Hang"]
    assert run.report.meta["readiness"]["fonts_ready"] is False


# ---------------------------------------------------------------------------
# Broken images and stylesheets
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_404_image_is_missing_and_not_asked_for_again(
    engine: str, browsers, site: LocalSite
) -> None:
    site.add("/gone.png", b"<html>not found</html>", status=404)
    url = site.add(
        "/",
        page_html(body=f"<p>text</p><img src=/gone.png alt=gone style='{IMG_STYLE}'>"),
    )
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        result, _ = await _finalize(run)
        still = await readiness.retry_in_place(run, result.issues)

    gone = site.url("/gone.png")
    assert [(i.kind, i.url, i.category, i.status) for i in result.issues] == [
        ("img", gone, "missing", 404)
    ]
    assert run.scratch["asset_error"].startswith("Visible page assets did not load")
    assert still == result.issues
    assert site.hits["/gone.png"] == 1


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_blocked_image_is_recovered_in_place(
    engine: str, browsers, site: LocalSite
) -> None:
    site.add(
        "/photo.png",
        responses=[
            Response(b"<html>blocked</html>", 403, "text/html"),
            Response(png(40, 20), 200, "image/png"),
        ],
    )
    url = site.add(
        "/",
        page_html(
            body=f"<p>text</p><img id=p src=/photo.png alt=photo style='{IMG_STYLE}'>"
        ),
    )
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        result, _ = await _finalize(run)
        still = await readiness.retry_in_place(run, result.issues)
        natural_width = await run.page.evaluate(
            "() => document.getElementById('p').naturalWidth"
        )

    assert [(i.kind, i.category, i.status) for i in result.issues] == [
        ("img", "blocked", 403)
    ]
    assert still == ()
    assert natural_width == 40
    assert site.hits["/photo.png"] == 2  # same URL, asked once more
    assert "asset_error" not in run.scratch


@pytest.mark.parametrize("engine", ENGINES)
async def test_an_html_stylesheet_is_recovered_in_place(
    engine: str, browsers, site: LocalSite
) -> None:
    site.add(
        "/site.css",
        responses=[
            Response(b"<html>waf</html>", 200, "text/html"),
            Response(b".box{background:rgb(0, 128, 0)}", 200, "text/css"),
        ],
    )
    url = site.add(
        "/",
        page_html(
            "<link rel=stylesheet href=/site.css>",
            "<p>text</p><div class=box style='height:50px'>box</div>",
        ),
    )
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        result, _ = await _finalize(run)
        still = await readiness.retry_in_place(run, result.issues)
        background = await run.page.evaluate(
            "() => getComputedStyle(document.querySelector('.box')).backgroundColor"
        )

    assert [(i.kind, i.url, i.category) for i in result.issues] == [
        ("stylesheet", site.url("/site.css"), "blocked")
    ]
    assert still == ()
    assert background == "rgb(0, 128, 0)"
    assert site.hits["/site.css"] == 2


# ---------------------------------------------------------------------------
# Activity tracker and the stability fast path
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_countdown_is_stable_by_the_old_rule(
    engine: str, browsers, site: LocalSite
) -> None:
    url = site.add(
        "/",
        page_html(
            body="<p>Offer ends in <span id=t>00:30</span></p><script>"
            "let left = 30; setInterval(() => { left -= 1;"
            " document.getElementById('t').textContent ="
            " '00:' + String(left).padStart(2, '0'); }, 1000);</script>"
        ),
    )
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        result, elapsed = await _finalize(run)

    assert result.stable is True
    assert result.fast_path is False
    assert elapsed < 4.0, elapsed


@pytest.mark.parametrize("engine", ENGINES)
async def test_a_count_up_keeps_the_fast_path_closed_until_it_ends(
    engine: str, browsers, site: LocalSite
) -> None:
    # Counts to 1000 in ~2 s while a bar grows (so the sample rule cannot
    # settle either), then stops for good.
    url = site.add(
        "/",
        page_html(
            body="<p id=n>0</p><div id=bar style='height:0;background:#09f'></div>"
            "<script>const t0 = performance.now(); const tick = () => {"
            " const p = Math.min(1, (performance.now() - t0) / 2000);"
            " document.getElementById('n').textContent = String(Math.round(p * 1000));"
            " document.getElementById('bar').style.height = Math.round(p * 400) + 'px';"
            " if (p < 1) { requestAnimationFrame(tick); }"
            " else { window.countDoneAt = performance.now(); } };"
            " requestAnimationFrame(tick);</script>"
        ),
    )
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        result, _ = await _finalize(run)
        done_at, now = await run.page.evaluate(
            "() => [window.countDoneAt ?? null, performance.now()]"
        )

    assert result.stable is True
    assert done_at is not None, "readiness returned while the count was running"
    if result.fast_path:
        assert now - done_at >= 1400, now - done_at


@pytest.mark.parametrize("engine", ENGINES)
async def test_an_idle_static_page_takes_the_fast_path(
    engine: str, browsers, site: LocalSite
) -> None:
    url = site.add("/", page_html(body="<h1>static</h1><p>nothing moves here</p>"))
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        await run.page.wait_for_timeout(2000)
        result, elapsed = await _finalize(run)

    assert result.stable is True
    assert result.fast_path is True
    assert elapsed < 1.2, elapsed
    assert run.report.meta["readiness"] == {
        "stable": True,
        "fast_path": True,
        "fonts_ready": True,
    }


@pytest.mark.parametrize("engine", ENGINES)
async def test_drain_hides_our_own_dom_changes(
    engine: str, browsers, site: LocalSite
) -> None:
    url = site.add(
        "/",
        page_html(body="<p id=a>tracked</p><iframe srcdoc='<p>child</p>'></iframe>"),
    )
    browser = await browsers(engine)

    async with opened(browser, engine, url) as run:
        page = run.page
        await page.wait_for_timeout(700)
        before = await page.evaluate("() => window.__qaActivity.quietForMs()")
        # Our own change (text, style, a new block that resizes the page),
        # drained right away: not page activity.
        await page.evaluate(
            "() => { const a = document.getElementById('a');"
            " a.textContent = 'changed by us'; a.style.color = 'red';"
            " const div = document.createElement('div');"
            " div.style.height = '300px'; document.body.appendChild(div);"
            " window.__qaActivity.drain(); }"
        )
        await page.wait_for_timeout(300)
        drained = await page.evaluate("() => window.__qaActivity.quietForMs()")
        # The same kind of change without drain() counts.
        await page.evaluate(
            "() => { document.getElementById('a').textContent = 'page change'; }"
        )
        await page.wait_for_timeout(300)
        undrained = await page.evaluate("() => window.__qaActivity.quietForMs()")
        child = await page.frames[1].evaluate("() => typeof window.__qaActivity")

    assert drained >= before + 250, (before, drained)
    assert undrained < 400, undrained
    assert child == "undefined"  # top frame only
