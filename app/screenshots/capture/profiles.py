"""Device profiles and the capture browser context.

A DeviceProfile describes how a capture context presents itself: the device
type, Playwright's context options (``is_mobile``, ``has_touch``, user agent,
locale) and an init script that evens out what the engines report.
build_profile() turns (browser, device type, browser version) into a profile:

- desktop — no device emulation (``emulation="none"``). chrome/msedge get
  their real user agent without "Headless" (Linux, matching
  navigator.platform); Firefox and WebKit keep their own.
- tablet / mobile on chrome, msedge and webkit — ``is_mobile``, touch and the
  device's user agent: Android Chrome/Edge, iPad/iPhone Safari
  (``emulation="full"``).
- tablet / mobile on Firefox — touch and the Android Firefox user agent only:
  Playwright rejects ``is_mobile`` there, so a page without a meta viewport
  keeps the desktop layout width (``emulation="partial"``).

The versions in the user agents come from ``browser.version`` — Playwright's
device descriptors lag behind the real browsers. Without a usable version a
profile sets no user agent, and a tablet/mobile one is then only ``partial``.
Every profile sets the locale (config.PROFILE_LOCALE); the device scale factor
is never set (DPR 1).

The profile init script (``/*qa:profile-identity*/``) makes
navigator.webdriver false on Firefox and WebKit (chrome/msedge get that from
the browser server's --disable-blink-features=AutomationControlled), and on an
emulated device makes navigator.platform match the user agent and
maxTouchPoints non-zero. It runs in every frame, like all init scripts.
"""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import Browser, BrowserContext, Page

from app.projects.models import normalize_browser
from app.screenshots.capture import config as cfg
from app.screenshots.capture import prepare, readiness

DeviceType = Literal["desktop", "tablet", "mobile"]
_DEVICE_TYPES: dict[str, DeviceType] = {
    "desktop": "desktop",
    "tablet": "tablet",
    "mobile": "mobile",
}
# Same thresholds as ViewportEntry.auto_detect_device_type.
_DESKTOP_MIN_WIDTH = 1024
_TABLET_MIN_WIDTH = 768

_ENGINES = ("chrome", "msedge", "firefox", "webkit")
_CHROMIUM_ENGINES = ("chrome", "msedge")
# Engines whose navigator.webdriver stays true under Playwright whatever the
# launch flags (Firefox has no pref for it any more).
_WEBDRIVER_SCRIPT_ENGINES = ("firefox", "webkit")

# The leading dotted number of a browser.version ("149.0.7827.114", "26.0").
_VERSION_RE = re.compile(r"\s*(\d+(?:\.\d+)*)")

# User agents (plan item 10). {major}: the browser's major version; WebKit's
# Safari "Version/" carries its full version.
_CHROME_DESKTOP_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
)
_CHROME_ANDROID_UA = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/{major}.0.0.0 {safari}"
)
_FIREFOX_ANDROID_UA = (
    "Mozilla/5.0 (Android {android}; {form}; rv:{major}.0) "
    "Gecko/{major}.0 Firefox/{major}.0"
)
_SAFARI_IOS_UA = (
    "Mozilla/5.0 ({device}) AppleWebKit/605.1.15 "
    "(KHTML, like Gecko) Version/{version} Mobile/15E148 Safari/604.1"
)

# Profile init script. __VALUES__: navigator properties to report (JSON);
# __TOUCH_POINTS__: maxTouchPoints for an emulated touch device whose engine
# reports none (0 = leave it).
_JS_PROFILE_IDENTITY = """
(function() { /*qa:profile-identity*/
    const define = (name, value) => {
        try {
            Object.defineProperty(Navigator.prototype, name, {
                get: () => value, configurable: true, enumerable: true,
            });
        } catch (e) {}
    };
    const values = __VALUES__;
    for (const name of Object.keys(values)) {
        define(name, values[name]);
    }
    const touchPoints = __TOUCH_POINTS__;
    try {
        if (touchPoints && !(navigator.maxTouchPoints > 0)) {
            define('maxTouchPoints', touchPoints);
        }
    } catch (e) {}
})();
"""


@dataclass(frozen=True)
class DeviceProfile:
    """How a capture context presents itself; build one with build_profile.

    The defaults describe a plain desktop context with no overrides at all.
    """

    device_type: DeviceType = "desktop"
    is_mobile: bool = False
    has_touch: bool = False
    user_agent: str | None = None
    locale: str | None = None
    emulation: str = "none"  # "none" | "partial" | "full"
    init_script: str = ""  # appended to the capture init script

    def context_options(self) -> dict[str, Any]:
        """browser.new_context() options this profile adds — only the ones
        that differ from Playwright's defaults."""
        options: dict[str, Any] = {}
        if self.is_mobile:
            options["is_mobile"] = True
        if self.has_touch:
            options["has_touch"] = True
        if self.user_agent:
            options["user_agent"] = self.user_agent
        if self.locale:
            options["locale"] = self.locale
        return options

    def meta(self) -> dict[str, Any]:
        """The ``device`` section of capture_meta."""
        return {
            "type": self.device_type,
            "emulation": self.emulation,
            "is_mobile": self.is_mobile,
            "has_touch": self.has_touch,
            "user_agent": self.user_agent,
            "locale": self.locale,
        }


def resolve_device_type(
    viewport: Mapping[str, Any], explicit: str | None = None
) -> DeviceType:
    """*explicit* when it is a known device type, else derived from the
    viewport width like ViewportEntry: ≥1024 desktop, ≥768 tablet, else
    mobile."""
    if explicit is not None and explicit in _DEVICE_TYPES:
        return _DEVICE_TYPES[explicit]
    try:
        width = int(viewport.get("width", 1920))
    except (TypeError, ValueError):
        width = 1920
    if width >= _DESKTOP_MIN_WIDTH:
        return "desktop"
    if width >= _TABLET_MIN_WIDTH:
        return "tablet"
    return "mobile"


def build_profile(
    browser_name: str,
    device_type: str,
    *,
    browser_version: str | None = None,
) -> DeviceProfile:
    """The capture profile for *device_type* in *browser_name*.

    *browser_version* is the connected browser's ``browser.version``; without
    it the profile sets no user agent. The legacy ``chromium`` name — and any
    unknown name, which the worker also launches as chrome — gets chrome's
    profile; an unknown device type is treated as desktop.
    """
    device = _DEVICE_TYPES.get(device_type, "desktop")
    engine = _engine(browser_name)
    user_agent = _user_agent(engine, device, browser_version)
    emulated = device != "desktop"
    if not emulated:
        emulation = "none"
    elif engine == "firefox" or user_agent is None:
        emulation = "partial"
    else:
        emulation = "full"
    return DeviceProfile(
        device_type=device,
        is_mobile=emulated and engine != "firefox",
        has_touch=emulated,
        user_agent=user_agent,
        locale=cfg.PROFILE_LOCALE,
        emulation=emulation,
        init_script=_identity_script(engine, device, user_agent),
    )


def _engine(browser_name: str) -> str:
    name = normalize_browser(browser_name)
    return name if name in _ENGINES else "chrome"


def _version_parts(version: str | None) -> tuple[str, str] | None:
    """``(major, full)`` of a ``browser.version``; None when it is missing or
    does not start with a number."""
    match = _VERSION_RE.match(version or "")
    if match is None:
        return None
    full = match.group(1)
    return full.split(".", 1)[0], full


def _user_agent(engine: str, device: DeviceType, version: str | None) -> str | None:
    """The user agent for *engine* on *device*; None keeps the browser's own."""
    parts = _version_parts(version)
    if parts is None:
        return None
    major, full = parts
    if engine in _CHROMIUM_ENGINES:
        if device == "desktop":
            user_agent = _CHROME_DESKTOP_UA.format(major=major)
            edge = f" Edg/{major}.0.0.0"
        else:
            safari = "Mobile Safari/537.36" if device == "mobile" else "Safari/537.36"
            user_agent = _CHROME_ANDROID_UA.format(major=major, safari=safari)
            edge = f" EdgA/{major}.0.0.0"
        return user_agent + edge if engine == "msedge" else user_agent
    if device == "desktop":
        return None
    if engine == "firefox":
        return _FIREFOX_ANDROID_UA.format(
            android=cfg.FIREFOX_ANDROID_VERSION,
            form="Mobile" if device == "mobile" else "Tablet",
            major=major,
        )
    os_part = (
        f"iPhone; CPU iPhone OS {cfg.IOS_VERSION} like Mac OS X"
        if device == "mobile"
        else f"iPad; CPU OS {cfg.IOS_VERSION} like Mac OS X"
    )
    return _SAFARI_IOS_UA.format(device=os_part, version=full)


def _identity_script(engine: str, device: DeviceType, user_agent: str | None) -> str:
    """The profile's init script, or "" when it has nothing to change."""
    values: dict[str, Any] = {}
    if engine in _WEBDRIVER_SCRIPT_ENGINES:
        values["webdriver"] = False
    if user_agent is not None and device != "desktop":
        if engine == "webkit":
            values["platform"] = "iPhone" if device == "mobile" else "iPad"
        else:
            values["platform"] = cfg.ANDROID_NAVIGATOR_PLATFORM
    touch_points = int(cfg.EMULATED_TOUCH_POINTS) if device != "desktop" else 0
    if not values and not touch_points:
        return ""
    return _JS_PROFILE_IDENTITY.replace("__VALUES__", json.dumps(values)).replace(
        "__TOUCH_POINTS__", str(touch_points)
    )


def capture_init_script(profile: DeviceProfile) -> str:
    """The one init script of a capture context: the IntersectionObserver
    override, the activity tracker and the profile's own script."""
    parts = (
        prepare.JS_IO_OVERRIDE,
        readiness.JS_ACTIVITY_TRACKER,
        profile.init_script,
    )
    return "\n".join(part for part in parts if part)


async def open_capture_context(
    browser: Browser,
    viewport: Mapping[str, int],
    profile: DeviceProfile | None = None,
) -> BrowserContext:
    """New isolated browser context with the capture defaults: *viewport*,
    reduced motion, the profile's options and the capture init script."""
    if profile is None:
        profile = DeviceProfile()
    context = await browser.new_context(
        viewport={"width": viewport["width"], "height": viewport["height"]},
        reduced_motion="reduce",
        **profile.context_options(),
    )
    try:
        await context.add_init_script(capture_init_script(profile))
    except BaseException:
        await context.close()
        raise
    return context


async def prepare_page_identity(page: Page, profile: DeviceProfile) -> None:
    """Per-page identity overrides for *profile*, before navigation.

    Nothing to do: the context options and the profile init script cover every
    page of the context. Kept as the per-page hook of the pipeline.
    """
    return None
