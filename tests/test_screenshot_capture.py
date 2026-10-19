import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.models import Screenshot
from app.screenshots.service import (
    _JS_HIDE_OVERLAYS,
    _JS_NORMALIZE_FIXED_BACKGROUNDS,
    EffectiveConfig,
    PageStabilizationTimeoutError,
    _do_capture,
    _navigation_wait_until_chain,
    capture_screenshot,
)


class _FakePage:
    def __init__(
        self,
        *,
        asset_state: dict | None = None,
        stability_state: dict | None = None,
        failed_requests: list[object] | None = None,
        bad_responses: list[object] | None = None,
    ) -> None:
        self.screenshot_kwargs: dict | None = None
        self.scroll_height = 0
        self.evaluated_scripts: list[object] = []
        self.events: list[tuple[str, object]] = []
        self.asset_state = asset_state
        self.stability_state = stability_state
        self.failed_requests = failed_requests or []
        self.bad_responses = bad_responses or []
        self.handlers: dict[str, list[object]] = {}
        self.goto_calls: list[tuple[tuple[object, ...], dict[str, object]]] = []

    def on(self, event: str, handler: object) -> None:
        self.handlers.setdefault(event, []).append(handler)

    async def goto(self, *args, **kwargs) -> None:
        self.goto_calls.append((args, kwargs))
        for request in self.failed_requests:
            for handler in self.handlers.get("requestfailed", []):
                handler(request)
        for response in self.bad_responses:
            for handler in self.handlers.get("response", []):
                handler(response)
        return None

    async def wait_for_load_state(self, *_args, **_kwargs) -> None:
        return None

    async def evaluate(self, script, *_args, **_kwargs):
        self.evaluated_scripts.append(script)
        self.events.append(("evaluate", script))
        if script == "document.body.scrollHeight":
            return self.scroll_height
        if "visibleBackgroundCount" in str(script) and "missingSamples" in str(script):
            return self.asset_state or {
                "visibleImageCount": 0,
                "visibleBackgroundCount": 0,
                "missingVisibleImageCount": 0,
                "missingBackgroundImageCount": 0,
                "hiddenPendingImageCount": 0,
                "missingSamples": [],
            }
        if "readyState:" in str(script) and "pendingLazyRender" in str(script):
            return self.stability_state or {
                "readyState": "complete",
                "scrollHeight": self.scroll_height,
                "pendingImages": 0,
                "pendingVisibleImages": 0,
                "hiddenPendingImages": 0,
                "pendingLazyRender": 0,
                "textLength": 42,
            }
        return None

    async def wait_for_timeout(self, *_args, **_kwargs) -> None:
        return None

    async def screenshot(self, **kwargs) -> bytes:
        self.screenshot_kwargs = kwargs
        self.events.append(("screenshot", kwargs))
        return b"img"


class _TimeoutOnWaitUntilPage(_FakePage):
    """Fake page whose goto times out for specific wait_until values.

    Simulates the real-world failure mode: a heavier navigation condition
    (e.g. ``load`` blocked on a slow third-party subresource) times out while a
    lighter one (``domcontentloaded``) succeeds.
    """

    def __init__(self, *, timeout_levels: set[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.timeout_levels = timeout_levels

    async def goto(self, *args, **kwargs) -> None:
        self.goto_calls.append((args, kwargs))
        if kwargs.get("wait_until") in self.timeout_levels:
            raise PlaywrightTimeoutError("Timeout 1000ms exceeded")
        return None


class _FakeContext:
    def __init__(self, page: _FakePage) -> None:
        self.page = page

    async def add_init_script(self, *_args, **_kwargs) -> None:
        return None

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self, page: _FakePage) -> None:
        self.page = page

    async def new_context(self, **_kwargs) -> _FakeContext:
        return _FakeContext(self.page)


class _FakeRequest:
    def __init__(
        self,
        *,
        resource_type: str = "image",
        url: str = "https://cdn.example.com/missing.jpg",
        failure: str = "net::ERR_FAILED",
    ) -> None:
        self.resource_type = resource_type
        self.url = url
        self.failure = failure


class _FakeResponse:
    def __init__(
        self,
        *,
        resource_type: str = "image",
        status: int = 403,
        url: str = "https://cdn.example.com/blocked.jpg",
    ) -> None:
        self.request = _FakeRequest(resource_type=resource_type, url=url)
        self.status = status
        self.url = url


class _FakeSession:
    def __init__(self) -> None:
        self.record: Screenshot | None = None

    def add(self, record: Screenshot) -> None:
        self.record = record

    async def commit(self) -> None:
        return None

    async def refresh(self, record: Screenshot) -> None:
        return None


def test_hide_overlays_does_not_match_generic_overlay_selectors() -> None:
    assert '[class*="overlay"]' not in _JS_HIDE_OVERLAYS
    assert '[id*="overlay"]' not in _JS_HIDE_OVERLAYS
    assert '[class*="popup"]' in _JS_HIDE_OVERLAYS
    assert '[class*="modal"]' in _JS_HIDE_OVERLAYS
    assert '[class*="banner"]' in _JS_HIDE_OVERLAYS


def test_normalize_fixed_backgrounds_script_targets_background_attachment() -> None:
    assert "backgroundAttachment" in _JS_NORMALIZE_FIXED_BACKGROUNDS
    assert "background-attachment" in _JS_NORMALIZE_FIXED_BACKGROUNDS
    assert "value.toLowerCase() === 'fixed'" in _JS_NORMALIZE_FIXED_BACKGROUNDS
    assert "return 'local';" in _JS_NORMALIZE_FIXED_BACKGROUNDS


async def test_do_capture_always_uses_full_page() -> None:
    page = _FakePage()
    browser = _FakeBrowser(page)
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    outcome = await _do_capture(
        browser,
        "https://example.com/",
        {"width": 1280, "height": 720},
        config,
    )

    assert outcome.data == b"img"
    assert outcome.duration >= 0
    assert outcome.captured_with_unstable_dom is False
    assert page.screenshot_kwargs is not None
    assert page.goto_calls[0][1]["wait_until"] == "load"
    assert page.screenshot_kwargs["full_page"] is True
    assert page.screenshot_kwargs["type"] == "jpeg"
    assert page.screenshot_kwargs["timeout"] == 1000
    assert page.screenshot_kwargs["quality"] == 85
    assert any("SELECTORS" in str(script) for script in page.evaluated_scripts)
    assert any("document.fonts" in str(script) for script in page.evaluated_scripts)
    assert any(
        'data-wpr-lazyrender="1"' in str(script) for script in page.evaluated_scripts
    )
    assert any(
        "backgroundAttachment" in str(script) for script in page.evaluated_scripts
    )

    screenshot_index = next(
        index
        for index, (kind, _payload) in enumerate(page.events)
        if kind == "screenshot"
    )
    normalize_indices = [
        index
        for index, (kind, payload) in enumerate(page.events)
        if kind == "evaluate" and payload == _JS_NORMALIZE_FIXED_BACKGROUNDS
    ]
    assert normalize_indices
    assert normalize_indices[-1] < screenshot_index


async def test_do_capture_uses_domcontentloaded_for_webkit_load_config() -> None:
    page = _FakePage(
        stability_state={
            "readyState": "interactive",
            "scrollHeight": 900,
            "pendingImages": 0,
            "pendingVisibleImages": 0,
            "hiddenPendingImages": 0,
            "pendingLazyRender": 0,
            "textLength": 42,
        },
    )
    browser = _FakeBrowser(page)
    config = EffectiveConfig(
        browser="webkit",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    outcome = await _do_capture(
        browser,
        "https://example.com/",
        {"width": 1280, "height": 720},
        config,
    )

    assert outcome.data == b"img"
    assert page.goto_calls[0][1]["wait_until"] == "domcontentloaded"
    assert page.screenshot_kwargs is not None


async def test_do_capture_ignores_hidden_pending_images() -> None:
    page = _FakePage(
        asset_state={
            "visibleImageCount": 0,
            "visibleBackgroundCount": 0,
            "missingVisibleImageCount": 0,
            "missingBackgroundImageCount": 0,
            "hiddenPendingImageCount": 6,
            "missingSamples": [],
        },
        stability_state={
            "readyState": "complete",
            "scrollHeight": 900,
            "pendingImages": 6,
            "pendingVisibleImages": 0,
            "hiddenPendingImages": 6,
            "pendingLazyRender": 0,
            "textLength": 42,
        },
    )
    browser = _FakeBrowser(page)
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    outcome = await _do_capture(
        browser,
        "https://example.com/",
        {"width": 1280, "height": 720},
        config,
    )

    assert outcome.data == b"img"
    assert outcome.captured_with_unstable_dom is False


async def test_do_capture_waits_for_visible_background_images() -> None:
    page = _FakePage(
        asset_state={
            "visibleImageCount": 0,
            "visibleBackgroundCount": 1,
            "missingVisibleImageCount": 0,
            "missingBackgroundImageCount": 0,
            "hiddenPendingImageCount": 0,
            "missingSamples": [],
        },
        stability_state={
            "readyState": "complete",
            "scrollHeight": 900,
            "pendingImages": 0,
            "pendingVisibleImages": 0,
            "hiddenPendingImages": 0,
            "pendingLazyRender": 0,
            "textLength": 0,
        },
    )
    browser = _FakeBrowser(page)
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    outcome = await _do_capture(
        browser,
        "https://example.com/",
        {"width": 1280, "height": 720},
        config,
    )

    assert outcome.data == b"img"
    assert any(
        "backgroundImage" in str(script) and "visibleBackgroundCount" in str(script)
        for script in page.evaluated_scripts
    )


async def test_capture_screenshot_fails_after_visible_image_asset_retry(
    tmp_path: Path,
) -> None:
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session = _FakeSession()
    page = _FakePage(
        asset_state={
            "visibleImageCount": 1,
            "visibleBackgroundCount": 0,
            "missingVisibleImageCount": 1,
            "missingBackgroundImageCount": 0,
            "hiddenPendingImageCount": 2,
            "missingSamples": [
                {"kind": "img", "url": "https://cdn.example.com/missing.jpg"}
            ],
        },
    )
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    relative_path = f"{project_id}/{task_id}/example.jpg"
    output_path = tmp_path / "example.jpg"

    with (
        patch("app.screenshots.service.build_file_path", return_value=relative_path),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(page),
            session=session,
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=config,
        )

    assert result.status == "failed"
    assert result.captured_with_unstable_dom is False
    assert result.error_message is not None
    assert "Visible page assets did not load" in result.error_message
    assert "https://cdn.example.com/missing.jpg" in result.error_message
    assert not output_path.exists()


async def test_capture_screenshot_includes_network_diagnostics(
    tmp_path: Path,
) -> None:
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session = _FakeSession()
    page = _FakePage(
        asset_state={
            "visibleImageCount": 1,
            "visibleBackgroundCount": 0,
            "missingVisibleImageCount": 1,
            "missingBackgroundImageCount": 0,
            "hiddenPendingImageCount": 0,
            "missingSamples": [
                {"kind": "img", "url": "https://cdn.example.com/missing.jpg"}
            ],
        },
        failed_requests=[
            _FakeRequest(
                resource_type="image",
                url="https://cdn.example.com/missing.jpg",
                failure="net::ERR_CONNECTION_RESET",
            )
        ],
        bad_responses=[
            _FakeResponse(
                resource_type="image",
                status=403,
                url="https://cdn.example.com/blocked.jpg",
            )
        ],
    )
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    relative_path = f"{project_id}/{task_id}/example.jpg"
    output_path = tmp_path / "example.jpg"

    with (
        patch("app.screenshots.service.build_file_path", return_value=relative_path),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(page),
            session=session,
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=config,
        )

    assert result.status == "failed"
    assert result.error_message is not None
    assert "Network diagnostics" in result.error_message
    assert "net::ERR_CONNECTION_RESET" in result.error_message
    assert "HTTP 403" in result.error_message


async def test_capture_screenshot_does_not_save_blank_assetless_page(
    tmp_path: Path,
) -> None:
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session = _FakeSession()
    page = _FakePage(
        asset_state={
            "visibleImageCount": 0,
            "visibleBackgroundCount": 0,
            "missingVisibleImageCount": 0,
            "missingBackgroundImageCount": 0,
            "hiddenPendingImageCount": 0,
            "missingSamples": [],
        },
        stability_state={
            "readyState": "complete",
            "scrollHeight": 720,
            "pendingImages": 0,
            "pendingVisibleImages": 0,
            "hiddenPendingImages": 0,
            "pendingLazyRender": 0,
            "textLength": 0,
        },
    )
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    relative_path = f"{project_id}/{task_id}/example.jpg"
    output_path = tmp_path / "example.jpg"

    with (
        patch("app.screenshots.service.build_file_path", return_value=relative_path),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(page),
            session=session,
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=config,
        )

    assert result.status == "failed"
    assert result.error_message is not None
    assert "Page rendered blank" in result.error_message
    assert not output_path.exists()


async def test_capture_screenshot_retries_after_stabilization_timeout(
    tmp_path: Path,
) -> None:
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session = _FakeSession()
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    failing_once = AsyncMock(
        side_effect=[
            PageStabilizationTimeoutError("not stable yet"),
            type(
                "Outcome",
                (),
                {
                    "data": b"img",
                    "duration": 0.25,
                    "captured_with_unstable_dom": True,
                    "warning_message": (
                        "Captured with unstable DOM after stabilization timeout. "
                        "Content may be incomplete or still settling."
                    ),
                },
            )(),
        ]
    )

    relative_path = f"{project_id}/{task_id}/example.jpg"
    output_path = tmp_path / "example.jpg"

    with (
        patch("app.screenshots.service._do_capture", failing_once),
        patch("app.screenshots.service.build_file_path", return_value=relative_path),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.ensure_parent"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(_FakePage()),
            session=session,
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=config,
        )

    assert failing_once.await_count == 2
    assert result.status == "ok"
    assert result.captured_with_unstable_dom is True
    assert result.error_message is not None
    assert session.record is not None
    assert output_path.read_bytes() == b"img"


def test_navigation_wait_until_chain_cascades_to_domcontentloaded() -> None:
    def cfg(browser: str, wait_until: str) -> EffectiveConfig:
        return EffectiveConfig(
            browser=browser,
            fmt="jpeg",
            quality=85,
            wait_until=wait_until,
            timeout=1000,
        )

    assert _navigation_wait_until_chain(cfg("chromium", "networkidle")) == [
        "networkidle",
        "load",
        "domcontentloaded",
    ]
    assert _navigation_wait_until_chain(cfg("chromium", "load")) == [
        "load",
        "domcontentloaded",
    ]
    assert _navigation_wait_until_chain(cfg("chromium", "domcontentloaded")) == [
        "domcontentloaded",
    ]
    # WebKit downgrades `load` to `domcontentloaded`, so the chain has no
    # redundant `load` step.
    assert _navigation_wait_until_chain(cfg("webkit", "load")) == [
        "domcontentloaded",
    ]
    assert _navigation_wait_until_chain(cfg("webkit", "networkidle")) == [
        "networkidle",
        "domcontentloaded",
    ]


async def test_do_capture_falls_back_to_lighter_wait_until_on_goto_timeout() -> None:
    # `load` times out (slow third-party subresource); `domcontentloaded` works.
    page = _TimeoutOnWaitUntilPage(timeout_levels={"load"})
    browser = _FakeBrowser(page)
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    outcome = await _do_capture(
        browser,
        "https://example.com/",
        {"width": 1280, "height": 720},
        config,
    )

    assert outcome.data == b"img"
    # Tried `load` first, then fell back to `domcontentloaded`.
    attempted = [call[1].get("wait_until") for call in page.goto_calls]
    assert attempted == ["load", "domcontentloaded"]


async def test_do_capture_raises_when_all_navigation_levels_time_out() -> None:
    page = _TimeoutOnWaitUntilPage(
        timeout_levels={"networkidle", "load", "domcontentloaded"}
    )
    browser = _FakeBrowser(page)
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="networkidle",
        timeout=1000,
    )

    with pytest.raises(PlaywrightTimeoutError):
        await _do_capture(
            browser,
            "https://example.com/",
            {"width": 1280, "height": 720},
            config,
        )

    # Exhausted the full cascade before giving up.
    attempted = [call[1].get("wait_until") for call in page.goto_calls]
    assert attempted == ["networkidle", "load", "domcontentloaded"]


async def test_capture_screenshot_recovers_when_load_event_times_out(
    tmp_path: Path,
) -> None:
    # End-to-end: a page whose `load` event never fires (third-party stall) must
    # still produce an `ok` screenshot via the domcontentloaded fallback rather
    # than a timeout `failed` row.
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    session = _FakeSession()
    page = _TimeoutOnWaitUntilPage(timeout_levels={"load"})
    config = EffectiveConfig(
        browser="chromium",
        fmt="jpeg",
        quality=85,
        wait_until="load",
        timeout=1000,
    )

    relative_path = f"{project_id}/{task_id}/example.jpg"
    output_path = tmp_path / "example.jpg"

    with (
        patch("app.screenshots.service.build_file_path", return_value=relative_path),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.ensure_parent"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(page),
            session=session,
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=config,
        )

    assert result.status == "ok"
    assert output_path.read_bytes() == b"img"
