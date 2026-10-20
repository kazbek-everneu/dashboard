"""Device identity on the real browsers (plan item 10, check Q7): what a page —
and an iframe in it — reads from navigator and matchMedia under the capture
profile of each engine x device.

chrome/msedge report navigator.webdriver = false only when the browser server
launches them with --disable-blink-features=AutomationControlled (the
everneu-screenshots-browsers change that came with these profiles). Their
webdriver test reads chrome://version and is skipped against a server without
that flag, e.g. the dev browser before it is rebuilt.
"""

from typing import Any

import pytest
from playwright.async_api import Browser

from app.screenshots.capture import profiles
from tests.browser.helpers import LocalSite

ALL_ENGINES = ("chrome", "msedge", "firefox", "webkit")
VIEWPORTS = {
    "desktop": {"width": 1920, "height": 1080},
    "mobile": {"width": 390, "height": 844},
}
META_VIEWPORT = '<meta name="viewport" content="width=device-width,initial-scale=1">'
PAGE = """<!doctype html><html><head><meta charset="utf-8">{meta}
<title>identity</title></head><body><p>identity</p>{frame}</body></html>"""
IDENTITY = """() => ({
    userAgent: navigator.userAgent,
    webdriver: navigator.webdriver,
    platform: navigator.platform,
    maxTouchPoints: navigator.maxTouchPoints,
    coarsePointer: matchMedia('(pointer: coarse)').matches,
    language: navigator.language,
    innerWidth: window.innerWidth,
})"""
MOBILE_UA_TOKENS = {
    "chrome": ("Linux; Android 10; K", "Mobile Safari/537.36"),
    "msedge": ("Linux; Android 10; K", "Mobile Safari/537.36", "EdgA/"),
    "firefox": ("Android 14; Mobile;", "Firefox/"),
    "webkit": ("iPhone; CPU iPhone OS", "Mobile/15E148"),
}
MOBILE_PLATFORM = {
    "chrome": "Linux armv8l",
    "msedge": "Linux armv8l",
    "firefox": "Linux armv8l",
    "webkit": "iPhone",
}
AUTOMATION_FLAG = "--disable-blink-features=AutomationControlled"


async def _read_identity(
    browser: Browser, site: LocalSite, engine: str, device: str
) -> dict[str, Any]:
    """Open the capture context of *engine* x *device* and read the identity
    of a page with a meta viewport, of its iframe, and the layout width of a
    page without one."""
    site.add("/meta", PAGE.format(meta=META_VIEWPORT, frame='<iframe src="/frame">'))
    site.add("/frame", PAGE.format(meta="", frame=""))
    site.add("/plain", PAGE.format(meta="", frame=""))
    viewport = VIEWPORTS[device]
    profile = profiles.build_profile(engine, device, browser_version=browser.version)
    context = await profiles.open_capture_context(browser, viewport, profile)
    try:
        page = await context.new_page()
        await page.goto(site.url("/meta"), wait_until="load")
        top = await page.evaluate(IDENTITY)
        (frame,) = page.main_frame.child_frames
        inner = await frame.evaluate(IDENTITY)
        await page.goto(site.url("/plain"), wait_until="load")
        no_meta_width = await page.evaluate("() => window.innerWidth")
    finally:
        await context.close()
    return {"profile": profile, "top": top, "frame": inner, "no_meta": no_meta_width}


@pytest.mark.parametrize("device", ["desktop", "mobile"])
@pytest.mark.parametrize("engine", ALL_ENGINES)
async def test_page_sees_the_profile_identity(engine, device, browsers, site) -> None:
    seen = await _read_identity(await browsers(engine), site, engine, device)
    top, frame = seen["top"], seen["frame"]

    assert "Headless" not in top["userAgent"]
    if seen["profile"].user_agent is not None:
        assert top["userAgent"] == seen["profile"].user_agent
    assert top["language"] == frame["language"] == "en-US"
    if engine in ("firefox", "webkit"):
        # Hidden by the profile init script, in every frame.
        assert top["webdriver"] is False
        assert frame["webdriver"] is False

    if device == "desktop":
        assert top["innerWidth"] == seen["no_meta"] == 1920
        assert top["maxTouchPoints"] == 0
        assert top["coarsePointer"] is False
        return

    for token in MOBILE_UA_TOKENS[engine]:
        assert token in top["userAgent"], token
    assert top["maxTouchPoints"] > 0
    assert frame["maxTouchPoints"] > 0
    assert top["coarsePointer"] is True
    assert top["platform"] == frame["platform"] == MOBILE_PLATFORM[engine]
    assert top["innerWidth"] == 390
    # Without a meta viewport is_mobile lays the page out 980 px wide; Firefox
    # has no is_mobile (partial emulation) and keeps the viewport width.
    assert seen["no_meta"] == (390 if engine == "firefox" else 980)


async def _launch_flags(browser: Browser) -> str:
    """The browser's command line, from chrome://version."""
    context = await browser.new_context()
    try:
        page = await context.new_page()
        await page.goto("chrome://version")
        return await page.evaluate(
            "() => (document.querySelector('#command_line') || document.body).innerText"
        )
    finally:
        await context.close()


@pytest.mark.parametrize("device", ["desktop", "mobile"])
@pytest.mark.parametrize("engine", ["chrome", "msedge"])
async def test_chromium_hides_webdriver_with_the_server_flag(
    engine, device, browsers, site
) -> None:
    browser = await browsers(engine)
    if AUTOMATION_FLAG not in await _launch_flags(browser):
        pytest.skip(
            f"the browser server launches {engine} without {AUTOMATION_FLAG} "
            "(an everneu-screenshots-browsers build older than the device "
            "profiles), so navigator.webdriver stays true"
        )

    seen = await _read_identity(browser, site, engine, device)

    assert seen["top"]["webdriver"] is False
    assert seen["frame"]["webdriver"] is False
