"""Capture pipeline.

run_attempt strings the stages together within the attempt's time budget —
navigation → prepare → readiness (+ an in-place second chance) → the recovery
ladder for broken assets → final touches → snapshot — and
capture_with_retries runs the attempts under the retry policy.
"""

import asyncio
import dataclasses
import inspect
import logging
import math
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

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
    BrowserUnavailableError,
    CaptureDeadlineExceeded,
    EvaluateTimeout,
    NavigationError,
    NavigationTimeoutError,
    PageBlockedError,
)
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import MAX_SAMPLES, CaptureReport
from app.screenshots.capture.types import (
    AssetIssue,
    CaptureOutcome,
    CaptureResult,
    CaptureRun,
    Deadline,
    ReadinessResult,
    SnapshotResult,
)

logger = logging.getLogger(__name__)

UNSTABLE_DOM_WARNING = (
    "Captured with unstable DOM after stabilization timeout. "
    "Content may be incomplete or still settling."
)
BROWSER_LOST_MESSAGE = "Browser connection lost; reconnect failed"

# Failures another attempt cannot fix: the page is unreachable or refuses us
# (a fresh context would also lose the protection cookies), or there is no
# browser to run it in.
NOT_RETRIED: tuple[type[Exception], ...] = (
    NavigationTimeoutError,
    NavigationError,
    PageBlockedError,
    BrowserUnavailableError,
)
# Error texts meaning the browser (or its connection) is gone.
_BROWSER_GONE_MARKERS = (
    "target page, context or browser has been closed",
    "browser has been closed",
    "connection closed",
)
_IMAGE_KINDS = frozenset({"img", "background"})
_RELOAD_IMAGE_CATEGORIES = frozenset({"blocked", "network"})

BrowserProvider = Callable[[], Awaitable[Browser | None]]


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def attempt_budget_s(config: EffectiveConfig) -> float:
    """Time budget of one attempt: clamp(4 x project timeout + 90 s, 240 s,
    480 s) with the config.ATTEMPT_BUDGET_* constants."""
    timeout_s = config.timeout / 1000
    budget = cfg.ATTEMPT_BUDGET_PER_TIMEOUT * timeout_s + cfg.ATTEMPT_BUDGET_BASE_S
    return float(min(max(budget, cfg.ATTEMPT_BUDGET_MIN_S), cfg.ATTEMPT_BUDGET_MAX_S))


def browser_version(browser: object) -> str | None:
    version = getattr(browser, "version", None)
    return version if isinstance(version, str) and version else None


def is_retryable(error: BaseException | None) -> bool:
    """Whether another attempt may fix *error*: everything except the
    NOT_RETRIED errors (a ScreenshotTimeoutError included — the retry loop
    still needs RETRY_MIN_REMAINING_S of the caller's deadline)."""
    return error is not None and not isinstance(error, NOT_RETRIED)


def browser_gone(error: BaseException | None) -> bool:
    """True when *error* says the browser or its connection died."""
    if error is None:
        return False
    if type(error).__name__ == "TargetClosedError":
        return True
    text = str(error).lower()
    return any(marker in text for marker in _BROWSER_GONE_MARKERS)


def _is_connected(browser: object) -> bool:
    try:
        return bool(getattr(browser, "is_connected", lambda: True)())
    except Exception:
        return False


def _seconds(value: float) -> float | None:
    return round(value, 1) if math.isfinite(value) else None


def _skip(run: CaptureRun, step: str, remaining_s: float, message: str, **details):
    """Record an optional recovery step that did not run (``outcome``
    "budget", the default) or whose result was not used (a reload that was
    "unavailable", "failed" or rendered "blank")."""
    details.setdefault("outcome", "budget")
    logger.info("%s: %s (step=%s, %s)", run.url, message, step, details or "")
    sample = {"step": step, "remaining_s": _seconds(remaining_s), **details}
    run.report.warn(
        "recovery_skipped",
        message,
        step=step,
        remaining_s=_seconds(remaining_s),
        samples=[sample],
        **details,
    )


async def _close_quietly(target: Any, what: str) -> None:
    """Close a page/context within config.CLOSE_TIMEOUT_S, never raising."""
    if target is None:
        return
    try:
        await asyncio.wait_for(target.close(), cfg.CLOSE_TIMEOUT_S)
    except Exception as exc:  # the TimeoutError of the bound included
        logger.debug("closing the capture %s failed: %s", what, exc)


# ---------------------------------------------------------------------------
# One attempt
# ---------------------------------------------------------------------------


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
    """One capture attempt: navigate, prepare, wait for readiness, recover
    broken assets, and return the PNG/JPEG bytes and the duration from
    page.goto → page.screenshot.

    By default a new context is created for the call so cookies/localStorage
    never leak between screenshots, and it is closed when the attempt ends.
    When a shared *context* is passed (per-URL context reuse, see
    profiles.open_capture_context) only a new page is opened in it, sized to
    *viewport* before navigation, and only that page is closed — the context's
    HTTP cache then serves the other viewports of the same URL. Closing is
    bounded by config.CLOSE_TIMEOUT_S and never fails the attempt.

    The attempt runs within *budget_s* seconds; past it the attempt fails with
    CaptureDeadlineExceeded (``phase``: the report phase that was running).
    Optional steps — the in-place second readiness chance (attempt 0 only),
    a recovery reload, a screenshot retry — run only when the budget has room
    for them and are otherwise recorded as ``recovery_skipped``.

    Broken assets never fail the attempt: they are retried in place, the page
    is reloaded once when that can help (blocked/unreachable stylesheets or
    images, a 5xx/429 main document), and what stays broken becomes
    ``styles_missing`` / ``assets_missing`` warnings. A blank page fails with
    BlankPageError.

    Any failure is raised as AttemptFailed carrying the error and the
    attempt's report; the builtin TimeoutError never leaves this function.

    Duration intentionally excludes:
    - browser.new_context() / context.close() overhead
    - semaphore wait time
    - retry sleep time
    - DB write time
    """
    report = CaptureReport()
    if profile is None:
        profile = profiles.build_profile(
            config.browser,
            profiles.resolve_device_type(viewport),
            browser_version=browser_version(browser),
        )
    report.meta["device"] = profile.meta()
    report.meta["browser_version"] = browser_version(browser)
    deadline = Deadline.start(budget_s)
    expiry = asyncio.timeout(budget_s if math.isfinite(budget_s) else None)
    own_context = context is None
    page: Page | None = None
    try:
        async with expiry:
            with report.phase("total"):
                if context is None:
                    with report.phase("context"):
                        context = await profiles.open_capture_context(
                            browser, viewport, profile
                        )
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
    except Exception as exc:
        error: BaseException = exc
        if expiry.expired():
            error = CaptureDeadlineExceeded(phase=report.current_phase)
            logger.warning(
                "capture of %s ran out of its %.0f s budget (attempt=%d phase=%s)",
                url,
                budget_s,
                attempt,
                report.current_phase,
            )
        elif isinstance(exc, TimeoutError):
            # A stage let a bare asyncio timeout escape; keep it away from the
            # re-capture job's own `except TimeoutError`.
            error = EvaluateTimeout(
                str(exc) or f"A page wait timed out (phase: {report.current_phase})"
            )
        raise AttemptFailed(error, report) from exc
    finally:
        if own_context:
            await _close_quietly(context, "context")
        else:
            await _close_quietly(page, "page")


async def _capture(run: CaptureRun) -> CaptureOutcome:
    report = run.report
    t_start = clock.now()  # start: right before navigation
    try:
        run.nav = await navigation.navigate(run)
    except PlaywrightTimeoutError as exc:
        # The goto itself timed out on its lightest wait: the page is
        # unreachable, which another attempt will not change.
        raise NavigationTimeoutError(str(exc)) from exc

    with report.phase("prepare"):
        run.prep = await prepare.prepare_page(run)
    with report.phase("readiness"):
        verdict = await _readiness(run)
        _raise_if_blank(run, verdict)

    state = await _recover(run, verdict, spent_s=clock.now() - t_start)

    unstable = not state.verdict.stable
    warning_message: str | None = None
    if unstable:
        warning_message = UNSTABLE_DOM_WARNING
        logger.warning(
            "capturing %s with unstable DOM after stabilization timeout (retry=%s)",
            run.url,
            run.attempt > 0,
        )
        report.warn(
            "dom_unstable", warning_message, reason=state.verdict.unstable_reason
        )
    await _report_status(run)
    _report_assets(run, state)

    shot = state.fallback
    if shot is None:
        with report.phase("snapshot"):
            await prepare.final_touches(run, unstable=unstable)
            shot = await snapshot.take_snapshot(run, unstable=unstable)
    duration = round(clock.now() - t_start, 3)  # stop: right after the snapshot
    return CaptureOutcome(
        data=shot.data,
        duration=duration,
        report=report,
        captured_with_unstable_dom=unstable,
        warning_message=warning_message,
        snapshot=shot,
    )


# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------


async def _verdict(run: CaptureRun, window_ms: int) -> ReadinessResult:
    """Re-apply the page transforms and take a readiness verdict within
    *window_ms*."""
    await prepare.reapply_transforms(run)
    allow_interactive = run.nav.allow_interactive if run.nav is not None else False
    return await readiness.finalize(
        run, window_ms=window_ms, allow_interactive_ready_state=allow_interactive
    )


async def _readiness(run: CaptureRun) -> ReadinessResult:
    """The readiness verdict; on attempt 0 an unsettled page gets a second,
    longer check in place (budget permitting) — far cheaper than the full
    re-navigation + re-scroll of a retry, which gets the longer window right
    away."""
    window_ms = cfg.stabilization_timeout_ms(run.config, retry=run.attempt > 0)
    verdict = await _verdict(run, window_ms)
    second_chance = False
    if not verdict.stable and run.attempt == 0:
        chance_ms = cfg.stabilization_timeout_ms(run.config, retry=True)
        remaining_s = run.deadline.remaining_s()
        if remaining_s < chance_ms / 1000 + cfg.SECOND_CHANCE_SPARE_S:
            _skip(
                run,
                "second_chance",
                remaining_s,
                "Second readiness check skipped: not enough time left",
            )
        else:
            logger.warning(
                "stabilization timed out for %s (settle_timeout_ms=%d), "
                "re-checking readiness in place (settle_timeout_ms=%d)",
                run.url,
                window_ms,
                chance_ms,
            )
            verdict = await _verdict(run, chance_ms)
            second_chance = True
    run.report.set_meta("readiness", second_chance=second_chance)
    return verdict


def _raise_if_blank(run: CaptureRun, verdict: ReadinessResult) -> None:
    if not verdict.blank:
        return
    message = network.append_network_diagnostics(BLANK_PAGE_MESSAGE, run.net)
    logger.warning(
        "page rendered blank for %s (retry=%s): %s",
        run.url,
        run.attempt > 0,
        message,
    )
    raise BlankPageError(message)


# ---------------------------------------------------------------------------
# Recovery ladder
# ---------------------------------------------------------------------------


@dataclass
class _Recovery:
    """Where the recovery ladder left the attempt."""

    verdict: ReadinessResult  # the verdict of the page the image will show
    issues: tuple[AssetIssue, ...]  # what is still broken on it
    in_place: int = 0  # resolved by readiness.retry_in_place
    after_reload: int = 0  # resolved by the reload
    recovered: list[AssetIssue] = field(default_factory=list)  # both kinds
    # The snapshot taken before a reload that did not work out; the image.
    fallback: SnapshotResult | None = None


def _issue_key(issue: AssetIssue) -> tuple[str, str]:
    return issue.kind, issue.url


def _resolved(
    before: Iterable[AssetIssue], after: Iterable[AssetIssue]
) -> list[AssetIssue]:
    """The issues of *before* that are gone from *after*."""
    still = {_issue_key(issue) for issue in after}
    return [issue for issue in before if _issue_key(issue) not in still]


def reload_reason(issues: Iterable[AssetIssue], nav: object) -> str | None:
    """Why reloading the page could help, or None: the main document asked
    for it (5xx/429, ``nav.needs_reload``), a stylesheet is still broken for
    another reason than a plain 404/410, or an image is blocked/unreachable.
    Plain missing (404/410) assets never warrant a reload."""
    issues = tuple(issues)
    if getattr(nav, "needs_reload", False):
        return "http_status"
    if any(i.kind == "stylesheet" and i.category != "missing" for i in issues):
        return "styles_missing"
    if any(
        i.kind in _IMAGE_KINDS and i.category in _RELOAD_IMAGE_CATEGORIES
        for i in issues
    ):
        return "assets_blocked"
    return None


async def _recover(
    run: CaptureRun, verdict: ReadinessResult, *, spent_s: float
) -> _Recovery:
    """Retry broken assets in place, then reload once when that can help and
    the budget allows (*spent_s*: time spent so far on navigation, prepare
    and readiness — a reload repeats them)."""
    state = _Recovery(verdict=verdict, issues=tuple(verdict.issues))
    if state.issues:
        with run.report.phase("recovery"):
            still = tuple(await readiness.retry_in_place(run, state.issues))
        resolved = _resolved(state.issues, still)
        state.in_place = len(resolved)
        state.recovered.extend(resolved)
        state.issues = still
    reason = reload_reason(state.issues, run.nav)
    if reason is None:
        return state
    needed_s = cfg.RELOAD_COST_FACTOR * spent_s
    remaining_s = run.deadline.remaining_s()
    if remaining_s < needed_s:
        _skip(
            run,
            "reload",
            remaining_s,
            "Reload skipped: not enough time left",
            reason=reason,
            needed_s=_seconds(needed_s),
        )
        return state
    await _reload(run, state, reason)
    return state


async def _reload(run: CaptureRun, state: _Recovery, reason: str) -> None:
    """Reload the page in place and take a new verdict; when the reload is
    unavailable, fails, runs out of budget or renders blank, the snapshot
    taken right before it becomes the image."""
    report = run.report
    unstable = not state.verdict.stable
    fallback_report = CaptureReport()
    with report.phase("snapshot"):
        await prepare.final_touches(run, unstable=unstable)
        fallback = await snapshot.take_snapshot(
            dataclasses.replace(run, report=fallback_report), unstable=unstable
        )
    nav, prep = run.nav, run.prep
    remaining_s = run.deadline.remaining_s()
    limit_s = remaining_s - cfg.RELOAD_RESERVE_S if math.isfinite(remaining_s) else None
    expiry = asyncio.timeout(limit_s)
    outcome: str
    error: str | None = None
    try:
        async with expiry:
            with report.phase("reload"):
                verdict, issues = await _reload_and_check(run, reason)
    except NotImplementedError:
        outcome = "unavailable"
    except Exception as exc:
        outcome = "budget" if expiry.expired() else "failed"
        error = f"{type(exc).__name__}: {exc}" if outcome == "failed" else None
        logger.warning("recovery reload of %s failed (%s): %s", run.url, outcome, exc)
    else:
        if not verdict.blank:
            resolved = _resolved(state.issues, issues)
            state.after_reload = len(resolved)
            state.recovered.extend(resolved)
            state.verdict = verdict
            state.issues = issues
            report.warn(
                "page_reloaded",
                "The page was reloaded to recover broken resources",
                reason=reason,
            )
            return
        outcome = "blank"
        logger.warning("%s rendered blank after the recovery reload", run.url)

    # Keep the page as it was before the reload.
    run.nav, run.prep = nav, prep
    _adopt(report, fallback_report)
    state.fallback = fallback
    messages = {
        "unavailable": "Reload not available; captured the page as it was",
        "failed": "Reload failed; captured the page as it was before it",
        "budget": "Reload ran out of time; captured the page as it was before it",
        "blank": "The reloaded page was blank; captured the page as it was before",
    }
    details: dict[str, Any] = {"reason": reason, "outcome": outcome}
    if error is not None:
        details["error"] = error
    _skip(run, "reload", run.deadline.remaining_s(), messages[outcome], **details)


async def _reload_and_check(
    run: CaptureRun, reason: str
) -> tuple[ReadinessResult, tuple[AssetIssue, ...]]:
    run.nav = await navigation.reload(run, reason=reason)
    run.prep = await prepare.prepare_page(run)
    verdict = await _verdict(
        run, cfg.stabilization_timeout_ms(run.config, retry=run.attempt > 0)
    )
    issues = tuple(verdict.issues)
    if issues and not verdict.blank:
        issues = tuple(await readiness.retry_in_place(run, issues))
    return verdict, issues


def _adopt(report: CaptureReport, other: CaptureReport) -> None:
    """Merge the warnings and meta *other* recorded into *report*."""
    for warning in other.warnings:
        details = {k: v for k, v in warning.details.items() if k != "count"}
        report.warn(warning.code, warning.message, level=warning.level, **details)
    for section, values in other.meta.items():
        if isinstance(values, dict) and isinstance(report.meta.get(section, {}), dict):
            report.set_meta(section, **values)
        else:
            report.meta[section] = values


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


async def _report_status(run: CaptureRun) -> None:
    """navigation.report_status (main-document status / redirect warnings),
    once the page the image shows is settled."""
    report_status = getattr(navigation, "report_status", None)
    if report_status is None:
        return
    result = report_status(run)
    if inspect.isawaitable(result):
        await result


def _issue_reason(issue: AssetIssue) -> str:
    if issue.reason:
        return issue.reason
    if issue.status is not None:
        return f"HTTP {issue.status}"
    return issue.category


def _issue_sample(issue: AssetIssue) -> dict[str, Any]:
    return {
        "kind": issue.kind,
        "url": issue.url,
        "status": issue.status,
        "reason": issue.reason,
        "category": issue.category,
    }


def _report_assets(run: CaptureRun, state: _Recovery) -> None:
    """Recovered resources → ``assets_recovered``; what is still broken →
    ``styles_missing`` / ``assets_missing`` (the capture stays ok). Pending
    images are left to readiness's ``assets_pending`` when it recorded one."""
    report = run.report
    recovered = state.in_place + state.after_reload
    if recovered:
        report.warn(
            "assets_recovered",
            f"{recovered} broken resource(s) loaded after a retry",
            in_place=state.in_place,
            after_reload=state.after_reload,
            samples=[_issue_sample(issue) for issue in state.recovered],
        )
    styles = [issue for issue in state.issues if issue.kind == "stylesheet"]
    pending_reported = report.warning("assets_pending") is not None
    assets = [
        issue
        for issue in state.issues
        if issue.kind != "stylesheet"
        and not (issue.category == "pending" and pending_reported)
    ]
    if styles:
        report.warn(
            "styles_missing",
            f"{len(styles)} stylesheet(s) did not load; the page may look unstyled",
            urls=[issue.url for issue in styles[:MAX_SAMPLES]],
            reasons=[_issue_reason(issue) for issue in styles[:MAX_SAMPLES]],
            samples=[_issue_sample(issue) for issue in styles],
        )
    if assets:
        kinds: dict[str, int] = {}
        for issue in assets:
            kinds[issue.kind] = kinds.get(issue.kind, 0) + 1
        breakdown = ", ".join(f"{count} {kind}" for kind, count in kinds.items())
        message = f"{len(assets)} visible asset(s) did not load: {breakdown}"
        # One warn per asset: the merged warning counts them and keeps the
        # first MAX_SAMPLES as samples.
        for issue in assets:
            report.warn("assets_missing", message, samples=[_issue_sample(issue)])


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------


def _unwrap(exc: Exception) -> tuple[BaseException, CaptureReport]:
    error, report = (
        (exc.error, exc.report) if isinstance(exc, AttemptFailed) else (exc, None)
    )
    if isinstance(error, TimeoutError):
        error = EvaluateTimeout(str(error) or "A page wait timed out")
    return error, report if report is not None else CaptureReport()


async def _provide(provider: BrowserProvider, task_id: object, url: str) -> Any:
    try:
        return await provider()
    except Exception:
        logger.exception("task=%s browser provider failed for url=%s", task_id, url)
        return None


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
    """Capture with up to config.RETRY_COUNT retries, config.RETRY_DELAY_S
    apart.

    Not retried: NavigationTimeoutError, NavigationError, PageBlockedError
    (and BrowserUnavailableError); everything else is — BlankPageError,
    ScreenshotTimeoutError, EvaluateTimeout, CaptureDeadlineExceeded,
    Playwright errors. Each attempt gets attempt_budget_s(config), never past
    *deadline_at* (an ``asyncio`` loop time); with *deadline_at* a retry only
    starts with config.RETRY_MIN_REMAINING_S left.

    Before a retry — and before the first attempt when the browser is no
    longer connected — *browser_provider* (when given) supplies the browser:
    the same one, a reconnected one, or None, which ends the capture with
    BrowserUnavailableError. The *profile* is kept across a browser swap.
    When *context* is given the first attempt opens its page in that shared
    context; a retry, or an attempt on a swapped browser, runs in a fresh
    isolated context. *task_id* only labels the log lines.

    The result carries the last attempt's report with ``attempts``,
    ``device``, ``browser_version`` and, for a failure, ``failure`` {class,
    phase} meta. No exception but cancellation leaves this function.
    """
    if profile is None:
        profile = profiles.build_profile(
            config.browser,
            profiles.resolve_device_type(viewport),
            browser_version=browser_version(browser),
        )
    width = viewport["width"]
    height = viewport["height"]
    loop = asyncio.get_running_loop()
    last_error: BaseException | None = None
    failed_outside_attempt = False
    outcome: CaptureOutcome | None = None
    report = CaptureReport()
    attempts = 0
    attempts_seconds = 0.0

    for attempt in range(cfg.RETRY_COUNT + 1):
        if attempt > 0:
            if not is_retryable(last_error):
                logger.warning(
                    "task=%s url=%s viewport=%dx%d — %s is not retried",
                    task_id,
                    url,
                    width,
                    height,
                    type(last_error).__name__,
                )
                break
            if deadline_at is not None:
                left_s = deadline_at - loop.time() - cfg.RETRY_DELAY_S
                if left_s < cfg.RETRY_MIN_REMAINING_S:
                    logger.warning(
                        "task=%s url=%s viewport=%dx%d — no retry, only %.0f s "
                        "left before the deadline",
                        task_id,
                        url,
                        width,
                        height,
                        max(left_s, 0.0),
                    )
                    break
            logger.warning(
                "task=%s retry %d/%d url=%s viewport=%dx%d%s",
                task_id,
                attempt,
                cfg.RETRY_COUNT,
                url,
                width,
                height,
                " (browser connection lost)" if browser_gone(last_error) else "",
            )
            await asyncio.sleep(cfg.RETRY_DELAY_S)

        if browser_provider is not None and (attempt > 0 or not _is_connected(browser)):
            replacement = await _provide(browser_provider, task_id, url)
            if replacement is None:
                logger.error(
                    "task=%s url=%s viewport=%dx%d — browser unavailable",
                    task_id,
                    url,
                    width,
                    height,
                )
                last_error = BrowserUnavailableError(BROWSER_LOST_MESSAGE)
                failed_outside_attempt = True
                break
            if replacement is not browser:
                context = None  # the shared context lived in the old browser
                browser = replacement

        budget_s = attempt_budget_s(config)
        if deadline_at is not None:
            budget_s = min(budget_s, deadline_at - loop.time())
            if budget_s <= 0:
                last_error = CaptureDeadlineExceeded(
                    message="Capture deadline passed before the attempt started"
                )
                failed_outside_attempt = True
                break

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
                budget_s=budget_s,
            )
        except Exception as exc:
            outcome = None
            last_error, report = _unwrap(exc)
            failed_outside_attempt = False
            logger.warning(
                "task=%s attempt=%d url=%s viewport=%dx%d failed (phase=%s) "
                "error=%s: %s",
                task_id,
                attempt + 1,
                url,
                width,
                height,
                report.current_phase,
                type(last_error).__name__,
                last_error,
            )
        else:
            report = outcome.report
            last_error = None
            break
        finally:
            attempts_seconds += clock.now() - attempt_started

    report.meta["attempts"] = attempts
    report.meta["device"] = profile.meta()
    report.meta["browser_version"] = browser_version(browser)
    if last_error is not None or outcome is None:
        if last_error is None:  # pragma: no cover — no attempt ran at all
            last_error = CaptureDeadlineExceeded(message="No capture attempt ran")
        report.meta["failure"] = {
            "class": type(last_error).__name__,
            "phase": None if failed_outside_attempt else report.current_phase,
        }
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
