import logging
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import config as cfg
from app.screenshots.capture import pipeline
from app.screenshots.capture.errors import (
    AttemptFailed,
    BlankPageError,
    PageAssetLoadError,
    PageStabilizationTimeoutError,
)
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import AssetReadinessResult, CaptureOutcome
from tests.capture.fakes import (
    READY_STABILITY_STATE,
    FakeBrowser,
    FakeContext,
    FakePage,
    FakeRequest,
    make_config,
)

URL = "https://example.com/"
DESKTOP = {"width": 1280, "height": 720}
UNSTABLE_TEXT = (
    "Captured with unstable DOM after stabilization timeout. "
    "Content may be incomplete or still settling."
)


async def _run(page: FakePage, **kwargs) -> CaptureOutcome:
    browser = kwargs.pop("browser", None) or FakeBrowser(page)
    config = kwargs.pop("config", None) or make_config()
    viewport = kwargs.pop("viewport", DESKTOP)
    return await pipeline.run_attempt(
        browser, URL, viewport, config, DeviceProfile(), **kwargs
    )


# ---------------------------------------------------------------------------
# run_attempt
# ---------------------------------------------------------------------------


async def test_run_attempt_captures_a_settled_page() -> None:
    page = FakePage()

    outcome = await _run(page)

    assert outcome.data == b"img"
    assert outcome.duration >= 0
    assert outcome.captured_with_unstable_dom is False
    assert outcome.warning_message is None
    assert outcome.report.warnings == []
    assert len(page.goto_calls) == 1
    assert page.screenshot_kwargs is not None
    for marker in ("hide-overlays", "wait-fonts", "wp-rocket-render"):
        assert marker in page.evaluated
    # Fixed backgrounds are normalized once more right before the screenshot.
    screenshot_index = next(
        index for index, (kind, _) in enumerate(page.events) if kind == "screenshot"
    )
    normalize_indices = [
        index
        for index, event in enumerate(page.events)
        if event == ("evaluate", "normalize-fixed-bg")
    ]
    assert normalize_indices
    assert normalize_indices[-1] < screenshot_index


async def test_run_attempt_stage_order() -> None:
    page = FakePage()

    await _run(page)

    assert page.events[0] == ("goto", "load")
    verdict_start = page.evaluated.index("wait-fonts", 3)  # prepare's is at 2
    # prepare ends by pausing videos; reapply_transforms runs right before the
    # readiness verdict ...
    assert page.evaluated[verdict_start - 5 : verdict_start] == [
        "pause-videos",
        "trigger-lazy",
        "wp-rocket-render",
        "normalize-fixed-bg",
        "hide-overlays",
    ]
    assert page.evaluated[verdict_start:].count("stability-state") == 5
    # ... and the final touches right before the screenshot.
    assert page.events[-1][0] == "screenshot"
    assert page.events[-5:-1] == [
        ("evaluate", "hide-overlays"),
        ("wait", 500),
        ("evaluate", "pause-videos"),
        ("evaluate", "normalize-fixed-bg"),
    ]


async def test_run_attempt_records_phase_timings() -> None:
    outcome = await _run(FakePage())

    timings = outcome.report.timings_ms
    expected = {"total", "context", "goto", "quiet", "prepare", "readiness", "snapshot"}
    assert expected <= set(timings)
    assert all(isinstance(value, int) and value >= 0 for value in timings.values())
    assert outcome.report.current_phase is None


async def test_run_attempt_accepts_interactive_page_after_webkit_navigation() -> None:
    page = FakePage(
        script_results={
            "stability-state": {
                **READY_STABILITY_STATE,
                "readyState": "interactive",
                "scrollHeight": 900,
            }
        }
    )

    outcome = await _run(page, config=make_config(browser="webkit"))

    assert outcome.data == b"img"
    assert page.goto_calls[0][1]["wait_until"] == "domcontentloaded"
    assert outcome.captured_with_unstable_dom is False


async def test_run_attempt_rechecks_readiness_in_place_after_timeout() -> None:
    page = FakePage()
    stability = AsyncMock(side_effect=[PageStabilizationTimeoutError("busy"), None])
    config = make_config()

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        outcome = await _run(page, config=config)

    assert outcome.captured_with_unstable_dom is False
    assert len(page.goto_calls) == 1
    assert stability.await_count == 2
    assert stability.await_args_list[0].args[1] == cfg.stabilization_timeout_ms(config)
    assert stability.await_args_list[1].args[1] == cfg.stabilization_timeout_ms(
        config, retry=True
    )


async def test_run_attempt_second_chance_after_fonts_timeout() -> None:
    page = FakePage()
    # Fonts are ready from the third wait on (the fake records the call first).
    page.script_results["wait-fonts"] = lambda _ms: (
        page.evaluated.count("wait-fonts") >= 3
    )

    outcome = await _run(page)

    # prepare (not ready, ignored) → verdict 1 (not ready) → second chance (ready)
    assert page.evaluated.count("wait-fonts") == 3
    assert outcome.captured_with_unstable_dom is False


async def test_run_attempt_unstable_after_second_timeout_keeps_one_navigation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = FakePage()
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.pipeline")

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        outcome = await _run(page)

    assert outcome.captured_with_unstable_dom is True
    assert outcome.warning_message == UNSTABLE_TEXT
    assert len(page.goto_calls) == 1
    assert stability.await_count == 2
    warning = outcome.report.warning("dom_unstable")
    assert warning is not None and warning.details["reason"] == "dom"
    assert caplog.messages == [
        "stabilization timed out for https://example.com/ (settle_timeout_ms=8000), "
        "re-checking readiness in place (settle_timeout_ms=12000)",
        "capturing https://example.com/ with unstable DOM after stabilization "
        "timeout (retry=False)",
    ]
    # The unstable cleanup (300 ms) replaces the normal final pause (500 ms).
    assert ("wait", 300) in page.events
    assert ("wait", 500) not in page.events


async def test_run_attempt_retry_attempt_skips_second_chance() -> None:
    page = FakePage()
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        outcome = await _run(page, attempt=1)

    assert outcome.captured_with_unstable_dom is True
    assert stability.await_count == 1
    # A retry gets the longer window right away.
    assert stability.await_args.args[1] == 12000


async def test_run_attempt_second_chance_asset_failure_keeps_diagnostics() -> None:
    page = FakePage(
        failed_requests=[
            FakeRequest(
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
        patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability),
        patch("app.screenshots.capture.readiness.decode_visible_images", decode),
        pytest.raises(AttemptFailed) as exc_info,
    ):
        await _run(page)

    error = exc_info.value.error
    assert isinstance(error, PageAssetLoadError)
    assert str(error) == (
        "Visible page assets did not load: 1 visible img; Network diagnostics: "
        "failed requests: image net::ERR_CONNECTION_RESET "
        "https://cdn.example.com/missing.jpg"
    )
    assert exc_info.value.report.current_phase == "readiness"


async def test_run_attempt_asset_failure_message_and_log(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = FakePage(
        script_results={
            "decode-images": {
                "visibleImageCount": 1,
                "visibleBackgroundCount": 0,
                "missingVisibleImageCount": 1,
                "missingBackgroundImageCount": 0,
                "hiddenPendingImageCount": 0,
                "missingSamples": [
                    {"kind": "img", "url": "https://cdn.example.com/missing.jpg"}
                ],
            }
        }
    )
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.pipeline")

    with pytest.raises(AttemptFailed) as exc_info:
        await _run(page)

    message = (
        "Visible page assets did not load: 1 visible img; samples: img: "
        "https://cdn.example.com/missing.jpg"
    )
    assert type(exc_info.value.error) is PageAssetLoadError
    assert str(exc_info.value.error) == message
    assert caplog.messages == [
        "asset readiness failed for https://example.com/ (retry=False "
        f"settle_timeout_ms=8000): {message}"
    ]
    assert page.screenshot_kwargs is None


async def test_run_attempt_blank_page_raises_blank_page_error() -> None:
    page = FakePage(
        script_results={"stability-state": {**READY_STABILITY_STATE, "textLength": 0}}
    )

    with pytest.raises(AttemptFailed) as exc_info:
        await _run(page)

    error = exc_info.value.error
    assert isinstance(error, BlankPageError)
    assert isinstance(error, PageAssetLoadError)
    assert str(error) == (
        "Page rendered blank after stabilization: no visible text or "
        "image/background assets"
    )


async def test_run_attempt_wraps_navigation_timeout_and_closes_context() -> None:
    page = FakePage(goto_timeout_levels={"load", "domcontentloaded"})
    browser = FakeBrowser(page)

    with pytest.raises(AttemptFailed) as exc_info:
        await _run(page, browser=browser)

    assert isinstance(exc_info.value.error, PlaywrightTimeoutError)
    assert exc_info.value.report.current_phase == "goto"
    assert "goto" in exc_info.value.report.timings_ms
    assert browser.contexts[0].closed is True


async def test_run_attempt_pauses_videos_before_screenshot() -> None:
    page = FakePage()

    await _run(page)

    kinds = [
        "pause" if event == ("evaluate", "pause-videos") else event[0]
        for event in page.events
    ]
    assert "pause" in kinds
    assert kinds.index("pause") < kinds.index("screenshot")


async def test_run_attempt_in_shared_context_sizes_page_and_keeps_context() -> None:
    page = FakePage()
    context = FakeContext(page)
    browser = FakeBrowser(page)
    browser.new_context = AsyncMock(side_effect=AssertionError("must reuse context"))

    outcome = await _run(
        page, browser=browser, viewport={"width": 390, "height": 844}, context=context
    )

    assert outcome.data == b"img"
    # Sized before anything else happens on the page, closed when done.
    assert page.events[0] == ("set_viewport_size", {"width": 390, "height": 844})
    assert page.events[-1] == ("close", None)
    assert context.closed is False
    assert "context" not in outcome.report.timings_ms


async def test_run_attempt_closes_its_own_context() -> None:
    page = FakePage()
    browser = FakeBrowser(page)

    await _run(page, browser=browser)

    assert len(browser.contexts) == 1 and browser.contexts[0].closed is True
    assert ("set_viewport_size", {"width": 1280, "height": 720}) not in page.events
    assert page.closed is False  # only the context is closed (with its pages)
    assert browser.context_kwargs == [
        {"viewport": {"width": 1280, "height": 720}, "reduced_motion": "reduce"}
    ]


async def test_run_attempt_wraps_context_failures() -> None:
    browser = FakeBrowser(init_script_error=RuntimeError("browser has been closed"))

    with pytest.raises(AttemptFailed) as exc_info:
        await _run(FakePage(), browser=browser)

    assert str(exc_info.value.error) == "browser has been closed"
    assert exc_info.value.report.current_phase == "context"


# ---------------------------------------------------------------------------
# capture_with_retries
# ---------------------------------------------------------------------------


def _outcome(**kwargs) -> CaptureOutcome:
    values = {"data": b"img", "duration": 0.5}
    values.update(kwargs)
    return CaptureOutcome(**values)


async def test_capture_with_retries_returns_the_first_success() -> None:
    outcome = _outcome()
    attempt = AsyncMock(return_value=outcome)
    sleep = AsyncMock()

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", sleep),
    ):
        result = await pipeline.capture_with_retries(
            browser=FakeBrowser(), url=URL, viewport=DESKTOP, config=make_config()
        )

    assert result.ok is True
    assert result.outcome is outcome
    assert result.report is outcome.report
    assert (result.attempts, result.duration, result.error) == (1, 0.5, None)
    sleep.assert_not_awaited()
    assert attempt.await_args.kwargs == {"context": None, "attempt": 0}


async def test_capture_with_retries_retries_in_a_fresh_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shared = FakeContext()
    report = CaptureReport()
    attempt = AsyncMock(
        side_effect=[AttemptFailed(PageAssetLoadError("missing"), report), _outcome()]
    )
    sleep = AsyncMock()
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.pipeline")

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", sleep),
    ):
        result = await pipeline.capture_with_retries(
            browser=FakeBrowser(),
            url=URL,
            viewport=DESKTOP,
            config=make_config(),
            context=shared,
            task_id="task-1",
        )

    assert result.ok is True
    assert result.attempts == 2
    assert [call.kwargs["context"] for call in attempt.await_args_list] == [
        shared,
        None,
    ]
    assert [call.kwargs["attempt"] for call in attempt.await_args_list] == [0, 1]
    sleep.assert_awaited_once_with(2.0)
    assert caplog.messages == [
        "task=task-1 visible asset load failure attempt=1 url=https://example.com/ "
        "viewport=1280x720: missing",
        "task=task-1 retry 1/1 url=https://example.com/ viewport=1280x720",
    ]


async def test_capture_with_retries_does_not_retry_playwright_timeouts(
    caplog: pytest.LogCaptureFixture,
) -> None:
    timeout = PlaywrightTimeoutError("Timeout 30000ms exceeded")
    report = CaptureReport(current_phase="goto")
    attempt = AsyncMock(side_effect=AttemptFailed(timeout, report))
    sleep = AsyncMock()
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.pipeline")

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", sleep),
    ):
        result = await pipeline.capture_with_retries(
            browser=FakeBrowser(),
            url=URL,
            viewport=DESKTOP,
            config=make_config(),
            task_id="t",
        )

    assert result.ok is False
    assert result.outcome is None
    assert result.error is timeout
    assert result.report is report
    assert result.attempts == 1
    sleep.assert_not_awaited()
    assert caplog.messages == [
        "task=t timeout url=https://example.com/ viewport=1280x720 — skipping retry"
    ]


async def test_capture_with_retries_retries_other_errors_and_reports_the_last(
    caplog: pytest.LogCaptureFixture,
) -> None:
    first = RuntimeError("Target page, context or browser has been closed")
    second = ValueError("still broken")
    attempt = AsyncMock(
        side_effect=[
            AttemptFailed(first, CaptureReport()),
            AttemptFailed(second, CaptureReport()),
        ]
    )
    caplog.set_level(logging.ERROR, logger="app.screenshots.capture.pipeline")

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", AsyncMock()),
    ):
        result = await pipeline.capture_with_retries(
            browser=FakeBrowser(),
            url=URL,
            viewport=DESKTOP,
            config=make_config(),
            task_id="t",
        )

    assert result.error is second
    assert result.attempts == 2
    assert caplog.messages == [
        "task=t attempt=1 url=https://example.com/ viewport=1280x720 "
        "error=RuntimeError: Target page, context or browser has been closed",
        "task=t attempt=2 url=https://example.com/ viewport=1280x720 "
        "error=ValueError: still broken",
    ]


async def test_capture_with_retries_handles_unwrapped_errors() -> None:
    attempt = AsyncMock(side_effect=PageAssetLoadError("missing"))

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", AsyncMock()),
    ):
        result = await pipeline.capture_with_retries(
            browser=FakeBrowser(), url=URL, viewport=DESKTOP, config=make_config()
        )

    assert isinstance(result.error, PageAssetLoadError)
    assert isinstance(result.report, CaptureReport)
    assert result.attempts == 2


async def test_capture_with_retries_failure_duration_excludes_sleep() -> None:
    attempt = AsyncMock(side_effect=PageAssetLoadError("missing"))
    # attempt 1: 10 → 13 s, attempt 2: 20 → 24 s
    clock = iter([10.0, 13.0, 20.0, 24.0])

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.clock.now", side_effect=lambda: next(clock)),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", AsyncMock()),
    ):
        result = await pipeline.capture_with_retries(
            browser=FakeBrowser(), url=URL, viewport=DESKTOP, config=make_config()
        )

    assert result.duration == 7.0


async def test_capture_with_retries_builds_a_profile_from_the_viewport() -> None:
    attempt = AsyncMock(return_value=_outcome())

    with patch("app.screenshots.capture.pipeline.run_attempt", attempt):
        await pipeline.capture_with_retries(
            browser=FakeBrowser(),
            url=URL,
            viewport={"width": 390, "height": 844},
            config=make_config(),
        )

    profile = attempt.await_args.args[4]
    assert profile.device_type == "mobile"
    assert profile.is_mobile is True


async def test_capture_with_retries_end_to_end_on_fakes() -> None:
    page = FakePage()

    result = await pipeline.capture_with_retries(
        browser=FakeBrowser(page),
        url=URL,
        viewport=DESKTOP,
        config=make_config(),
        profile=DeviceProfile(),
    )

    assert result.ok is True
    assert result.outcome is not None and result.outcome.data == b"img"
    assert "total" in result.report.timings_ms
