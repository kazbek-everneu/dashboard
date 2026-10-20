import pytest

from app.projects.models import ViewportEntry
from app.screenshots import service
from app.screenshots.capture import prepare, profiles
from app.screenshots.capture.profiles import DeviceProfile
from tests.capture.fakes import FakeBrowser, FakePage

VERSIONS = {
    "chrome": "149.0.7827.114",
    "msedge": "149.0.4022.69",
    "firefox": "146.0.1",
    "webkit": "26.0",
}
CHROME_DESKTOP = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
CHROME_TABLET = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Safari/537.36"
)
CHROME_MOBILE = (
    "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/149.0.0.0 Mobile Safari/537.36"
)
# (user agent, is_mobile, emulation, navigator.platform the init script sets)
EXPECTED = {
    ("chrome", "desktop"): (CHROME_DESKTOP, False, "none", None),
    ("chrome", "tablet"): (CHROME_TABLET, True, "full", "Linux armv8l"),
    ("chrome", "mobile"): (CHROME_MOBILE, True, "full", "Linux armv8l"),
    ("msedge", "desktop"): (CHROME_DESKTOP + " Edg/149.0.0.0", False, "none", None),
    ("msedge", "tablet"): (
        CHROME_TABLET + " EdgA/149.0.0.0",
        True,
        "full",
        "Linux armv8l",
    ),
    ("msedge", "mobile"): (
        CHROME_MOBILE + " EdgA/149.0.0.0",
        True,
        "full",
        "Linux armv8l",
    ),
    ("firefox", "desktop"): (None, False, "none", None),
    ("firefox", "tablet"): (
        "Mozilla/5.0 (Android 14; Tablet; rv:146.0) Gecko/146.0 Firefox/146.0",
        False,
        "partial",
        "Linux armv8l",
    ),
    ("firefox", "mobile"): (
        "Mozilla/5.0 (Android 14; Mobile; rv:146.0) Gecko/146.0 Firefox/146.0",
        False,
        "partial",
        "Linux armv8l",
    ),
    ("webkit", "desktop"): (None, False, "none", None),
    ("webkit", "tablet"): (
        "Mozilla/5.0 (iPad; CPU OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1",
        True,
        "full",
        "iPad",
    ),
    ("webkit", "mobile"): (
        "Mozilla/5.0 (iPhone; CPU iPhone OS 18_5 like Mac OS X) AppleWebKit/605.1.15 "
        "(KHTML, like Gecko) Version/26.0 Mobile/15E148 Safari/604.1",
        True,
        "full",
        "iPhone",
    ),
}
IDENTITY_MARKER = "/*qa:profile-identity*/"
HIDE_WEBDRIVER = '"webdriver": false'
TOUCH_POINTS = "const touchPoints = 5;"


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


@pytest.mark.parametrize(("browser_name", "device_type"), sorted(EXPECTED))
def test_build_profile_matrix(browser_name: str, device_type: str) -> None:
    user_agent, is_mobile, emulation, platform = EXPECTED[browser_name, device_type]

    profile = profiles.build_profile(
        browser_name, device_type, browser_version=VERSIONS[browser_name]
    )

    touch = device_type != "desktop"
    assert profile.device_type == device_type
    assert profile.user_agent == user_agent
    assert profile.is_mobile is is_mobile
    assert profile.has_touch is touch
    assert profile.emulation == emulation
    assert profile.locale == "en-US"
    expected_options = {"locale": "en-US"}
    if is_mobile:
        expected_options["is_mobile"] = True
    if touch:
        expected_options["has_touch"] = True
    if user_agent:
        expected_options["user_agent"] = user_agent
    # DPR stays 1: the profile never sets device_scale_factor.
    assert profile.context_options() == expected_options
    assert "Headless" not in (profile.user_agent or "")

    script = profile.init_script
    if browser_name in ("chrome", "msedge") and device_type == "desktop":
        assert script == ""
        return
    assert IDENTITY_MARKER in script
    # navigator.webdriver: hidden by the script on Firefox/WebKit only (chrome
    # and msedge get it from the browser server's launch flags).
    assert (HIDE_WEBDRIVER in script) is (browser_name in ("firefox", "webkit"))
    assert (TOUCH_POINTS in script) is touch
    if platform is None:
        assert '"platform"' not in script
    else:
        assert f'"platform": "{platform}"' in script


@pytest.mark.parametrize("version", [None, "", "unknown", "  "])
@pytest.mark.parametrize("browser_name", ["chrome", "msedge", "firefox", "webkit"])
def test_build_profile_without_a_usable_version_sets_no_user_agent(
    browser_name: str, version: str | None
) -> None:
    desktop = profiles.build_profile(browser_name, "desktop", browser_version=version)
    mobile = profiles.build_profile(browser_name, "mobile", browser_version=version)

    assert desktop.user_agent is None and desktop.emulation == "none"
    assert mobile.user_agent is None
    # Still a touch device (and is_mobile where supported), but only partly
    # emulated — and navigator.platform is left matching the browser's own UA.
    assert mobile.has_touch is True
    assert mobile.is_mobile is (browser_name != "firefox")
    assert mobile.emulation == "partial"
    assert mobile.locale == "en-US"
    assert '"platform"' not in mobile.init_script
    assert TOUCH_POINTS in mobile.init_script


@pytest.mark.parametrize(
    ("browser_name", "version", "token"),
    [
        ("chrome", "149", "Chrome/149.0.0.0"),
        ("chrome", " 150.0.1-beta", "Chrome/150.0.0.0"),
        ("msedge", "149.0.4022.69", "EdgA/149.0.0.0"),
        ("firefox", "146.0.1", "rv:146.0) Gecko/146.0 Firefox/146.0"),
        ("webkit", "26.0", "Version/26.0 Mobile/15E148"),
        ("webkit", "18.4.1", "Version/18.4.1 Mobile/15E148"),
    ],
)
def test_build_profile_reads_the_version(
    browser_name: str, version: str, token: str
) -> None:
    profile = profiles.build_profile(browser_name, "mobile", browser_version=version)

    assert profile.user_agent is not None and token in profile.user_agent


@pytest.mark.parametrize("device_type", ["desktop", "tablet", "mobile"])
def test_legacy_and_unknown_browser_names_get_chromes_profile(
    device_type: str,
) -> None:
    chrome = profiles.build_profile(
        "chrome", device_type, browser_version=VERSIONS["chrome"]
    )

    for name in ("chromium", "opera"):
        assert (
            profiles.build_profile(
                name, device_type, browser_version=VERSIONS["chrome"]
            )
            == chrome
        )


def test_build_profile_falls_back_to_desktop_for_unknown_types() -> None:
    profile = profiles.build_profile("chrome", "phone", browser_version="149.0")

    assert profile.device_type == "desktop"
    assert profile.emulation == "none"
    assert profile.user_agent == CHROME_DESKTOP


def test_profile_init_script_follows_config(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.screenshots.capture.config.ANDROID_NAVIGATOR_PLATFORM", "Linux aarch64"
    )
    monkeypatch.setattr("app.screenshots.capture.config.EMULATED_TOUCH_POINTS", 10)
    monkeypatch.setattr("app.screenshots.capture.config.PROFILE_LOCALE", "en-GB")

    profile = profiles.build_profile("firefox", "mobile", browser_version="146.0")

    assert '"platform": "Linux aarch64"' in profile.init_script
    assert "const touchPoints = 10;" in profile.init_script
    assert profile.locale == "en-GB"


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
        "locale": "en-US",
    }


def test_capture_init_script_ends_with_the_profile_script() -> None:
    profile = profiles.build_profile("webkit", "mobile", browser_version="26.0")

    script = profiles.capture_init_script(profile)

    assert script.startswith(prepare.JS_IO_OVERRIDE)
    assert script.endswith(profile.init_script)
    assert script.index("/*qa:io-override*/") < script.index(IDENTITY_MARKER)


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


async def test_open_capture_context_applies_a_built_profile() -> None:
    browser = FakeBrowser()
    profile = profiles.build_profile(
        "chrome", "mobile", browser_version=VERSIONS["chrome"]
    )

    context = await profiles.open_capture_context(
        browser, {"width": 390, "height": 844}, profile
    )

    assert browser.context_kwargs == [
        {
            "viewport": {"width": 390, "height": 844},
            "reduced_motion": "reduce",
            "is_mobile": True,
            "has_touch": True,
            "user_agent": CHROME_MOBILE,
            "locale": "en-US",
        }
    ]
    # FakeContext.add_init_script is positional-only: one script, passed so.
    assert context.init_scripts == [profiles.capture_init_script(profile)]
    assert IDENTITY_MARKER in context.init_scripts[0]


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
    profile = profiles.build_profile("firefox", "desktop", browser_version="146.0.1")

    context = await service.open_capture_context(
        browser, {"width": 1280, "height": 720}, profile
    )

    assert browser.context_kwargs == [
        {
            "viewport": {"width": 1280, "height": 720},
            "reduced_motion": "reduce",
            "locale": "en-US",
        }
    ]
    assert context.init_scripts == [profiles.capture_init_script(profile)]


async def test_prepare_page_identity_does_nothing() -> None:
    page = FakePage()
    profile = profiles.build_profile("chrome", "mobile", browser_version="149.0")

    await profiles.prepare_page_identity(page, profile)

    assert page.events == []
