import asyncio
import gc
import logging
import math
from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import pytest
from playwright._impl._errors import TargetClosedError
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import (
    clock,
    navigation,
    pipeline,
    prepare,
    readiness,
    snapshot,
)
from app.screenshots.capture import config as cfg
from app.screenshots.capture.errors import (
    AttemptFailed,
    BlankPageError,
    BrowserUnavailableError,
    CaptureDeadlineExceeded,
    EvaluateTimeout,
    NavigationError,
    NavigationTimeoutError,
    PageBlockedError,
    ScreenshotTimeoutError,
)
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import (
    AssetIssue,
    CaptureOutcome,
    NavigationResult,
    ReadinessResult,
)
from tests.capture.fakes import (
    FakeBrowser,
    FakeContext,
    FakePage,
    FakeRequest,
    make_config,
)
from tests.capture.test_snapshot import SequencePage

URL = "https://example.com/"
DESKTOP = {"width": 1280, "height": 720}
UNSTABLE_TEXT = (
    "Captured with unstable DOM after stabilization timeout. "
    "Content may be incomplete or still settling."
)
SETTLED = ReadinessResult()
UNSETTLED = ReadinessResult(stable=False, unstable_reason="dom")
CSS = "https://cdn.example.com/site.css"
IMG = "https://cdn.example.com/hero.jpg"


async def _run(page: FakePage, **kwargs) -> CaptureOutcome:
    browser = kwargs.pop("browser", None) or FakeBrowser(page)
    config = kwargs.pop("config", None) or make_config()
    viewport = kwargs.pop("viewport", DESKTOP)
    profile = kwargs.pop("profile", DeviceProfile())
    return await pipeline.run_attempt(browser, URL, viewport, config, profile, **kwargs)


def _finalize(*verdicts: ReadinessResult) -> AsyncMock:
    """readiness.finalize answering *verdicts* in order (the last repeats)."""
    remaining = list(verdicts)

    async def finalize(_run, **_kwargs) -> ReadinessResult:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return AsyncMock(side_effect=finalize)


def _retry_in_place(fixed: Callable[[AssetIssue], bool] = lambda _issue: False):
    """readiness.retry_in_place that repairs the issues *fixed* accepts."""

    async def retry(_run, issues):
        return tuple(issue for issue in issues if not fixed(issue))

    return AsyncMock(side_effect=retry)


def _nav(**overrides: Any) -> NavigationResult:
    values: dict[str, Any] = {"requested_url": URL, "final_url": URL, "status": 200}
    values.update(overrides)
    return NavigationResult(**values)


async def _hang(*_args, **_kwargs) -> None:
    # Not asyncio.sleep: some tests patch it (module-wide) to skip the retry pause.
    await asyncio.get_running_loop().create_future()


# ---------------------------------------------------------------------------
# run_attempt
# ---------------------------------------------------------------------------


async def test_run_attempt_captures_a_settled_page() -> None:
    page = FakePage()
    profile = DeviceProfile(device_type="tablet")

    outcome = await _run(page, profile=profile)

    assert outcome.data == b"img"
    assert outcome.duration >= 0
    assert outcome.captured_with_unstable_dom is False
    assert outcome.warning_message is None
    assert outcome.report.warnings == []
    assert outcome.snapshot is not None and outcome.snapshot.data == b"img"
    assert len(page.goto_calls) == 1
    assert page.screenshot_kwargs is not None
    meta = outcome.report.meta
    assert meta["device"] == profile.meta()
    assert meta["browser_version"] == "149.0.7800.0"
    assert meta["readiness"]["second_chance"] is False
    assert meta["page"]["viewport_width"] == 1280


async def test_run_attempt_stage_order() -> None:
    order: list[str] = []

    def spy(name: str, target):
        async def wrapper(*args, **kwargs):
            order.append(name)
            return await target(*args, **kwargs)

        return AsyncMock(side_effect=wrapper)

    stages = {
        "navigation.navigate": navigation.navigate,
        "prepare.prepare_page": prepare.prepare_page,
        "prepare.reapply_transforms": prepare.reapply_transforms,
        "readiness.finalize": readiness.finalize,
        "prepare.final_touches": prepare.final_touches,
        "snapshot.take_snapshot": snapshot.take_snapshot,
    }
    patches = [
        patch(f"app.screenshots.capture.{name}", spy(name, target))
        for name, target in stages.items()
    ]
    for active in patches:
        active.start()
    try:
        await _run(FakePage())
    finally:
        for active in patches:
            active.stop()

    assert order == list(stages)


async def test_run_attempt_records_phase_timings() -> None:
    outcome = await _run(FakePage())

    timings = outcome.report.timings_ms
    expected = {"total", "context", "goto", "prepare", "readiness", "snapshot"}
    assert expected <= set(timings)
    assert "recovery" not in timings and "reload" not in timings
    assert all(isinstance(value, int) and value >= 0 for value in timings.values())
    assert outcome.report.current_phase is None


async def test_run_attempt_rechecks_readiness_in_place_after_timeout() -> None:
    page = FakePage()
    finalize = _finalize(UNSETTLED, SETTLED)
    config = make_config()

    with patch("app.screenshots.capture.readiness.finalize", finalize):
        outcome = await _run(page, config=config)

    assert outcome.captured_with_unstable_dom is False
    assert len(page.goto_calls) == 1
    windows = [call.kwargs["window_ms"] for call in finalize.await_args_list]
    assert windows == [
        cfg.stabilization_timeout_ms(config),
        cfg.stabilization_timeout_ms(config, retry=True),
    ]
    assert outcome.report.meta["readiness"]["second_chance"] is True


async def test_run_attempt_unstable_after_second_timeout_keeps_one_navigation(
    caplog: pytest.LogCaptureFixture,
) -> None:
    page = FakePage()
    final_touches = AsyncMock(wraps=prepare.final_touches)
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.pipeline")

    with (
        patch("app.screenshots.capture.readiness.finalize", _finalize(UNSETTLED)),
        patch("app.screenshots.capture.prepare.final_touches", final_touches),
    ):
        outcome = await _run(page)

    assert outcome.captured_with_unstable_dom is True
    assert outcome.warning_message == UNSTABLE_TEXT
    assert len(page.goto_calls) == 1
    warning = outcome.report.warning("dom_unstable")
    assert warning is not None and warning.details["reason"] == "dom"
    assert caplog.messages == [
        "stabilization timed out for https://example.com/ (settle_timeout_ms=8000), "
        "re-checking readiness in place (settle_timeout_ms=12000)",
        "capturing https://example.com/ with unstable DOM after stabilization "
        "timeout (retry=False)",
    ]
    assert final_touches.await_args.kwargs == {"unstable": True}


async def test_run_attempt_retry_attempt_skips_second_chance() -> None:
    finalize = _finalize(UNSETTLED)

    with patch("app.screenshots.capture.readiness.finalize", finalize):
        outcome = await _run(FakePage(), attempt=1)

    assert outcome.captured_with_unstable_dom is True
    # A retry gets the longer window right away.
    assert [call.kwargs["window_ms"] for call in finalize.await_args_list] == [12000]
    assert outcome.report.meta["readiness"]["second_chance"] is False


async def test_run_attempt_skips_the_second_chance_without_budget() -> None:
    finalize = _finalize(UNSETTLED)

    # The second chance needs its 12 s window + 10 s.
    with patch("app.screenshots.capture.readiness.finalize", finalize):
        outcome = await _run(FakePage(), budget_s=20)

    assert finalize.await_count == 1
    assert outcome.captured_with_unstable_dom is True
    skipped = outcome.report.warning("recovery_skipped")
    assert skipped is not None and skipped.level == "info"
    assert skipped.details["step"] == "second_chance"
    assert skipped.details["outcome"] == "budget"
    assert 0 < skipped.details["remaining_s"] <= 20
    assert outcome.report.meta["readiness"]["second_chance"] is False


async def test_run_attempt_blank_page_raises_blank_page_error() -> None:
    page = FakePage(
        failed_requests=[
            FakeRequest(
                url="https://cdn.example.com/app.css", resource_type="stylesheet"
            )
        ]
    )

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(blank=True)),
        ),
        pytest.raises(AttemptFailed) as exc_info,
    ):
        await _run(page)

    error = exc_info.value.error
    assert isinstance(error, BlankPageError)
    assert str(error) == (
        "Page rendered blank after stabilization: no visible text or "
        "image/background assets; Network diagnostics: failed requests: "
        "stylesheet net::ERR_FAILED https://cdn.example.com/app.css"
    )
    assert page.screenshot_kwargs is None


async def test_run_attempt_wraps_a_navigation_timeout_and_closes_the_context() -> None:
    page = FakePage(goto_timeout_levels={"load", "domcontentloaded"})
    browser = FakeBrowser(page)

    with pytest.raises(AttemptFailed) as exc_info:
        await _run(page, browser=browser)

    error = exc_info.value.error
    assert isinstance(error, NavigationTimeoutError)
    assert str(error) == "Navigation timed out after 1000 ms: https://example.com/"
    assert exc_info.value.report.current_phase == "goto"
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


async def test_run_attempt_without_a_profile_builds_one_for_the_browser() -> None:
    page = FakePage()
    browser = FakeBrowser(page)

    outcome = await pipeline.run_attempt(
        browser, URL, {"width": 390, "height": 844}, make_config(), None
    )

    device = outcome.report.meta["device"]
    assert device["type"] == "mobile"
    # Built with the browser's version: a real user agent.
    assert "149" in (browser.context_kwargs[0].get("user_agent") or "")
    assert device["user_agent"] == browser.context_kwargs[0]["user_agent"]


async def test_run_attempt_wraps_context_failures() -> None:
    browser = FakeBrowser(init_script_error=RuntimeError("browser has been closed"))

    with pytest.raises(AttemptFailed) as exc_info:
        await _run(FakePage(), browser=browser)

    assert str(exc_info.value.error) == "browser has been closed"
    assert exc_info.value.report.current_phase == "context"


async def test_run_attempt_close_failures_do_not_fail_the_attempt() -> None:
    page = FakePage()
    browser = FakeBrowser(page)
    original = browser.new_context

    async def new_context(**kwargs):
        context = await original(**kwargs)
        context.close = AsyncMock(side_effect=RuntimeError("Connection closed"))
        return context

    browser.new_context = new_context

    outcome = await _run(page, browser=browser)

    assert outcome.data == b"img"


# ---------------------------------------------------------------------------
# run_attempt: time budget
# ---------------------------------------------------------------------------


async def test_run_attempt_deadline_names_the_phase_and_closes_the_context() -> None:
    page = FakePage()
    browser = FakeBrowser(page)

    with (
        patch("app.screenshots.capture.config.CLOSE_TIMEOUT_S", 0.05),
        patch(
            "app.screenshots.capture.readiness.finalize", AsyncMock(side_effect=_hang)
        ),
        pytest.raises(AttemptFailed) as exc_info,
    ):
        await _run(page, browser=browser, budget_s=0.05)

    error = exc_info.value.error
    assert isinstance(error, CaptureDeadlineExceeded)
    assert not isinstance(error, TimeoutError)
    assert error.phase == "readiness"
    assert "readiness" in str(error)
    assert exc_info.value.report.current_phase == "readiness"
    assert browser.contexts[0].closed is True


async def test_run_attempt_deadline_close_is_bounded() -> None:
    page = FakePage()
    context = FakeContext(page)
    page.close = AsyncMock(side_effect=_hang)

    with (
        patch("app.screenshots.capture.config.CLOSE_TIMEOUT_S", 0.05),
        patch(
            "app.screenshots.capture.prepare.prepare_page", AsyncMock(side_effect=_hang)
        ),
        pytest.raises(AttemptFailed) as exc_info,
    ):
        await asyncio.wait_for(_run(page, context=context, budget_s=0.05), 5)

    assert isinstance(exc_info.value.error, CaptureDeadlineExceeded)
    assert exc_info.value.error.phase == "prepare"
    page.close.assert_awaited_once()


class _PendingGotoPage(FakePage):
    """A goto that waits on a protocol future the way Playwright's
    Channel._inner_send does (asyncio.wait, so cancelling the caller leaves
    the future pending); closing the page fails it."""

    def __init__(self) -> None:
        super().__init__()
        self.pending: asyncio.Future | None = None

    async def goto(self, url: str, **kwargs: Any):
        self.goto_calls.append((url, kwargs))
        pending = asyncio.get_running_loop().create_future()
        self.pending = pending
        await asyncio.wait({pending})
        return pending.result()

    async def close(self) -> None:
        await super().close()
        if self.pending is not None and not self.pending.done():
            self.pending.set_exception(TargetClosedError())
        self.pending = None


async def test_run_attempt_deadline_leaves_no_unretrieved_playwright_error() -> None:
    loop = asyncio.get_running_loop()
    reported: list[dict] = []
    previous_handler = loop.get_exception_handler()
    loop.set_exception_handler(lambda _loop, context: reported.append(context))
    page = _PendingGotoPage()

    try:
        with pytest.raises(AttemptFailed) as exc_info:
            await _run(page, context=FakeContext(page), budget_s=0.05)
        error_type = type(exc_info.value.error)
        phase = getattr(exc_info.value.error, "phase", None)
        del exc_info  # its tracebacks would keep an abandoned future alive
        for _ in range(3):
            await asyncio.sleep(0)
        gc.collect()
    finally:
        loop.set_exception_handler(previous_handler)

    assert error_type is CaptureDeadlineExceeded
    assert phase == "goto"
    # The page was closed under the goto, which then failed in its own task.
    assert page.closed is True
    assert reported == []


async def test_run_attempt_cancelled_closes_the_page_first() -> None:
    page = _PendingGotoPage()
    task = asyncio.ensure_future(_run(page, context=FakeContext(page)))
    while not page.goto_calls:
        await asyncio.sleep(0)

    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert page.closed is True


async def test_run_attempt_never_lets_a_bare_timeout_error_out() -> None:
    with (
        patch(
            "app.screenshots.capture.prepare.prepare_page",
            AsyncMock(side_effect=TimeoutError()),
        ),
        pytest.raises(AttemptFailed) as exc_info,
    ):
        await _run(FakePage())

    error = exc_info.value.error
    assert isinstance(error, EvaluateTimeout)
    assert "prepare" in str(error)


# ---------------------------------------------------------------------------
# run_attempt: recovery ladder
# ---------------------------------------------------------------------------


async def test_missing_image_is_captured_with_a_warning_and_no_reload() -> None:
    issue = AssetIssue(
        kind="img", url=IMG, category="missing", status=404, reason="HTTP 404"
    )
    retry = _retry_in_place()
    reload = AsyncMock()

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(issue,))),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", retry),
        patch("app.screenshots.capture.navigation.reload", reload),
    ):
        outcome = await _run(FakePage())

    assert outcome.data == b"img"
    retry.assert_awaited_once()
    assert retry.await_args.args[1] == (issue,)
    reload.assert_not_awaited()
    warning = outcome.report.warning("assets_missing")
    assert warning is not None and warning.level == "warning"
    assert warning.details["count"] == 1
    assert warning.details["samples"] == [
        {
            "kind": "img",
            "url": IMG,
            "status": 404,
            "reason": "HTTP 404",
            "category": "missing",
        }
    ]
    assert outcome.report.warning_codes() == ["assets_missing"]
    assert "recovery" in outcome.report.timings_ms


async def test_assets_missing_counts_every_asset_and_keeps_ten_samples() -> None:
    issues = tuple(
        AssetIssue(kind="background", url=f"{IMG}?{n}", category="missing")
        for n in range(12)
    )

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=issues)),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
    ):
        outcome = await _run(FakePage())

    warning = outcome.report.warning("assets_missing")
    assert warning is not None
    assert warning.details["count"] == 12
    assert len(warning.details["samples"]) == 10
    assert warning.message == "12 visible asset(s) did not load: 12 background"


async def test_assets_repaired_in_place_are_reported_as_recovered() -> None:
    issue = AssetIssue(kind="img", url=IMG, category="blocked", status=403)
    reload = AsyncMock()

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(issue,))),
        ),
        patch(
            "app.screenshots.capture.readiness.retry_in_place",
            _retry_in_place(lambda _issue: True),
        ),
        patch("app.screenshots.capture.navigation.reload", reload),
    ):
        outcome = await _run(FakePage())

    reload.assert_not_awaited()
    assert outcome.report.warning_codes() == ["assets_recovered"]
    recovered = outcome.report.warning("assets_recovered")
    assert recovered is not None and recovered.level == "info"
    assert (recovered.details["in_place"], recovered.details["after_reload"]) == (
        1,
        0,
    )
    assert recovered.details["samples"] == [
        {
            "kind": "img",
            "url": IMG,
            "status": 403,
            "reason": None,
            "category": "blocked",
        }
    ]


async def test_blocked_stylesheet_reloads_and_recovers() -> None:
    css = AssetIssue(
        kind="stylesheet", url=CSS, category="blocked", status=200, reason="text/html"
    )
    page = SequencePage(b"before-reload", b"after-reload")
    finalize = _finalize(ReadinessResult(issues=(css,)), SETTLED)
    reloaded_nav = _nav(reloads=1)
    reload = AsyncMock(return_value=reloaded_nav)
    prepare_page = AsyncMock(wraps=prepare.prepare_page)
    report_status = Mock(return_value=None)

    with (
        patch("app.screenshots.capture.readiness.finalize", finalize),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch("app.screenshots.capture.navigation.reload", reload),
        patch("app.screenshots.capture.prepare.prepare_page", prepare_page),
        patch("app.screenshots.capture.navigation.report_status", report_status),
    ):
        outcome = await _run(page)

    reload.assert_awaited_once()
    assert reload.await_args.kwargs == {"reason": "styles_missing"}
    assert prepare_page.await_count == 2  # the reloaded page is prepared again
    assert finalize.await_count == 2
    # The fallback snapshot came first; the image is the reloaded page.
    assert len(page.screenshot_calls) == 2
    assert outcome.data == b"after-reload"
    report = outcome.report
    assert report.warning_codes() == ["page_reloaded", "assets_recovered"]
    reloaded = report.warning("page_reloaded")
    assert reloaded is not None and reloaded.level == "info"
    assert reloaded.details["reason"] == "styles_missing"
    recovered = report.warning("assets_recovered")
    assert recovered is not None
    assert (recovered.details["in_place"], recovered.details["after_reload"]) == (
        0,
        1,
    )
    # The reloaded page is prepared and checked again within the same phases.
    assert {"prepare", "readiness", "recovery"} <= set(report.timings_ms)
    # The status is reported once, for the reloaded page.
    report_status.assert_called_once()
    assert report_status.call_args.args[0].nav is reloaded_nav


async def test_stylesheet_still_broken_after_the_reload_is_reported() -> None:
    css = AssetIssue(kind="stylesheet", url=CSS, category="network")

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(css,))),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch(
            "app.screenshots.capture.navigation.reload",
            AsyncMock(return_value=_nav()),
        ),
    ):
        outcome = await _run(SequencePage(b"one", b"two"))

    assert outcome.data == b"two"
    assert outcome.report.warning_codes() == ["page_reloaded", "styles_missing"]
    styles = outcome.report.warning("styles_missing")
    assert styles is not None and styles.level == "warning"
    assert styles.details["urls"] == [CSS]
    assert styles.details["reasons"] == ["network"]
    assert styles.details["samples"] == [
        {
            "kind": "stylesheet",
            "url": CSS,
            "status": None,
            "reason": None,
            "category": "network",
        }
    ]


async def test_reload_failure_falls_back_to_the_snapshot_before_it() -> None:
    css = AssetIssue(kind="stylesheet", url=CSS, category="blocked", status=403)
    page = SequencePage(b"before-reload", b"unused")
    reload = AsyncMock(side_effect=PlaywrightError("net::ERR_CONNECTION_RESET"))

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(css,))),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch("app.screenshots.capture.navigation.reload", reload),
    ):
        outcome = await _run(page)

    assert outcome.data == b"before-reload"
    assert len(page.screenshot_calls) == 1
    report = outcome.report
    assert "page_reloaded" not in report.warning_codes()
    assert report.warning("styles_missing") is not None
    skipped = report.warning("recovery_skipped")
    assert skipped is not None
    assert skipped.details["step"] == "reload"
    assert skipped.details["outcome"] == "failed"
    assert "ERR_CONNECTION_RESET" in skipped.details["error"]
    # The fallback's page meta is kept.
    assert report.meta["page"]["viewport_width"] == 1280


async def test_unavailable_reload_keeps_the_current_page() -> None:
    img = AssetIssue(kind="img", url=IMG, category="blocked", status=403)
    page = SequencePage(b"current")
    nav = _nav()
    report_status = Mock(return_value=None)

    with (
        patch(
            "app.screenshots.capture.navigation.reload",
            AsyncMock(side_effect=NotImplementedError),
        ),
        patch(
            "app.screenshots.capture.navigation.navigate", AsyncMock(return_value=nav)
        ),
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(img,))),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch("app.screenshots.capture.navigation.report_status", report_status),
    ):
        outcome = await _run(page)

    assert outcome.data == b"current"
    assert len(page.screenshot_calls) == 1
    skipped = outcome.report.warning("recovery_skipped")
    assert skipped is not None
    assert (skipped.details["step"], skipped.details["outcome"]) == (
        "reload",
        "unavailable",
    )
    assert skipped.details["reason"] == "assets_blocked"
    assert outcome.report.warning("assets_missing") is not None
    assert report_status.call_args.args[0].nav is nav


async def test_blank_page_after_the_reload_falls_back() -> None:
    css = AssetIssue(kind="stylesheet", url=CSS, category="blocked")
    original_nav = _nav()
    report_status = Mock(return_value=None)

    with (
        patch(
            "app.screenshots.capture.navigation.navigate",
            AsyncMock(return_value=original_nav),
        ),
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(css,)), ReadinessResult(blank=True)),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch(
            "app.screenshots.capture.navigation.reload",
            AsyncMock(return_value=_nav(reloads=1)),
        ),
        patch("app.screenshots.capture.navigation.report_status", report_status),
    ):
        outcome = await _run(SequencePage(b"before", b"after"))

    assert outcome.data == b"before"
    skipped = outcome.report.warning("recovery_skipped")
    assert skipped is not None and skipped.details["outcome"] == "blank"
    assert outcome.report.warning("styles_missing") is not None
    # The status reported is the one of the page in the image.
    assert report_status.call_args.args[0].nav is original_nav


async def test_reload_that_runs_out_of_budget_falls_back() -> None:
    css = AssetIssue(kind="stylesheet", url=CSS, category="blocked")

    with (
        patch("app.screenshots.capture.config.RELOAD_RESERVE_S", 29.9),
        patch("app.screenshots.capture.config.CLOSE_TIMEOUT_S", 0.05),
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(css,))),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch(
            "app.screenshots.capture.navigation.reload", AsyncMock(side_effect=_hang)
        ),
    ):
        outcome = await asyncio.wait_for(_run(SequencePage(b"before"), budget_s=30), 5)

    assert outcome.data == b"before"
    skipped = outcome.report.warning("recovery_skipped")
    assert skipped is not None and skipped.details["outcome"] == "budget"


async def test_reload_is_skipped_without_budget() -> None:
    css = AssetIssue(kind="stylesheet", url=CSS, category="blocked")
    reload = AsyncMock()
    page = SequencePage(b"img")

    with (
        patch("app.screenshots.capture.config.RELOAD_COST_FACTOR", 1e9),
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=(css,))),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch("app.screenshots.capture.navigation.reload", reload),
    ):
        outcome = await _run(page, budget_s=100)

    reload.assert_not_awaited()
    assert len(page.screenshot_calls) == 1
    skipped = outcome.report.warning("recovery_skipped")
    assert skipped is not None
    assert skipped.details["step"] == "reload"
    assert skipped.details["needed_s"] > skipped.details["remaining_s"]
    assert outcome.report.warning("styles_missing") is not None


async def test_main_document_asking_for_a_reload_is_reloaded() -> None:
    nav = _nav(status=503)
    nav.needs_reload = True  # NavigationResult.needs_reload (5xx / 429)
    reload = AsyncMock(return_value=_nav())

    with (
        patch(
            "app.screenshots.capture.navigation.navigate", AsyncMock(return_value=nav)
        ),
        patch("app.screenshots.capture.navigation.reload", reload),
    ):
        outcome = await _run(FakePage())

    assert reload.await_args.kwargs == {"reason": "http_status"}
    assert outcome.report.warning_codes() == ["page_reloaded"]


@pytest.mark.parametrize(
    ("issues", "expected"),
    [
        ((), None),
        ((AssetIssue(kind="img", url=IMG, category="missing", status=404),), None),
        ((AssetIssue(kind="img", url=IMG, category="unknown"),), None),
        ((AssetIssue(kind="img", url=IMG, category="pending"),), None),
        ((AssetIssue(kind="font", url=IMG, category="blocked"),), None),
        ((AssetIssue(kind="stylesheet", url=CSS, category="missing"),), None),
        (
            (AssetIssue(kind="stylesheet", url=CSS, category="unknown"),),
            "styles_missing",
        ),
        ((AssetIssue(kind="img", url=IMG, category="blocked"),), "assets_blocked"),
        (
            (AssetIssue(kind="background", url=IMG, category="network"),),
            "assets_blocked",
        ),
    ],
)
def test_reload_reason(issues: tuple[AssetIssue, ...], expected: str | None) -> None:
    assert pipeline.reload_reason(issues, _nav()) == expected


async def test_pending_images_and_fonts_are_left_to_readiness() -> None:
    # Pending images are readiness's assets_pending; font issues are
    # informational (fonts_timeout) — neither is assets_missing nor reloaded.
    issues = (
        AssetIssue(kind="img", url=IMG, category="pending"),
        AssetIssue(kind="font", url=f"{CSS}.woff2", category="blocked", status=403),
    )
    reload = AsyncMock()

    with (
        patch(
            "app.screenshots.capture.readiness.finalize",
            _finalize(ReadinessResult(issues=issues)),
        ),
        patch("app.screenshots.capture.readiness.retry_in_place", _retry_in_place()),
        patch("app.screenshots.capture.navigation.reload", reload),
    ):
        outcome = await _run(FakePage())

    assert outcome.data == b"img"
    reload.assert_not_awaited()
    assert outcome.report.warning_codes() == []


async def test_report_status_runs_once_after_recovery() -> None:
    report_status = Mock(return_value=None)

    with patch("app.screenshots.capture.navigation.report_status", report_status):
        await _run(FakePage())

    report_status.assert_called_once()


async def test_overflow_is_cropped_end_to_end() -> None:
    page = FakePage(
        script_results={
            "page-metrics": {
                "docWidth": 3518,
                "docHeight": 2000,
                "innerWidth": 1280,
                "overflowXHidden": True,
                "dir": "ltr",
            }
        }
    )

    outcome = await _run(page)

    assert page.screenshot_kwargs is not None
    assert page.screenshot_kwargs["clip"]["width"] == 1280
    assert outcome.snapshot is not None and outcome.snapshot.clip_width == 1280
    assert outcome.report.warning("overflow_cropped") is not None
    assert outcome.report.meta["page"]["document_width"] == 3518


# ---------------------------------------------------------------------------
# capture_with_retries
# ---------------------------------------------------------------------------


def _outcome(**kwargs) -> CaptureOutcome:
    values = {"data": b"img", "duration": 0.5}
    values.update(kwargs)
    return CaptureOutcome(**values)


async def _retries(attempt: AsyncMock, **kwargs):
    kwargs.setdefault("browser", FakeBrowser())
    kwargs.setdefault("url", URL)
    kwargs.setdefault("viewport", DESKTOP)
    kwargs.setdefault("config", make_config())
    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", AsyncMock()),
    ):
        return await pipeline.capture_with_retries(**kwargs)


def test_attempt_budget() -> None:
    assert pipeline.attempt_budget_s(make_config(timeout=1000)) == 240
    assert pipeline.attempt_budget_s(make_config(timeout=60_000)) == 330
    assert pipeline.attempt_budget_s(make_config(timeout=120_000)) == 480


async def test_capture_with_retries_returns_the_first_success() -> None:
    outcome = _outcome()
    attempt = AsyncMock(return_value=outcome)
    sleep = AsyncMock()
    browser = FakeBrowser()

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", attempt),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", sleep),
    ):
        result = await pipeline.capture_with_retries(
            browser=browser, url=URL, viewport=DESKTOP, config=make_config()
        )

    assert result.ok is True
    assert result.outcome is outcome
    assert result.report is outcome.report
    assert (result.attempts, result.duration, result.error) == (1, 0.5, None)
    sleep.assert_not_awaited()
    assert attempt.await_args.kwargs == {"context": None, "attempt": 0, "budget_s": 240}
    meta = result.report.meta
    assert meta["attempts"] == 1
    assert meta["browser_version"] == "149.0.7800.0"
    assert meta["device"] == attempt.await_args.args[4].meta()
    assert "failure" not in meta


async def test_capture_with_retries_retries_in_a_fresh_context(
    caplog: pytest.LogCaptureFixture,
) -> None:
    shared = FakeContext()
    report = CaptureReport(current_phase="readiness")
    attempt = AsyncMock(
        side_effect=[AttemptFailed(BlankPageError("blank"), report), _outcome()]
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
    assert result.report.meta["attempts"] == 2
    assert [call.kwargs["context"] for call in attempt.await_args_list] == [
        shared,
        None,
    ]
    assert [call.kwargs["attempt"] for call in attempt.await_args_list] == [0, 1]
    sleep.assert_awaited_once_with(2.0)
    assert caplog.messages == [
        "task=task-1 attempt=1 url=https://example.com/ viewport=1280x720 failed "
        "(phase=readiness) error=BlankPageError: blank",
        "task=task-1 retry 1/1 url=https://example.com/ viewport=1280x720",
    ]


@pytest.mark.parametrize(
    ("error", "retried"),
    [
        (NavigationTimeoutError("Timeout 30000ms exceeded"), False),
        (NavigationError("net::ERR_NAME_NOT_RESOLVED"), False),
        (PageBlockedError("cloudflare", 403), False),
        (BlankPageError(), True),
        (ScreenshotTimeoutError("Screenshot timed out"), True),
        (EvaluateTimeout("probe did not finish"), True),
        (CaptureDeadlineExceeded("readiness"), True),
        (PlaywrightTimeoutError("Timeout 30000ms exceeded"), True),
        (PlaywrightError("Protocol error"), True),
        (TargetClosedError(), True),
        (RuntimeError("boom"), True),
    ],
)
async def test_capture_with_retries_retry_policy(
    error: Exception, retried: bool
) -> None:
    report = CaptureReport(current_phase="snapshot")
    attempt = AsyncMock(side_effect=AttemptFailed(error, report))

    result = await _retries(attempt)

    assert result.ok is False
    assert result.error is error
    assert result.attempts == (2 if retried else 1)
    assert attempt.await_count == result.attempts
    assert result.report is report
    assert result.report.meta["failure"] == {
        "class": type(error).__name__,
        "phase": "snapshot",
    }
    assert result.report.meta["attempts"] == result.attempts


async def test_capture_with_retries_reports_the_last_error() -> None:
    first = RuntimeError("first")
    second = ValueError("still broken")
    second_report = CaptureReport(current_phase="goto")
    attempt = AsyncMock(
        side_effect=[
            AttemptFailed(first, CaptureReport()),
            AttemptFailed(second, second_report),
        ]
    )

    result = await _retries(attempt)

    assert result.error is second
    assert result.report is second_report
    assert result.report.meta["failure"] == {"class": "ValueError", "phase": "goto"}


async def test_capture_with_retries_asks_the_provider_before_a_retry() -> None:
    first_browser = FakeBrowser()
    second_browser = FakeBrowser(version="150.0.1.2")
    provider = AsyncMock(return_value=second_browser)
    profile = DeviceProfile(device_type="mobile", is_mobile=True)
    attempt = AsyncMock(
        side_effect=[
            AttemptFailed(TargetClosedError(), CaptureReport()),
            _outcome(),
        ]
    )

    result = await _retries(
        attempt,
        browser=first_browser,
        browser_provider=provider,
        profile=profile,
        context=FakeContext(),
    )

    assert result.ok is True
    provider.assert_awaited_once()  # not before the first attempt: connected
    browsers = [call.args[0] for call in attempt.await_args_list]
    assert browsers == [first_browser, second_browser]
    # The same profile on the new browser, in a fresh context.
    assert [call.args[4] for call in attempt.await_args_list] == [profile, profile]
    assert attempt.await_args_list[1].kwargs["context"] is None
    assert result.report.meta["browser_version"] == "150.0.1.2"


async def test_capture_with_retries_reconnects_a_dead_browser_first() -> None:
    dead = FakeBrowser(connected=False)
    live = FakeBrowser()
    provider = AsyncMock(return_value=live)
    attempt = AsyncMock(return_value=_outcome())

    result = await _retries(
        attempt, browser=dead, browser_provider=provider, context=FakeContext()
    )

    assert result.ok is True
    provider.assert_awaited_once()
    assert attempt.await_args.args[0] is live
    # The shared context belonged to the dead browser.
    assert attempt.await_args.kwargs["context"] is None


async def test_capture_with_retries_keeps_a_shared_context_on_the_same_browser() -> (
    None
):
    browser = FakeBrowser(connected=False)
    shared = FakeContext()
    attempt = AsyncMock(return_value=_outcome())

    result = await _retries(
        attempt,
        browser=browser,
        browser_provider=AsyncMock(return_value=browser),
        context=shared,
    )

    assert result.ok is True
    assert attempt.await_args.kwargs["context"] is shared


@pytest.mark.parametrize(
    "provider",
    [AsyncMock(return_value=None), AsyncMock(side_effect=RuntimeError("no server"))],
)
async def test_capture_with_retries_stops_when_no_browser_comes_back(
    provider: AsyncMock,
) -> None:
    failed_report = CaptureReport(current_phase="goto")
    attempt = AsyncMock(
        side_effect=AttemptFailed(
            PlaywrightError("Browser has been closed"), failed_report
        )
    )

    result = await _retries(attempt, browser_provider=provider)

    assert attempt.await_count == 1
    assert isinstance(result.error, BrowserUnavailableError)
    assert str(result.error) == "Browser connection lost; reconnect failed"
    assert result.attempts == 1
    assert result.report is failed_report
    assert result.report.meta["failure"] == {
        "class": "BrowserUnavailableError",
        "phase": None,
    }


async def test_capture_with_retries_dead_browser_and_no_reconnect() -> None:
    attempt = AsyncMock(return_value=_outcome())

    result = await _retries(
        attempt,
        browser=FakeBrowser(connected=False),
        browser_provider=AsyncMock(return_value=None),
    )

    attempt.assert_not_awaited()
    assert isinstance(result.error, BrowserUnavailableError)
    assert result.attempts == 0
    assert result.report.meta["attempts"] == 0


async def test_capture_with_retries_without_a_provider_retries_on_the_same_browser(
    caplog: pytest.LogCaptureFixture,
) -> None:
    browser = FakeBrowser(connected=False)
    attempt = AsyncMock(
        side_effect=[
            AttemptFailed(
                PlaywrightError("Target page, context or browser has been closed"),
                CaptureReport(),
            ),
            _outcome(),
        ]
    )
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.pipeline")

    result = await _retries(attempt, browser=browser, task_id="t")

    assert result.ok is True
    assert [call.args[0] for call in attempt.await_args_list] == [browser, browser]
    assert caplog.messages[-1].endswith("(browser connection lost)")


async def test_capture_with_retries_caps_the_budget_by_the_deadline() -> None:
    attempt = AsyncMock(return_value=_outcome())
    deadline_at = clock.now() + 100

    await _retries(attempt, deadline_at=deadline_at)

    assert 99 < attempt.await_args.kwargs["budget_s"] <= 100


async def test_capture_with_retries_needs_a_minute_for_a_retry() -> None:
    attempt = AsyncMock(
        side_effect=AttemptFailed(ScreenshotTimeoutError("slow"), CaptureReport())
    )
    deadline_at = clock.now() + 61  # 59 s after the pause

    result = await _retries(attempt, deadline_at=deadline_at)

    assert result.attempts == 1
    assert isinstance(result.error, ScreenshotTimeoutError)


async def test_capture_with_retries_retries_with_a_minute_left() -> None:
    attempt = AsyncMock(
        side_effect=[
            AttemptFailed(ScreenshotTimeoutError("slow"), CaptureReport()),
            _outcome(),
        ]
    )
    deadline_at = clock.now() + 200

    result = await _retries(attempt, deadline_at=deadline_at)

    assert result.ok is True
    second_budget = attempt.await_args_list[1].kwargs["budget_s"]
    assert 195 < second_budget <= 200


async def test_capture_with_retries_past_the_deadline_runs_nothing() -> None:
    attempt = AsyncMock(return_value=_outcome())

    result = await _retries(attempt, deadline_at=clock.now() - 1)

    attempt.assert_not_awaited()
    assert isinstance(result.error, CaptureDeadlineExceeded)
    assert result.attempts == 0


async def test_capture_with_retries_handles_unwrapped_errors() -> None:
    attempt = AsyncMock(side_effect=RuntimeError("missing"))

    result = await _retries(attempt)

    assert isinstance(result.error, RuntimeError)
    assert isinstance(result.report, CaptureReport)
    assert result.attempts == 2
    assert result.report.meta["failure"] == {"class": "RuntimeError", "phase": None}


async def test_no_builtin_timeout_error_comes_out_of_capture_with_retries() -> None:
    attempt = AsyncMock(side_effect=TimeoutError())

    result = await _retries(attempt)

    assert isinstance(result.error, EvaluateTimeout)
    assert not isinstance(result.error, TimeoutError)


async def test_capture_with_retries_deadline_end_to_end() -> None:
    page = FakePage()
    browser = FakeBrowser(page)

    with (
        patch("app.screenshots.capture.config.CLOSE_TIMEOUT_S", 0.05),
        patch("app.screenshots.capture.config.ATTEMPT_BUDGET_MIN_S", 0.05),
        patch("app.screenshots.capture.config.ATTEMPT_BUDGET_MAX_S", 0.05),
        patch(
            "app.screenshots.capture.prepare.prepare_page", AsyncMock(side_effect=_hang)
        ),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", AsyncMock()),
    ):
        result = await asyncio.wait_for(
            pipeline.capture_with_retries(
                browser=browser, url=URL, viewport=DESKTOP, config=make_config()
            ),
            5,
        )

    assert isinstance(result.error, CaptureDeadlineExceeded)
    assert result.error.phase == "prepare"
    assert result.attempts == 2  # a deadline is retried
    assert result.report.meta["failure"] == {
        "class": "CaptureDeadlineExceeded",
        "phase": "prepare",
    }
    assert all(context.closed for context in browser.contexts)


async def test_capture_with_retries_failure_duration_excludes_sleep() -> None:
    attempt = AsyncMock(side_effect=RuntimeError("missing"))
    # attempt 1: 10 → 13 s, attempt 2: 20 → 24 s
    clock = iter([10.0, 13.0, 20.0, 24.0])

    with patch("app.screenshots.capture.clock.now", side_effect=lambda: next(clock)):
        result = await _retries(attempt)

    assert result.duration == 7.0


async def test_capture_with_retries_builds_a_profile_from_the_viewport() -> None:
    attempt = AsyncMock(return_value=_outcome())

    await _retries(attempt, viewport={"width": 390, "height": 844})

    profile = attempt.await_args.args[4]
    assert profile.device_type == "mobile"
    assert profile.is_mobile is True
    # Built with the browser's version: the device user agent is set.
    assert profile.user_agent is not None and "149" in profile.user_agent


async def test_capture_with_retries_end_to_end_on_fakes() -> None:
    page = FakePage()
    profile = DeviceProfile()

    result = await pipeline.capture_with_retries(
        browser=FakeBrowser(page),
        url=URL,
        viewport=DESKTOP,
        config=make_config(),
        profile=profile,
    )

    assert result.ok is True
    assert result.outcome is not None and result.outcome.data == b"img"
    assert "total" in result.report.timings_ms
    assert result.report.meta["attempts"] == 1
    assert result.report.meta["device"] == profile.meta()
    assert math.isfinite(result.duration)
