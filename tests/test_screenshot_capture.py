import asyncio
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.models import Screenshot
from app.screenshots.service import (
    _JS_DETECT_LAZY_CONTENT,
    _JS_HIDE_OVERLAYS,
    _JS_NORMALIZE_FIXED_BACKGROUNDS,
    _JS_PAUSE_VIDEOS,
    AssetReadinessResult,
    EffectiveConfig,
    PageAssetLoadError,
    PageStabilizationTimeoutError,
    _decode_visible_images,
    _do_capture,
    _evaluate_bounded,
    _InflightTracker,
    _navigation_wait_until_chain,
    _scroll_pass,
    _stabilization_timeout_ms,
    _wait_for_fonts,
    _wait_for_quiet,
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

    async def set_viewport_size(self, size: dict) -> None:
        self.events.append(("set_viewport_size", size))

    async def close(self) -> None:
        self.events.append(("close", None))


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
        self.closed = False

    async def add_init_script(self, *_args, **_kwargs) -> None:
        return None

    async def new_page(self) -> _FakePage:
        return self.page

    async def close(self) -> None:
        self.closed = True


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


def test_hide_overlays_hides_backdrops_and_unlocks_scroll() -> None:
    assert "'.cky-overlay'" in _JS_HIDE_OVERLAYS
    assert "el === document.body" in _JS_HIDE_OVERLAYS
    assert "d.matches(':modal')" in _JS_HIDE_OVERLAYS
    assert "cs.position !== 'fixed'" in _JS_HIDE_OVERLAYS
    assert "cs.pointerEvents === 'none'" in _JS_HIDE_OVERLAYS
    assert "MIN_COVERAGE" in _JS_HIDE_OVERLAYS
    assert "data-qa-overlay-hidden" in _JS_HIDE_OVERLAYS
    assert "cs.overflowY === 'hidden'" in _JS_HIDE_OVERLAYS
    assert "'overflow-x'" not in _JS_HIDE_OVERLAYS


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


# ---------------------------------------------------------------------------
# Adaptive waits, readiness second chance, bounded evaluates
# ---------------------------------------------------------------------------


def _config(**overrides) -> EffectiveConfig:
    values = {
        "browser": "chromium",
        "fmt": "jpeg",
        "quality": 85,
        "wait_until": "load",
        "timeout": 1000,
    }
    values.update(overrides)
    return EffectiveConfig(**values)


class _RecordingPage(_FakePage):
    """Fake page that records waits and can run a hook on each poll."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self.waits: list[float] = []
        self.on_wait = None

    async def wait_for_timeout(self, timeout, *_args, **_kwargs) -> None:
        self.waits.append(timeout)
        if self.on_wait is not None:
            self.on_wait(len(self.waits))


def test_detect_lazy_content_ignores_native_lazy_images() -> None:
    # WordPress marks nearly every image loading="lazy"; _JS_TRIGGER_LAZY flips
    # those to eager, so they must not select the slow scroll profile.
    assert 'img[loading="lazy"]' not in _JS_DETECT_LAZY_CONTENT
    assert "img[data-src]" in _JS_DETECT_LAZY_CONTENT
    assert '[data-wpr-lazyrender="1"]' in _JS_DETECT_LAZY_CONTENT


def test_inflight_tracker_counts_only_tracked_request_types() -> None:
    tracker = _InflightTracker()
    image = _FakeRequest(resource_type="image")
    xhr = _FakeRequest(resource_type="xhr")
    video = _FakeRequest(resource_type="media")
    for request in (image, xhr, video):
        tracker.on_request(request)

    assert tracker.inflight() == 1
    assert tracker.inflight(include_data=True) == 2

    tracker.on_request_done(_FakeRequest())  # never seen: no-op
    assert tracker.inflight(include_data=True) == 2

    tracker.on_request_done(image)
    tracker.on_request_done(xhr)
    assert tracker.inflight(include_data=True) == 0


def test_inflight_tracker_ignores_stale_requests() -> None:
    now = [100.0]
    with patch("app.screenshots.service._clock", side_effect=lambda: now[0]):
        tracker = _InflightTracker()
        tracker.on_request(_FakeRequest(resource_type="image"))
        tracker.on_request(_FakeRequest(resource_type="fetch"))
        assert tracker.inflight(include_data=True) == 2

        now[0] += 5  # past the 3 s data limit, within the 10 s visual one
        assert tracker.inflight(include_data=True) == 1

        now[0] += 6
        assert tracker.inflight(include_data=True) == 0


async def test_wait_for_quiet_ends_once_requests_finish() -> None:
    page = _RecordingPage()
    tracker = _InflightTracker()
    request = _FakeRequest(resource_type="image")
    tracker.on_request(request)
    page.on_wait = lambda polls: polls == 5 and tracker.on_request_done(request)

    await _wait_for_quiet(page, tracker, min_ms=300, quiet_ms=200, max_ms=2000)

    # Busy for 5 polls (500 ms), then quiet for 200 ms — well under the cap.
    assert sum(page.waits) == 700
    assert all(wait <= 100 for wait in page.waits)


async def test_wait_for_quiet_stops_at_cap_when_request_never_finishes() -> None:
    page = _RecordingPage()
    tracker = _InflightTracker()
    tracker.on_request(_FakeRequest(resource_type="font"))

    await _wait_for_quiet(page, tracker, min_ms=300, quiet_ms=200, max_ms=1000)

    assert sum(page.waits) == 1000


async def test_wait_for_quiet_honours_min_wait_when_idle() -> None:
    page = _RecordingPage()

    await _wait_for_quiet(
        page, _InflightTracker(), min_ms=300, quiet_ms=200, max_ms=2000
    )

    assert sum(page.waits) == 300


async def test_wait_for_quiet_without_tracker_keeps_fixed_pause() -> None:
    page = _RecordingPage()

    await _wait_for_quiet(page, None, min_ms=300, quiet_ms=200, max_ms=2000)

    assert page.waits == [2000]


async def test_scroll_pass_waits_adaptively_per_step() -> None:
    page = _RecordingPage()
    page.scroll_height = 1000

    await _scroll_pass(
        page, 300, tracker=_InflightTracker(), min_ms=300, quiet_ms=200, max_ms=2000
    )

    scrolls = [
        script
        for script in page.evaluated_scripts
        if str(script).startswith("window.scrollTo(0, ")
    ]
    # scrollTo(0, 0) reset + positions 0, 300, 600, 900
    assert len(scrolls) == 5
    # An idle page waits only the per-step minimum, not the 2 s cap.
    assert sum(page.waits) == 4 * 300


def test_stabilization_retry_window_exceeds_base_cap() -> None:
    config = _config(timeout=30000)

    assert _stabilization_timeout_ms(config) == 25000
    assert _stabilization_timeout_ms(config, retry=True) == 37500


async def test_do_capture_rechecks_readiness_in_place_after_timeout() -> None:
    page = _FakePage()
    stability = AsyncMock(side_effect=[PageStabilizationTimeoutError("busy"), None])
    config = _config()

    with patch("app.screenshots.service._wait_for_dom_stability", stability):
        outcome = await _do_capture(
            _FakeBrowser(page),
            "https://example.com/",
            {"width": 1280, "height": 720},
            config,
        )

    assert outcome.captured_with_unstable_dom is False
    assert len(page.goto_calls) == 1
    assert stability.await_count == 2
    assert stability.await_args_list[1].args[1] == _stabilization_timeout_ms(
        config, retry=True
    )


async def test_do_capture_unstable_after_second_timeout_keeps_one_navigation() -> None:
    page = _FakePage()
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))

    with patch("app.screenshots.service._wait_for_dom_stability", stability):
        outcome = await _do_capture(
            _FakeBrowser(page),
            "https://example.com/",
            {"width": 1280, "height": 720},
            _config(),
        )

    assert outcome.captured_with_unstable_dom is True
    assert outcome.warning_message is not None
    assert len(page.goto_calls) == 1
    assert stability.await_count == 2


async def test_do_capture_retry_attempt_skips_second_chance() -> None:
    page = _FakePage()
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))

    with patch("app.screenshots.service._wait_for_dom_stability", stability):
        outcome = await _do_capture(
            _FakeBrowser(page),
            "https://example.com/",
            {"width": 1280, "height": 720},
            _config(),
            retry=True,
        )

    assert outcome.captured_with_unstable_dom is True
    assert stability.await_count == 1


async def test_do_capture_second_chance_asset_failure_keeps_diagnostics() -> None:
    page = _FakePage(
        failed_requests=[
            _FakeRequest(
                url="https://cdn.example.com/missing.jpg",
                failure="net::ERR_CONNECTION_RESET",
            )
        ]
    )
    stability = AsyncMock(side_effect=[PageStabilizationTimeoutError("busy"), None])
    decode = AsyncMock(
        side_effect=[
            AssetReadinessResult(visible_image_count=1),
            PageAssetLoadError("Visible page assets did not load: 1 visible img"),
        ]
    )

    with (
        patch("app.screenshots.service._wait_for_dom_stability", stability),
        patch("app.screenshots.service._decode_visible_images", decode),
        pytest.raises(PageAssetLoadError) as exc_info,
    ):
        await _do_capture(
            _FakeBrowser(page),
            "https://example.com/",
            {"width": 1280, "height": 720},
            _config(),
        )

    assert "Network diagnostics" in str(exc_info.value)
    assert "net::ERR_CONNECTION_RESET" in str(exc_info.value)


async def test_do_capture_pauses_videos_before_screenshot() -> None:
    page = _FakePage()

    await _do_capture(
        _FakeBrowser(page),
        "https://example.com/",
        {"width": 1280, "height": 720},
        _config(),
    )

    kinds = [
        "pause" if payload == _JS_PAUSE_VIDEOS else kind
        for kind, payload in page.events
    ]
    assert "pause" in kinds
    assert kinds.index("pause") < kinds.index("screenshot")


async def test_capture_screenshot_records_attempt_time_for_failed_rows(
    tmp_path: Path,
) -> None:
    session = _FakeSession()
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    failing = AsyncMock(side_effect=PageAssetLoadError("missing"))
    # attempt 1: 10 → 13 s, attempt 2: 20 → 24 s
    clock = iter([10.0, 13.0, 20.0, 24.0])

    with (
        patch("app.screenshots.service._do_capture", failing),
        patch("app.screenshots.service._clock", side_effect=lambda: next(clock)),
        patch(
            "app.screenshots.service.build_file_path",
            return_value=f"{project_id}/{task_id}/example.jpg",
        ),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(_FakePage()),
            session=session,
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=_config(),
        )

    assert result.status == "failed"
    assert result.duration_seconds == 7.0


async def test_wait_for_fonts_raises_when_page_reports_timeout() -> None:
    class _FontsTimeoutPage(_FakePage):
        async def evaluate(self, script, *args, **kwargs):
            if "document.fonts" in str(script):
                return False
            return await super().evaluate(script, *args, **kwargs)

    with pytest.raises(PageStabilizationTimeoutError):
        await _wait_for_fonts(_FontsTimeoutPage(), 1000)


async def test_decode_visible_images_raises_when_page_reports_timeout() -> None:
    page = _FakePage(asset_state={"timedOut": True})

    with pytest.raises(PageAssetLoadError, match="Timed out"):
        await _decode_visible_images(page, 1000)


async def test_evaluate_bounded_consumes_late_failure() -> None:
    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    gate = loop.create_future()

    class _HangingPage:
        async def evaluate(self, *_args):
            return await gate

    try:
        with (
            patch("app.screenshots.service._JS_TIMEOUT_GRACE_MS", 0),
            pytest.raises(TimeoutError),
        ):
            await _evaluate_bounded(_HangingPage(), "() => 1", 10)

        # The context closes under the abandoned evaluate.
        gate.set_exception(RuntimeError("Target page, context or browser closed"))
        for _ in range(3):
            await asyncio.sleep(0)
    finally:
        loop.set_exception_handler(previous_handler)

    assert reported == []


async def test_do_capture_in_shared_context_sizes_page_and_keeps_context() -> None:
    page = _FakePage()
    context = _FakeContext(page)
    browser = _FakeBrowser(page)
    browser.new_context = AsyncMock(side_effect=AssertionError("must reuse context"))

    outcome = await _do_capture(
        browser,
        "https://example.com/",
        {"width": 390, "height": 844},
        _config(),
        context=context,
    )

    assert outcome.data == b"img"
    # Sized before anything else happens on the page, closed when done.
    assert page.events[0] == ("set_viewport_size", {"width": 390, "height": 844})
    assert page.events[-1] == ("close", None)
    assert context.closed is False


async def test_do_capture_closes_its_own_context() -> None:
    page = _FakePage()
    contexts: list[_FakeContext] = []

    class _TrackingBrowser(_FakeBrowser):
        async def new_context(self, **_kwargs) -> _FakeContext:
            contexts.append(_FakeContext(self.page))
            return contexts[-1]

    await _do_capture(
        _TrackingBrowser(page),
        "https://example.com/",
        {"width": 1280, "height": 720},
        _config(),
    )

    assert len(contexts) == 1 and contexts[0].closed is True
    assert ("set_viewport_size", {"width": 1280, "height": 720}) not in page.events


async def test_capture_screenshot_retries_in_fresh_context(tmp_path: Path) -> None:
    task_id = uuid.uuid4()
    project_id = uuid.uuid4()
    shared = _FakeContext(_FakePage())
    outcome = type(
        "Outcome",
        (),
        {
            "data": b"img",
            "duration": 0.5,
            "captured_with_unstable_dom": False,
            "warning_message": None,
        },
    )()
    do_capture = AsyncMock(side_effect=[PageAssetLoadError("missing"), outcome])

    with (
        patch("app.screenshots.service._do_capture", do_capture),
        patch(
            "app.screenshots.service.build_file_path",
            return_value=f"{project_id}/{task_id}/example.jpg",
        ),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.ensure_parent"),
        patch(
            "app.screenshots.service.abs_file_path",
            return_value=tmp_path / "example.jpg",
        ),
        patch("app.screenshots.service.asyncio.sleep", new=AsyncMock()),
    ):
        result = await capture_screenshot(
            browser=_FakeBrowser(_FakePage()),
            session=_FakeSession(),
            task_id=task_id,
            project_id=project_id,
            url="https://example.com/",
            viewport={"width": 1280, "height": 720},
            config=_config(),
            context=shared,
        )

    assert result.status == "ok"
    assert [call.kwargs["context"] for call in do_capture.await_args_list] == [
        shared,
        None,
    ]
