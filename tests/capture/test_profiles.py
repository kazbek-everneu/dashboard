import pytest

from app.projects.models import ViewportEntry
from app.screenshots import service
from app.screenshots.capture import prepare, profiles
from app.screenshots.capture.profiles import DeviceProfile
from tests.capture.fakes import FakeBrowser, FakePage


@pytest.mark.parametrize(
    ("width", "expected"),
    [
        (1920, "desktop"),
        (1024, "desktop"),
        (1023, "tablet"),
        (834, "tablet"),
        (768, "tablet"),
        (767, "mobile"),
        (390, "mobile"),
    ],
)
def test_resolve_device_type_by_width(width: int, expected: str) -> None:
    assert profiles.resolve_device_type({"width": width, "height": 800}) == expected


def test_resolve_device_type_matches_viewport_entry() -> None:
    for width in range(300, 2000, 7):
        entry = ViewportEntry(width=width, height=800)
        assert (
            profiles.resolve_device_type({"width": width, "height": 800})
            == entry.device_type
        ), width


def test_resolve_device_type_prefers_a_valid_explicit_type() -> None:
    mobile = {"width": 390, "height": 844}
    desktop = {"width": 1920, "height": 1080}

    assert profiles.resolve_device_type(mobile, "desktop") == "desktop"
    assert profiles.resolve_device_type(desktop, "tablet") == "tablet"
    assert profiles.resolve_device_type(desktop, "phone") == "desktop"
    assert profiles.resolve_device_type(mobile, None) == "mobile"
    assert profiles.resolve_device_type({}) == "desktop"


@pytest.mark.parametrize("browser_name", ["chrome", "msedge", "firefox", "webkit"])
@pytest.mark.parametrize("device_type", ["desktop", "tablet", "mobile"])
def test_build_profile_keeps_desktop_behavior_for_every_device(
    browser_name: str, device_type: str
) -> None:
    profile = profiles.build_profile(
        browser_name, device_type, browser_version="149.0.7800.0"
    )

    assert profile.device_type == device_type
    assert profile.context_options() == {}
    assert profile.emulation == "none"
    assert profile.init_script == ""


def test_build_profile_falls_back_to_desktop_for_unknown_types() -> None:
    assert profiles.build_profile("chrome", "phone").device_type == "desktop"


def test_context_options_lists_only_non_default_options() -> None:
    profile = DeviceProfile(
        device_type="mobile",
        is_mobile=True,
        has_touch=True,
        user_agent="Mozilla/5.0 (Linux; Android 10; K)",
        locale="en-US",
        emulation="full",
    )

    assert DeviceProfile().context_options() == {}
    assert profile.context_options() == {
        "is_mobile": True,
        "has_touch": True,
        "user_agent": "Mozilla/5.0 (Linux; Android 10; K)",
        "locale": "en-US",
    }
    assert profile.meta() == {
        "type": "mobile",
        "emulation": "full",
        "is_mobile": True,
        "has_touch": True,
        "user_agent": "Mozilla/5.0 (Linux; Android 10; K)",
    }


async def test_open_capture_context_uses_the_capture_defaults() -> None:
    browser = FakeBrowser()

    context = await profiles.open_capture_context(
        browser, {"width": 390, "height": 844}
    )

    assert browser.context_kwargs == [
        {"viewport": {"width": 390, "height": 844}, "reduced_motion": "reduce"}
    ]
    # One init script, added positionally: just the IO override for now.
    assert context.init_scripts == [prepare.JS_IO_OVERRIDE]
    assert context.closed is False


async def test_open_capture_context_applies_profile_options() -> None:
    browser = FakeBrowser()
    profile = DeviceProfile(
        device_type="mobile", has_touch=True, init_script="window.__qa = 1;"
    )

    context = await profiles.open_capture_context(
        browser, {"width": 390, "height": 844}, profile
    )

    assert browser.context_kwargs[0]["has_touch"] is True
    assert context.init_scripts == [prepare.JS_IO_OVERRIDE + "\nwindow.__qa = 1;"]


async def test_open_capture_context_closes_the_context_when_init_fails() -> None:
    browser = FakeBrowser(init_script_error=RuntimeError("context closed"))

    with pytest.raises(RuntimeError, match="context closed"):
        await profiles.open_capture_context(browser, {"width": 1280, "height": 720})

    assert len(browser.contexts) == 1
    assert browser.contexts[0].closed is True


async def test_service_open_capture_context_delegates_to_profiles() -> None:
    # The worker opens its shared per-URL contexts through the service module.
    browser = FakeBrowser()

    context = await service.open_capture_context(
        browser, {"width": 1280, "height": 720}
    )

    assert browser.context_kwargs == [
        {"viewport": {"width": 1280, "height": 720}, "reduced_motion": "reduce"}
    ]
    assert context.init_scripts == [prepare.JS_IO_OVERRIDE]


async def test_prepare_page_identity_does_nothing_yet() -> None:
    page = FakePage()

    await profiles.prepare_page_identity(page, DeviceProfile(device_type="mobile"))

    assert page.events == []
