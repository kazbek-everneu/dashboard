"""Prepare stage on real engines: overlays, content-visibility, inner scroll
containers, smooth scrolling, the infinite-scroll guard, carousels and the
IntersectionObserver override."""

import contextlib
import struct
import zlib
from collections.abc import AsyncIterator

import pytest
from playwright.async_api import Browser

from app.screenshots.capture import network, prepare, profiles
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureRun, Deadline
from tests.browser.helpers import (
    ENGINES,
    capture,
    image_size,
    make_config,
    near,
    pixels_at,
)


def solid_png(width: int, height: int, rgb: tuple[int, int, int]) -> bytes:
    """A width x height PNG of one color (stdlib only)."""

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    row = b"\x00" + bytes(rgb) * width
    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row * height))
        + chunk(b"IEND", b"")
    )


@contextlib.asynccontextmanager
async def prepared(
    browser: Browser, engine: str, url: str, *, viewport: tuple[int, int] = (1280, 800)
) -> AsyncIterator[CaptureRun]:
    """*url* loaded in a capture context (init script included) and run
    through prepare.prepare_page; the context closes on exit."""
    vp = {"width": viewport[0], "height": viewport[1]}
    profile = profiles.build_profile(
        engine, profiles.resolve_device_type(vp), browser_version=browser.version
    )
    context = await profiles.open_capture_context(browser, vp, profile)
    try:
        page = await context.new_page()
        tracker = network.InflightTracker()
        tracker.attach(page)
        net = network.NetworkDiagnostics()
        net.attach(page)
        run = CaptureRun(
            url=url,
            viewport={"width": vp["width"], "height": vp["height"]},
            config=make_config(engine),
            profile=profile,
            page=page,
            tracker=tracker,
            net=net,
            report=CaptureReport(),
            deadline=Deadline(),
        )
        await page.goto(url, wait_until="load")
        run.prep = await prepare.prepare_page(run)
        yield run
    finally:
        await context.close()


# ---------------------------------------------------------------------------
# Overlays
# ---------------------------------------------------------------------------

OVERLAY_PAGE = """<!doctype html><html><head><title>overlays</title><style>
body { margin: 0; font: 16px sans-serif; }
.top-banner { position: sticky; top: 0; height: 60px; background: #333;
  color: #fff; z-index: 100; }
main { height: 2500px; padding: 20px; }
.cta { display: inline-block; padding: 10px 20px; background: #0a0; color: #fff; }
#nl-modal { position: fixed; top: 20%; left: 25%; width: 50%; height: 40%;
  background: #fff; border: 2px solid #000; z-index: 2000; }
.cky-overlay { position: fixed; inset: 0; background: rgba(0, 0, 0, .5);
  z-index: 9998; }
.cky-consent-container { position: fixed; left: 0; right: 0; bottom: 0;
  height: 120px; background: #eee; z-index: 9999; }
.cc-bar-x { position: fixed; left: 0; right: 0; bottom: 120px; padding: 12px;
  background: #222; color: #fff; z-index: 500; }
#hubspot-messages-iframe-container { position: fixed; right: 20px; bottom: 20px;
  width: 80px; height: 80px; background: #f80; z-index: 1000; }
.back-to-top { position: fixed; left: 20px; bottom: 20px; width: 40px;
  height: 40px; z-index: 1000; }
</style></head><body>
<header class="top-banner">Site header <a href="#">Home</a></header>
<main>
  <p>Article text.</p>
  <a href="#" id="cta" class="cta newsletter-popup">NEWSLETTER SIGNUP</a>
</main>
<footer><a id="cookie-link" class="cookie-link" href="#">Cookie policy</a></footer>
<div id="nl-modal" class="newsletter-popup" role="dialog">Subscribe to our news</div>
<div class="cky-overlay"></div>
<div class="cky-consent-container">We value your privacy <button>Accept all</button></div>
<div class="cc-bar-x">We use cookies to improve your experience.
  <button>Accept</button></div>
<div id="hubspot-messages-iframe-container"></div>
<button class="back-to-top" aria-label="Back to top">^</button>
</body></html>"""

_DISPLAY = """(selectors) => selectors.map((selector) => {
    const el = document.querySelector(selector);
    return el ? window.getComputedStyle(el).display : 'missing';
})"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_overlays_hidden_by_tier_and_position(engine, browsers, site) -> None:
    url = site.add("/", OVERLAY_PAGE)
    browser = await browsers(engine)

    async with prepared(browser, engine, url) as run:
        keep = ["#cta", ".top-banner", "#cookie-link", ".back-to-top"]
        hide = [
            "#nl-modal",
            ".cky-overlay",
            ".cky-consent-container",
            ".cc-bar-x",
            "#hubspot-messages-iframe-container",
        ]
        kept = await run.page.evaluate(_DISPLAY, keep)
        hidden = await run.page.evaluate(_DISPLAY, hide)

    assert all(display not in ("none", "missing") for display in kept), kept
    assert hidden == ["none"] * len(hide)
    assert run.prep is not None and run.prep.overlays_hidden == 5
    overlays = run.report.meta["overlays"]
    assert overlays["hidden"] == 5
    rules = {(sample["tier"], sample["rule"]) for sample in overlays["samples"]}
    assert ("B", "popup") in rules
    assert ("B", "cookie") in rules
    assert ("A", ".cky-consent-container") in rules
    assert ("A", "#hubspot-messages-iframe-container") in rules
    assert all(len(sample["element"]) <= 80 for sample in overlays["samples"])
    by_element = {sample["element"]: sample for sample in overlays["samples"]}
    modal = by_element["div#nl-modal.newsletter-popup"]
    assert set(modal["rect"]) == {"x", "y", "width", "height"}
    assert modal["rect"]["width"] >= 600 and modal["rect"]["height"] >= 300
    warning = run.report.warning("overlays_hidden")
    assert warning is not None and warning.level == "info"
    assert warning.details["count"] == 5
    assert warning.details["samples"] == overlays["samples"]


# ---------------------------------------------------------------------------
# content-visibility
# ---------------------------------------------------------------------------

CV_PAGE = """<!doctype html><html><head><title>cv</title><style>
body { margin: 0; font: 16px sans-serif; }
.intro { height: 1200px; background: #fff; }
section { content-visibility: auto; contain-intrinsic-size: auto 400px; }
.block { height: 800px; }
</style></head><body>
<div class="intro">Three sections below render only when visible.</div>
<section><div class="block" style="background:#ff0000"></div></section>
<section><div class="block" style="background:#00ff00"></div></section>
<section><div class="block" style="background:#0000ff"></div></section>
</body></html>"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_content_visibility_sections_are_rendered(engine, browsers, site) -> None:
    url = site.add("/", CV_PAGE)
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    _width, height = image_size(result.outcome.data)
    assert height >= 3600
    red, green, blue = await pixels_at(
        await browsers("chrome"),
        result.outcome.data,
        [(640, 1600), (640, 2400), (640, 3200)],
    )
    assert near(red, (255, 0, 0)), red
    assert near(green, (0, 255, 0)), green
    assert near(blue, (0, 0, 255)), blue


# ---------------------------------------------------------------------------
# Inner scroll container
# ---------------------------------------------------------------------------

INNER_SCROLLER_PAGE = """<!doctype html><html><head><title>inner</title><style>
html, body { margin: 0; height: 100%; overflow: hidden; font: 16px sans-serif; }
#app { height: 100vh; overflow: auto; }
.content { position: relative; height: 4000px; background: #f4f4f4; }
.marker { position: absolute; left: 100px; top: 3600px; width: 300px;
  height: 150px; background: #ff00ff; }
</style></head><body>
<div id="app"><div class="content">
  <p>An app shell that scrolls inside #app.</p>
  <div class="marker"></div>
</div></div>
</body></html>"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_inner_scroller_is_expanded(engine, browsers, site) -> None:
    url = site.add("/", INNER_SCROLLER_PAGE)
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    _width, height = image_size(result.outcome.data)
    assert height >= 4000
    (marker,) = await pixels_at(
        await browsers("chrome"), result.outcome.data, [(250, 3675)]
    )
    assert near(marker, (255, 0, 255)), marker
    warning = result.report.warning("inner_scroll_expanded")
    assert warning is not None
    assert warning.details["scroll_height"] >= 4000
    assert warning.details["element"] == "div#app"
    assert result.report.meta["page"]["inner_scroller"] is True


# ---------------------------------------------------------------------------
# Smooth scrolling
# ---------------------------------------------------------------------------

SMOOTH_SCROLL_PAGE = """<!doctype html><html><head><title>smooth</title><style>
html { scroll-behavior: smooth; }
body { margin: 0; font: 16px sans-serif; }
.spacer { height: 3000px; background: #fafafa; }
#slot { height: 300px; }
#slot img { display: block; width: 200px; height: 200px; }
</style></head><body>
<div class="spacer">A long page whose last image loads at the very bottom.</div>
<div id="slot"></div>
<script>
window.addEventListener('scroll', () => {
  const bottom = window.scrollY + window.innerHeight;
  if (!window.__injected && bottom >= document.documentElement.scrollHeight - 50) {
    window.__injected = true;
    const img = new Image();
    img.src = '/bottom.png';
    document.getElementById('slot').appendChild(img);
  }
});
</script>
</body></html>"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_smooth_scroll_page_loads_its_bottom_image(
    engine, browsers, site
) -> None:
    url = site.add("/", SMOOTH_SCROLL_PAGE)
    site.add(
        "/bottom.png",
        solid_png(200, 200, (255, 102, 0)),
        content_type="image/png",
        delay_s=0.3,
    )
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    assert site.hits["/bottom.png"] >= 1
    (pixel,) = await pixels_at(
        await browsers("chrome"), result.outcome.data, [(100, 3100)]
    )
    assert near(pixel, (255, 102, 0)), pixel


# ---------------------------------------------------------------------------
# Infinite scroll
# ---------------------------------------------------------------------------

INFINITE_SCROLL_PAGE = """<!doctype html><html><head><title>infinite</title><style>
body { margin: 0; font: 16px sans-serif; }
.chunk { height: 2000px; border-top: 4px solid #888; background: #eef; }
#sentinel { height: 10px; }
</style></head><body>
<div id="list"><div class="chunk">page 1</div></div>
<div id="sentinel"></div>
<script>
let pages = 1;
const list = document.getElementById('list');
const sentinel = document.getElementById('sentinel');
const io = new IntersectionObserver((entries, observer) => {
  if (!entries.some((entry) => entry.isIntersecting)) return;
  pages += 1;
  const chunk = document.createElement('div');
  chunk.className = 'chunk';
  chunk.textContent = 'page ' + pages;
  list.appendChild(chunk);
  observer.unobserve(sentinel);
  observer.observe(sentinel);
});
io.observe(sentinel);
</script>
</body></html>"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_infinite_scroll_is_stopped(engine, browsers, site) -> None:
    url = site.add("/", INFINITE_SCROLL_PAGE)
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    warning = result.report.warning("infinite_scroll_stopped")
    assert warning is not None and warning.level == "warning"
    assert warning.details["growth_events"] == 3
    _width, height = image_size(result.outcome.data)
    assert 6000 <= height <= 30000, height


# ---------------------------------------------------------------------------
# Carousels
# ---------------------------------------------------------------------------

CAROUSEL_PAGE = """<!doctype html><html><head><title>carousels</title></head><body>
<p>Carousel stubs</p>
<div class="swiper" id="plain"></div>
<div class="swiper swiper-initialized" id="looped"></div>
<div class="slick-initialized" id="slick"></div>
<div class="flickity-enabled" id="flickity"></div>
<div class="splide" id="splide"></div>
<script>
window.__spy = [];
const spy = (name) => (...args) => { window.__spy.push([name].concat(args)); };
document.getElementById('plain').swiper = {
  params: { loop: false },
  autoplay: { stop: spy('plain.autoplay.stop') },
  slideTo: spy('plain.slideTo'),
  slideToLoop: spy('plain.slideToLoop'),
};
document.getElementById('looped').swiper = {
  params: { loop: true },
  autoplay: { stop: spy('looped.autoplay.stop') },
  slideTo: spy('looped.slideTo'),
  slideToLoop: spy('looped.slideToLoop'),
};
window.jQuery = function (el) {
  return {
    slick: (...args) => { window.__spy.push(['slick', el.id].concat(args)); },
    data: () => undefined,
    trigger: () => {},
  };
};
window.jQuery.fn = { slick: function () {} };
window.Flickity = {
  data: (el) => (el.id === 'flickity'
    ? { stopPlayer: spy('flickity.stopPlayer'), select: spy('flickity.select') }
    : undefined),
};
document.getElementById('splide').splide = {
  Components: { Autoplay: { pause: spy('splide.pause') } },
  go: spy('splide.go'),
};
</script>
</body></html>"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_carousels_are_paused_on_the_first_slide(engine, browsers, site) -> None:
    url = site.add("/", CAROUSEL_PAGE)
    browser = await browsers(engine)

    async with prepared(browser, engine, url) as run:
        calls = await run.page.evaluate("() => window.__spy")

    assert ["plain.autoplay.stop"] in calls
    assert ["plain.slideTo", 0, 0] in calls
    assert ["looped.autoplay.stop"] in calls
    assert ["looped.slideToLoop", 0, 0] in calls
    assert not any(call[0] in ("plain.slideToLoop", "looped.slideTo") for call in calls)
    assert ["slick", "slick", "slickPause"] in calls
    assert ["slick", "slick", "slickGoTo", 0, True] in calls
    assert ["flickity.stopPlayer"] in calls
    assert ["flickity.select", 0, False, True] in calls
    assert ["splide.pause"] in calls
    assert ["splide.go", 0] in calls
    assert run.prep is not None and run.prep.carousels_paused == 5
    warning = run.report.warning("carousels_paused")
    assert warning is not None
    assert warning.details == {
        "total": 5,
        "swiper": 2,
        "slick": 1,
        "flickity": 1,
        "splide": 1,
        "count": 1,
    }


# ---------------------------------------------------------------------------
# IntersectionObserver override v2
# ---------------------------------------------------------------------------

IO_PAGE = """<!doctype html><html><head><title>io</title></head><body>
<p>IntersectionObserver override</p>
<div style="height: 5000px"></div>
<div id="target" style="height: 50px">far below the fold</div>
<div id="many"></div>
</body></html>"""

_IO_PROBE = """async () => {
    const tick = () => new Promise((resolve) => setTimeout(resolve, 50));
    const frames = () => new Promise((resolve) => {
        requestAnimationFrame(() => requestAnimationFrame(() => resolve()));
    });
    const target = document.getElementById('target');
    const calls = [];
    const io = new IntersectionObserver((entries, observer) => {
        entries.forEach((entry) => calls.push({
            intersecting: entry.isIntersecting,
            same: observer === io,
        }));
    }, { rootMargin: '0px' });
    for (let i = 0; i < 3; i += 1) {
        io.unobserve(target);
        io.observe(target);
        await tick();
        await frames();
    }
    await tick();
    return {
        calls,
        stats: Object.assign({}, window.__qaIO),
        isInstance: io instanceof IntersectionObserver,
        rootMargin: typeof io.rootMargin,
        thresholds: Array.isArray(io.thresholds),
    };
}"""

_IO_TOTAL_CAP = """async () => {
    const holder = document.getElementById('many');
    let fake = 0;
    const io = new IntersectionObserver((entries) => {
        entries.forEach((entry) => { if (entry.isIntersecting) fake += 1; });
    });
    for (let i = 0; i < 310; i += 1) {
        const el = document.createElement('span');
        holder.appendChild(el);
        io.observe(el);
    }
    await new Promise((resolve) => setTimeout(resolve, 100));
    return Object.assign({}, window.__qaIO);
}"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_io_override_fakes_twice_per_target_then_goes_native(
    engine, browsers, site
) -> None:
    url = site.add("/", IO_PAGE)
    browser = await browsers(engine)
    vp = {"width": 1280, "height": 800}
    context = await profiles.open_capture_context(browser, vp)
    try:
        page = await context.new_page()
        await page.goto(url, wait_until="load")
        probe = await page.evaluate(_IO_PROBE)
        await page.goto(url, wait_until="load")
        capped = await page.evaluate(_IO_TOTAL_CAP)
    finally:
        await context.close()

    assert probe["isInstance"] is True
    assert probe["rootMargin"] == "string"
    assert probe["thresholds"] is True
    assert probe["stats"] == {"fake": 2, "native": 1, "observes": 3}
    # Two fake "in view" deliveries, then the native observer's real verdict
    # for a target 5000 px below the fold — always to the page's own observer.
    assert probe["calls"] == [
        {"intersecting": True, "same": True},
        {"intersecting": True, "same": True},
        {"intersecting": False, "same": True},
    ]
    assert capped == {"fake": 300, "native": 10, "observes": 310}
