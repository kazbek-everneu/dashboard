"""The capture pipeline on the real browsers: overflow cropping (plan item 6),
broken assets that no longer cost the screenshot and their recovery (item 12),
and the image height of a mobile 100vh hero."""

import inspect

import pytest

from app.screenshots.capture import navigation, pipeline
from app.screenshots.capture.profiles import DeviceProfile
from tests.browser.helpers import (
    ENGINES,
    Response,
    capture,
    image_size,
    make_config,
    near,
    pixels_at,
)

RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
WHITE = (255, 255, 255)

WIDE_PAGE = """<!doctype html><html{html_attrs}><head><meta charset="utf-8">
<title>wide</title><style>
html, body {{ margin: 0; }}
body {{ {body_style} font: 16px sans-serif; }}
.wide {{ width: 3000px; height: 300px; background: #0000ff; }}
.tall {{ height: 1500px; background: #00ff00; }}
</style></head><body>
<div class="wide">wide row</div><div class="tall">content</div>
</body></html>"""

RTL_PAGE = """<!doctype html><html dir="rtl"><head><meta charset="utf-8">
<title>rtl</title><style>
html, body { margin: 0; }
body { font: 16px sans-serif; }
/* Starts on the right (inline start) and overflows to the left: red right
   edge, blue far left end. */
.wide {
    width: 3000px; height: 300px;
    background: linear-gradient(to left, #ff0000 0 300px, #ffffff 300px 2700px,
                                #0000ff 2700px);
}
.tall { height: 1200px; background: #00ff00; }
</style></head><body><div class="wide">بداية</div><div class="tall">محتوى</div>
</body></html>"""

MISSING_IMAGE_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>missing image</title></head>
<body style="margin:0;font:16px sans-serif">
<p>text before the image</p>
<img src="/missing.png" alt="" style="display:block;width:300px;height:200px">
<div style="height:1200px;background:#00ff00">content</div>
</body></html>"""

STYLED_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<title>styled</title><link rel="stylesheet" href="/site.css"></head>
<body><div class="hero">styled page</div><p>more text</p></body></html>"""
SITE_CSS = """html, body { margin: 0; }
body { background: #00ff00; }
.hero { height: 600px; background: #0000ff; }"""

HERO_PAGE = """<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>hero</title><style>
html, body { margin: 0; }
.hero { height: 100vh; background: #ff0000; }
.rest { height: 1500px; background: #0000ff; }
</style></head><body><div class="hero">hero</div><div class="rest">rest</div>
</body></html>"""


def _reload_implemented() -> bool:
    return "NotImplementedError" not in inspect.getsource(navigation.reload)


@pytest.mark.parametrize("engine", ENGINES)
async def test_hidden_overflow_is_cropped_to_the_viewport(
    engine, browsers, site
) -> None:
    url = site.add(
        "/", WIDE_PAGE.format(html_attrs="", body_style="overflow-x: hidden;")
    )
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    width, height = image_size(result.outcome.data)
    assert width == 1280
    assert height >= 1800
    warning = result.report.warning("overflow_cropped")
    assert warning is not None, result.report.warning_codes()
    assert warning.level == "info"
    assert warning.details["viewport_width"] == 1280
    assert warning.details["document_width"] >= 3000
    page_meta = result.report.meta["page"]
    assert page_meta["clipped_width"] == 1280
    assert page_meta["document_width"] >= 3000
    (top, below) = await pixels_at(
        await browsers("chrome"), result.outcome.data, [(1200, 100), (1200, 900)]
    )
    assert near(top, BLUE), top
    assert near(below, GREEN), below


@pytest.mark.parametrize("engine", ENGINES)
async def test_visible_overflow_is_cropped_with_a_warning(
    engine, browsers, site
) -> None:
    url = site.add("/", WIDE_PAGE.format(html_attrs="", body_style=""))
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    assert image_size(result.outcome.data)[0] == 1280
    warning = result.report.warning("overflow_cropped")
    assert warning is not None, result.report.warning_codes()
    assert warning.level == "warning"


@pytest.mark.parametrize("engine", ENGINES)
async def test_right_to_left_overflow_keeps_the_right_edge(
    engine, browsers, site
) -> None:
    url = site.add("/", RTL_PAGE)
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    assert image_size(result.outcome.data)[0] == 1280
    assert result.report.warning("overflow_cropped") is not None
    right, left, below = await pixels_at(
        await browsers("chrome"),
        result.outcome.data,
        [(1250, 150), (30, 150), (640, 600)],
    )
    # The page starts on the right: its red start edge is in the image, the
    # blue far end (2700 px to the left) is not.
    assert near(right, RED), right
    assert near(left, WHITE), left
    assert near(below, GREEN), below


@pytest.mark.parametrize("engine", ENGINES)
async def test_missing_image_keeps_the_screenshot(engine, browsers, site) -> None:
    url = site.add("/", MISSING_IMAGE_PAGE)
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    assert result.attempts == 1
    width, height = image_size(result.outcome.data)
    assert width == 1280
    assert height >= 1200
    codes = result.report.warning_codes()
    assert "assets_missing" in codes, codes
    assert "page_reloaded" not in codes
    missing = result.report.warning("assets_missing")
    assert missing is not None
    assert any(
        str(sample.get("url", "")).endswith("/missing.png")
        for sample in missing.details["samples"]
    ), missing.details
    # A plain 404 never triggers a reload.
    assert site.hits["/"] == 1


@pytest.mark.xfail(
    not _reload_implemented(),
    reason="needs B2's navigation.reload and B4's stylesheet issues",
    strict=False,
)
@pytest.mark.parametrize("engine", ENGINES)
async def test_stylesheet_served_as_html_once_is_recovered(
    engine, browsers, site
) -> None:
    url = site.add("/", STYLED_PAGE)
    site.add(
        "/site.css",
        responses=[
            Response(b"<html><body>Access denied</body></html>", 200),
            Response(SITE_CSS.encode(), 200, "text/css"),
        ],
    )
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    codes = result.report.warning_codes()
    assert "assets_recovered" in codes or "page_reloaded" in codes, codes
    assert "styles_missing" not in codes
    assert site.hits["/site.css"] >= 2
    hero, body = await pixels_at(
        await browsers("chrome"), result.outcome.data, [(100, 300), (100, 700)]
    )
    assert near(hero, BLUE), hero
    assert near(body, GREEN), body


@pytest.mark.parametrize("engine", ["chrome", "webkit"])
async def test_mobile_100vh_hero_keeps_the_viewport_height(
    engine, browsers, site
) -> None:
    url = site.add("/", HERO_PAGE)
    browser = await browsers(engine)
    profile = DeviceProfile(device_type="mobile", is_mobile=True, has_touch=True)

    result = await pipeline.capture_with_retries(
        browser=browser,
        url=url,
        viewport={"width": 390, "height": 844},
        config=make_config(engine),
        profile=profile,
    )

    assert result.ok, result.error
    width, height = image_size(result.outcome.data)
    assert width == 390
    assert height == 844 + 1500
    inside, after = await pixels_at(
        await browsers("chrome"), result.outcome.data, [(200, 840), (200, 848)]
    )
    assert near(inside, RED), inside
    assert near(after, BLUE), after
    assert result.report.meta["device"]["is_mobile"] is True
