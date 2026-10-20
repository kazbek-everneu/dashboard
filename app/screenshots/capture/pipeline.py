"""Capture pipeline: one attempt (run_attempt) strings the stages together —
navigation → prepare → readiness → final touches → snapshot — and
capture_with_retries runs the attempts."""

import asyncio
import logging
import math
from collections.abc import Awaitable, Callable, Mapping

from playwright.async_api import Browser, BrowserContext, Page
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import (
    clock,
    navigation,
    network,
    prepare,
    profiles,
    readiness,
    snapshot,
)
from app.screenshots.capture import config as cfg
from app.screenshots.capture.config import EffectiveConfig
from app.screenshots.capture.errors import (
    BLANK_PAGE_MESSAGE,
    AttemptFailed,
    BlankPageError,
    PageAssetLoadError,
)
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import (
    CaptureOutcome,
    CaptureResult,
    CaptureRun,
    Deadline,
    ReadinessResult,
)

logger = logging.getLogger(__name__)

UNSTABLE_DOM_WARNING = (
    "Captured with unstable DOM after stabilization timeout. "
    "Content may be incomplete or still settling."
)

BrowserProvider = Callable[[], Awaitable[Browser | None]]


async def run_attempt(
    browser: Browser,
    url: str,
    viewport: Mapping[str, int],
    config: EffectiveConfig,
    profile: DeviceProfile | None,
    *,
    context: BrowserContext | None = None,
    attempt: int = 0,
    budget_s: float = math.inf,
) -> CaptureOutcome:
    """One capture attempt: navigate, apply page transforms, and return the
    PNG/JPEG bytes and the duration from page.goto → page.screenshot.

    By default a new context is created for the call so cookies/localStorage
    never leak between screenshots, and it is closed when the attempt ends.
    When a shared *context* is passed (per-URL context reuse, see
    profiles.open_capture_context) only a new page is opened in it, sized to
    *viewport* before navigation, and only that page is closed — the context's
    HTTP cache then serves the other viewports of the same URL.

    Attempt 0 gets an in-place second readiness check (longer window) when the
    page does not settle; later attempts capture an unsettled page right away.

    Any failure is raised as AttemptFailed carrying the original error and the
    attempt's report. *budget_s* is not enforced yet.

    Duration intentionally excludes:
    - browser.new_context() / context.close() overhead
    - semaphore wait time
    - retry sleep time
    - DB write time
    """
    report = CaptureReport()
    if profile is None:
        profile = DeviceProfile()
    deadline = Deadline.start(budget_s)
    own_context = context is None
    try:
        with report.phase("total"):
            if context is None:
                with report.phase("context"):
                    context = await profiles.open_capture_context(
                        browser, viewport, profile
                    )
            page: Page | None = None
            try:
                page = await context.new_page()
                if not own_context:
                    await page.set_viewport_size(
                        {"width": viewport["width"], "height": viewport["height"]}
                    )
                await profiles.prepare_page_identity(page, profile)
                net = network.NetworkDiagnostics()
                net.attach(page)
                tracker = network.InflightTracker()
                tracker.attach(page)
                run = CaptureRun(
                    url=url,
                    viewport={"width": viewport["width"], "height": viewport["height"]},
                    config=config,
                    profile=profile,
                    page=page,
                    tracker=tracker,
                    net=net,
                    report=report,
                    deadline=deadline,
                    attempt=attempt,
                )
                return await _capture(run)
            finally:
                if own_context:
                    await context.close()
                elif page is not None:
                    await page.close()
    except Exception as exc:
        raise AttemptFailed(exc, report) from exc


async def _capture(run: CaptureRun) -> CaptureOutcome:
    report = run.report
    retry = run.attempt > 0
    t_start = clock.now()  # start: right before navigation
    run.nav = await navigation.navigate(run)

    settle_timeout_ms = cfg.stabilization_timeout_ms(run.config, retry=retry)
    allow_interactive = run.nav.allow_interactive
    with report.phase("prepare"):
        run.prep = await prepare.prepare_page(run)
    with report.phase("readiness"):
        verdict = await _check_readiness(
            run, settle_timeout_ms, settle_timeout_ms, allow_interactive
        )
        if not verdict.stable and not retry:
            # Second chance on the same, already navigated and scrolled page
            # with the longer retry window — far cheaper than the full
            # re-navigation + re-scroll of a fresh retry.
            second_chance_ms = cfg.stabilization_timeout_ms(run.config, retry=True)
            logger.warning(
                "stabilization timed out for %s (settle_timeout_ms=%d), "
                "re-checking readiness in place (settle_timeout_ms=%d)",
                run.url,
                settle_timeout_ms,
                second_chance_ms,
            )
            verdict = await _check_readiness(
                run, second_chance_ms, settle_timeout_ms, allow_interactive
            )

    unstable = not verdict.stable
    warning_message: str | None = None
    if unstable:
        warning_message = UNSTABLE_DOM_WARNING
        logger.warning(
            "capturing %s with unstable DOM after stabilization timeout (retry=%s)",
            run.url,
            retry,
        )
        report.warn("dom_unstable", warning_message, reason=verdict.unstable_reason)

    with report.phase("snapshot"):
        await prepare.final_touches(run, unstable=unstable)
        shot = await snapshot.take_snapshot(run)
    duration = round(clock.now() - t_start, 3)  # stop: right after screenshot
    return CaptureOutcome(
        data=shot.data,
        duration=duration,
        report=report,
        captured_with_unstable_dom=unstable,
        warning_message=warning_message,
    )


async def _check_readiness(
    run: CaptureRun,
    window_ms: int,
    settle_timeout_ms: int,
    allow_interactive: bool,
) -> ReadinessResult:
    """Re-apply the page transforms and take a readiness verdict within
    *window_ms*. Missing visible assets / a blank page raise right here
    (logged with the attempt's base *settle_timeout_ms*)."""
    await prepare.reapply_transforms(run)
    verdict = await readiness.finalize(
        run, window_ms=window_ms, allow_interactive_ready_state=allow_interactive
    )
    if verdict.issues:
        message = network.append_network_diagnostics(
            run.scratch.get("asset_error") or "Visible page assets did not load",
            run.net,
        )
        _log_asset_failure(run, settle_timeout_ms, message)
        raise PageAssetLoadError(message, assets=verdict.assets)
    if verdict.blank:
        message = network.append_network_diagnostics(BLANK_PAGE_MESSAGE, run.net)
        _log_asset_failure(run, settle_timeout_ms, message)
        raise BlankPageError(message)
    return verdict


def _log_asset_failure(run: CaptureRun, settle_timeout_ms: int, message: str) -> None:
    logger.warning(
        "asset readiness failed for %s (retry=%s settle_timeout_ms=%d): %s",
        run.url,
        run.attempt > 0,
        settle_timeout_ms,
        message,
    )


def _unwrap(exc: Exception) -> tuple[BaseException, CaptureReport]:
    if isinstance(exc, AttemptFailed):
        return exc.error, exc.report
    return exc, CaptureReport()


async def capture_with_retries(
    *,
    browser: Browser,
    url: str,
    viewport: Mapping[str, int],
    config: EffectiveConfig,
    profile: DeviceProfile | None = None,
    context: BrowserContext | None = None,
    browser_provider: BrowserProvider | None = None,
    deadline_at: float | None = None,
    task_id: object = None,
) -> CaptureResult:
    """Capture with one retry on failure.

    When *context* is given the first attempt opens its page in that shared
    context; a retry always runs in a fresh isolated context. A Playwright
    timeout is not retried (the page will not succeed on retry). *task_id*
    only labels the log lines. *browser_provider* and *deadline_at* are
    accepted for the retry/deadline policy and not used yet.
    """
    if profile is None:
        profile = profiles.build_profile(
            config.browser, profiles.resolve_device_type(viewport)
        )
    width = viewport["width"]
    height = viewport["height"]
    last_error: BaseException | None = None
    outcome: CaptureOutcome | None = None
    report = CaptureReport()
    attempts = 0
    attempts_seconds = 0.0

    for attempt in range(cfg.RETRY_COUNT + 1):
        if attempt > 0:
            logger.warning(
                "task=%s retry %d/%d url=%s viewport=%dx%d",
                task_id,
                attempt,
                cfg.RETRY_COUNT,
                url,
                width,
                height,
            )
            await asyncio.sleep(cfg.RETRY_DELAY_S)

        attempts += 1
        attempt_started = clock.now()
        try:
            outcome = await run_attempt(
                browser,
                url,
                viewport,
                config,
                profile,
                context=context if attempt == 0 else None,
                attempt=attempt,
            )
        except Exception as exc:
            last_error, report = _unwrap(exc)
            if isinstance(last_error, PageAssetLoadError):
                logger.warning(
                    "task=%s visible asset load failure attempt=%d url=%s "
                    "viewport=%dx%d: %s",
                    task_id,
                    attempt + 1,
                    url,
                    width,
                    height,
                    last_error,
                )
            elif isinstance(last_error, PlaywrightTimeoutError):
                # Timeouts are deterministic — the page will not succeed on
                # retry. Break immediately to avoid wasting another timeout.
                logger.warning(
                    "task=%s timeout url=%s viewport=%dx%d — skipping retry",
                    task_id,
                    url,
                    width,
                    height,
                )
                break
            else:
                logger.error(
                    "task=%s attempt=%d url=%s viewport=%dx%d error=%s: %s",
                    task_id,
                    attempt + 1,
                    url,
                    width,
                    height,
                    type(last_error).__name__,
                    last_error,
                )
        else:
            report = outcome.report
            last_error = None
            break
        finally:
            attempts_seconds += clock.now() - attempt_started

    if last_error is not None or outcome is None:
        return CaptureResult(
            outcome=None,
            error=last_error,
            report=report,
            attempts=attempts,
            duration=round(attempts_seconds, 3),
        )
    return CaptureResult(
        outcome=outcome,
        error=None,
        report=report,
        attempts=attempts,
        duration=outcome.duration,
    )
