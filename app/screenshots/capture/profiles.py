"""Device profiles and the capture browser context.

A DeviceProfile describes how a capture context presents itself (device type,
emulation options, user agent). For now every profile keeps the desktop
behavior: build_profile records the device type but emulates nothing, so
context_options() is empty and contexts are created exactly as before.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

from playwright.async_api import Browser, BrowserContext, Page

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


@dataclass(frozen=True)
class DeviceProfile:
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

    Device emulation is not implemented yet: every device type captures like
    a desktop (no emulation options, no user agent override).
    """
    return DeviceProfile(device_type=_DEVICE_TYPES.get(device_type, "desktop"))


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
    """Per-page identity overrides for *profile*, before navigation. Nothing
    to do yet."""
    return None
