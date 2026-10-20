"""Smoke test of the real-browser harness: one synthetic page per engine."""

import pytest

from tests.browser.helpers import ENGINES, capture, image_size, near, pixels_at

GREEN_PAGE = """<!doctype html><html><head><title>smoke</title></head>
<body style="margin:0;font:16px sans-serif">
<div style="height:1500px;background:#00ff00">smoke test page</div>
</body></html>"""


@pytest.mark.parametrize("engine", ENGINES)
async def test_capture_synthetic_page(engine, browsers, site) -> None:
    url = site.add("/", GREEN_PAGE)
    browser = await browsers(engine)

    result = await capture(browser, url, browser_name=engine, viewport=(1280, 800))

    assert result.ok, result.error
    width, height = image_size(result.outcome.data)
    assert width == 1280
    assert height >= 1500
    (pixel,) = await pixels_at(
        await browsers("chrome"), result.outcome.data, [(40, 1200)]
    )
    assert near(pixel, (0, 255, 0)), pixel
    assert site.hits["/"] >= 1
