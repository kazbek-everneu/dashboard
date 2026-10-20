"""Navigation and network diagnostics against real engines (see conftest.py for
how to run these): HTTP errors, redirects, a never-idle page, a stylesheet
served as text/html and protection pages."""

import contextlib
import socket
import time
from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Browser

from app.screenshots.capture import navigation, network, profiles
from app.screenshots.capture.errors import NavigationError, PageBlockedError
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureRun, Deadline
from tests.browser.helpers import ENGINES, LocalSite, Response, eventually, make_config

PAGE = """<!doctype html><html><head><title>{title}</title>{head}</head>
<body style="margin:0;font:16px sans-serif">
<h1>{title}</h1><p>An ordinary page with enough text to be a page.</p>{body}
</body></html>"""

CHALLENGE_PAGE = """<!doctype html><html lang="en-US"><head>
<title>Just a moment...</title><meta name="robots" content="noindex,nofollow">
</head><body><div class="main-wrapper" role="main"><div class="main-content">
<h1>example.com</h1><p>Verifying you are human. This may take a few seconds.</p>
</div></div>{script}</body></html>"""


def page(title: str = "Example", head: str = "", body: str = "") -> str:
    return PAGE.format(title=title, head=head, body=body)


@contextlib.asynccontextmanager
async def open_run(
    browser: Browser, url: str, engine: str, **config: object
) -> AsyncIterator[CaptureRun]:
    """A CaptureRun on a fresh capture context, wired like pipeline.run_attempt."""
    viewport = {"width": 1280, "height": 800}
    profile = profiles.build_profile(engine, "desktop", browser_version=browser.version)
    context = await profiles.open_capture_context(browser, viewport, profile)
    try:
        page_ = await context.new_page()
        net = network.NetworkDiagnostics()
        net.attach(page_)
        tracker = network.InflightTracker()
        tracker.attach(page_)
        yield CaptureRun(
            url=url,
            viewport={"width": 1280, "height": 800},
            config=make_config(engine, **config),
            profile=profile,
            page=page_,
            tracker=tracker,
            net=net,
            report=CaptureReport(),
            deadline=Deadline.start(),
        )
    finally:
        await context.close()


def closed_port() -> int:
    with socket.socket() as sock:
        sock.bind(("0.0.0.0", 0))
        return sock.getsockname()[1]


@pytest.mark.parametrize("engine", ENGINES)
async def test_http_error_page_is_reported(engine, browsers, site: LocalSite) -> None:
    url = site.add("/missing", page("Not found"), status=404)

    async with open_run(await browsers(engine), url, engine) as run:
        result = await navigation.navigate(run)
        navigation.report_status(run)

    assert (result.status, result.final_url) == (404, url)
    assert result.needs_reload is False
    assert run.report.warning_codes() == ["http_error"]
    assert run.report.warning("http_error").details["status"] == 404


@pytest.mark.parametrize("engine", ENGINES)
@pytest.mark.parametrize(
    ("path", "target", "level"),
    [
        ("/old", "/new", "info"),
        ("/members", "/wp-login.php?redirect_to=%2Fmembers", "warning"),
    ],
)
async def test_redirect_is_reported(
    engine, path: str, target: str, level: str, browsers, site: LocalSite
) -> None:
    url = site.add(path, "", status=302, headers={"Location": target})
    site.add(target.split("?", 1)[0], page("Landing"))

    async with open_run(await browsers(engine), url, engine) as run:
        result = await navigation.navigate(run)
        navigation.report_status(run)

    assert result.status == 200
    assert result.final_url == site.url(target)
    warning = run.report.warning("redirected")
    assert warning is not None
    assert (warning.level, warning.details["final_url"]) == (level, site.url(target))
    assert run.report.warning("http_error") is None


@pytest.mark.parametrize("engine", ENGINES)
async def test_long_polling_page_finishes_under_networkidle(
    engine, browsers, site: LocalSite
) -> None:
    long_poll = """<script>
    const xhr = new XMLHttpRequest();
    xhr.open('GET', '/poll');
    xhr.send();
    </script>"""
    url = site.add("/", page(body=long_poll))
    site.add("/poll", hang=True)

    async with open_run(
        await browsers(engine), url, engine, wait_until="networkidle"
    ) as run:
        started = time.monotonic()
        result = await navigation.navigate(run)
        elapsed = time.monotonic() - started

    assert elapsed < 20, elapsed
    assert result.wait_strategy == "networkidle→quiet"
    assert result.load_event_fired is True
    assert site.hits["/poll"] == 1


@pytest.mark.parametrize("engine", ENGINES)
async def test_load_event_that_never_fires_is_not_fatal(
    engine, browsers, site: LocalSite
) -> None:
    url = site.add("/", page(body='<img src="/stuck.png" width="10" height="10">'))
    site.add("/stuck.png", hang=True)

    async with open_run(await browsers(engine), url, engine, timeout=2000) as run:
        result = await navigation.navigate(run)
        ready_state = await run.page.evaluate("() => document.readyState")

    assert result.status == 200
    assert result.load_event_fired is False
    assert result.allow_interactive is True
    assert ready_state == "interactive"


@pytest.mark.parametrize("engine", ENGINES)
async def test_stylesheet_served_as_html_is_flagged(
    engine, browsers, site: LocalSite
) -> None:
    head = (
        '<link rel="stylesheet" href="/waf.css">'
        '<link rel="stylesheet" href="/good.css">'
    )
    url = site.add("/", page(head=head))
    site.add("/waf.css", "body { background: rgb(255, 0, 0); }")  # text/html
    site.add("/good.css", "h1 { color: rgb(0, 0, 255); }", content_type="text/css")
    waf_url = site.url("/waf.css")

    async with open_run(await browsers(engine), url, engine) as run:
        await navigation.navigate(run)

        async def flagged() -> bool:
            problems = [record.url for record in run.net.problem_resources()]
            if problems != [waf_url]:
                return False
            # Firefox and WebKit say so in the console; Chromium refuses the
            # sheet silently.
            return engine == "chrome" or run.net.refused_stylesheets() == [waf_url]

        assert await eventually(flagged, timeout_s=5)
        background = await run.page.evaluate(
            "() => getComputedStyle(document.body).backgroundColor"
        )

    record = run.net.lookup(waf_url)
    assert record is not None
    assert (record.resource_type, record.status) == ("stylesheet", 200)
    assert record.content_type.startswith("text/html")
    assert record.blocked_like()
    good = run.net.lookup(site.url("/good.css"))
    assert good is not None and not good.blocked_like() and good.finished
    # The engine really did refuse it.
    assert background != "rgb(255, 0, 0)"


@pytest.mark.parametrize("engine", ENGINES)
async def test_challenge_page_that_passes(engine, browsers, site: LocalSite) -> None:
    replace = "<script>setTimeout(() => location.replace(location.href), 2000)</script>"
    url = site.add(
        "/",
        responses=[
            Response(
                CHALLENGE_PAGE.format(script=replace).encode(),
                403,
                headers={"cf-mitigated": "challenge"},
            ),
            Response(page("Real page").encode()),
        ],
    )

    async with open_run(await browsers(engine), url, engine) as run:
        result = await navigation.navigate(run)
        title = await run.page.title()
        navigation.report_status(run)

    assert title == "Real page"
    assert (result.challenge, result.status, result.final_url) == (
        "cloudflare",
        200,
        url,
    )
    warning = run.report.warning("challenge_passed")
    assert warning is not None
    assert warning.level == "info"
    assert warning.details["vendor"] == "cloudflare"
    assert 1000 <= warning.details["waited_ms"] < 15_000
    assert run.report.warning_codes() == ["challenge_passed"]
    assert run.report.meta["navigation"]["challenge"] == "cloudflare"
    assert site.hits["/"] == 2


@pytest.mark.parametrize("engine", ENGINES)
async def test_persistent_challenge_page_is_blocked(
    engine, browsers, site: LocalSite, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr("app.screenshots.capture.config.CHALLENGE_WAIT_MS", 3000)
    url = site.add(
        "/",
        CHALLENGE_PAGE.format(script=""),
        status=403,
        headers={"cf-mitigated": "challenge"},
    )

    async with open_run(await browsers(engine), url, engine) as run:
        started = time.monotonic()
        with pytest.raises(PageBlockedError) as exc_info:
            await navigation.navigate(run)
        elapsed = time.monotonic() - started

    assert (exc_info.value.vendor, exc_info.value.status) == ("cloudflare", 403)
    assert 3 <= elapsed < 15, elapsed
    assert run.report.warnings == []


@pytest.mark.parametrize("engine", ENGINES)
async def test_refused_connection_is_a_navigation_error(
    engine, browsers, site: LocalSite
) -> None:
    url = f"http://{site.host}:{closed_port()}/"

    async with open_run(await browsers(engine), url, engine) as run:
        with pytest.raises(NavigationError) as exc_info:
            await navigation.navigate(run)

    assert exc_info.value.net_error == "ERR_CONNECTION_REFUSED"
    assert url in str(exc_info.value)
