import asyncio
import logging
import time
import uuid
from datetime import datetime
from typing import Any

from playwright.async_api import async_playwright
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import User  # noqa: F401 — required for SQLAlchemy FK resolution
from app.core.config import settings
from app.projects.models import (
    ProjectConfig,
    ViewportEntry,
    normalize_browser,
    normalize_browsers,
)
from app.screenshots.models import Screenshot
from app.screenshots.service import (
    EffectiveConfig,
    capture_screenshot,
    open_capture_context,
)
from app.screenshots.storage import build_file_path, generate_signed_url
from app.settings.service import get_application_settings, resolve_setting
from app.tasks.limiter import CaptureLimiter
from app.tasks.models import Task
from app.tasks.service import _clear_user_cancel_marker, is_user_cancel_requested

logger = logging.getLogger(__name__)

# Browsers that need a named channel instead of a separate Playwright browser type
_CHANNEL_BROWSERS: dict[str, str] = {
    "chrome": "chrome",
    "msedge": "msedge",
}
# Per-browser ports on the remote browser server. Each browser/channel runs as its
# own `launchServer` instance — see browser-server.mjs in the sibling repo
# everneu-screenshots-browsers, which MUST keep this exact mapping (manual
# contract, documented in both READMEs). The worker connects to
# f"{BROWSER_WS_ENDPOINT}:{port}/". Port 3000 (the retired chromium server) is
# no longer used; legacy "chromium" names are aliased to chrome.
_BROWSER_WS_PORTS: dict[str, int] = {
    "chrome": 3001,
    "msedge": 3002,
    "firefox": 3003,
    "webkit": 3004,
}
_CHROMIUM_LAUNCH_ARGS: tuple[str, ...] = (
    "--disable-gpu",
    "--disable-extensions",
    "--disable-dev-shm-usage",
    "--no-default-browser-check",
    "--no-sandbox",
    "--no-first-run",
    "--disable-default-apps",
)
_PROGRESS_FLUSH_EVERY = 5
_JOB_TIMEOUT_GRACE_SECONDS = 1.0
_TIMEOUT_PLACEHOLDER_ERROR = "Task timed out before this capture completed."
# Remote connect() fails immediately on a refused socket (it does not retry), so
# a task started right after the browser container (re)starts needs a few
# attempts of its own.
_CONNECT_ATTEMPTS = 3
_CONNECT_RETRY_DELAY_S = 1.0


def _build_effective_config(
    task: Task,
    config: ProjectConfig,
    screenshot_format: str,
    screenshot_quality: int,
) -> EffectiveConfig:
    """Merge task overrides with project and application defaults.

    Browser overrides remain task-scoped. Screenshot format and quality come
    from global application settings. Timeout remains project-scoped.
    """
    return EffectiveConfig(
        browser=normalize_browsers(
            task.override_browsers
            or ([task.override_browser] if task.override_browser else None)
            or config.default_browsers
        )[0],
        fmt=screenshot_format,
        quality=screenshot_quality,
        wait_until=config.wait_until,
        timeout=config.timeout,
    )


def _resolve_browser_type(pw: Any, browser_name: str) -> tuple[Any, str | None, bool]:
    """Map *browser_name* to a Playwright browser type.

    Returns ``(browser_type, channel, use_chromium_launch_args)``. Channel
    browsers (chrome/msedge) resolve to the chromium type with a ``channel``;
    unknown names fall back to chrome.
    """
    channel = _CHANNEL_BROWSERS.get(browser_name)
    if channel:
        return pw.chromium, channel, True
    if browser_name == "firefox":
        return pw.firefox, None, False
    if browser_name == "webkit":
        return pw.webkit, None, False
    logger.warning("unknown browser %r, falling back to chrome", browser_name)
    return pw.chromium, _CHANNEL_BROWSERS["chrome"], True


async def _launch_browser(pw: Any, browser_name: str) -> Any:
    """Obtain a single Playwright browser instance for *browser_name*.

    When ``BROWSER_WS_ENDPOINT`` is set (a base host like ``ws://browser``),
    connect to the per-browser ``launchServer`` on the remote browser host: the
    port is resolved from ``_BROWSER_WS_PORTS``, the
    ``BROWSER_WS_PATH`` is appended, and the channel + launch args are already
    baked into that server, so chrome/msedge render via their real branded
    binaries. Connection is retried a few times (the server may still be
    starting). Otherwise launch a local browser as before. Raises on
    connect/launch failure. A legacy ``chromium`` name (old screenshots being
    re-captured) is served by chrome.
    """
    browser_name = normalize_browser(browser_name)
    bt, channel, use_chromium_launch_args = _resolve_browser_type(pw, browser_name)

    base = settings.BROWSER_WS_ENDPOINT
    if base:
        port = _BROWSER_WS_PORTS.get(browser_name, _BROWSER_WS_PORTS["chrome"])
        ws_path = settings.BROWSER_WS_PATH.strip("/")
        endpoint = f"{base.rstrip('/')}:{port}/{ws_path}"
        last_exc: Exception | None = None
        for attempt in range(_CONNECT_ATTEMPTS):
            try:
                return await bt.connect(
                    endpoint, timeout=settings.BROWSER_CONNECT_TIMEOUT_MS
                )
            except Exception as exc:
                last_exc = exc
                if attempt < _CONNECT_ATTEMPTS - 1:
                    logger.warning(
                        "browser %s connect attempt %d/%d failed: %s",
                        browser_name, attempt + 1, _CONNECT_ATTEMPTS, exc,
                    )
                    await asyncio.sleep(_CONNECT_RETRY_DELAY_S)
        assert last_exc is not None
        raise last_exc

    launch_kwargs: dict[str, object] = {"headless": True}
    if use_chromium_launch_args:
        launch_kwargs["args"] = list(_CHROMIUM_LAUNCH_ARGS)
    if channel:
        launch_kwargs["channel"] = channel

    return await bt.launch(**launch_kwargs)


def _capture_limiter(ctx: dict, sem_limit: int) -> CaptureLimiter:
    """The worker-wide capture limiter from the ARQ ctx, resized to *sem_limit*.

    Falls back to a job-local limiter when the job runs without the worker's
    startup hook (unit tests).
    """
    limiter = ctx.get("capture_limiter")
    if limiter is None:
        limiter = CaptureLimiter(sem_limit)
    limiter.set_limit(sem_limit)
    return limiter


def _group_combos(
    combos: list[tuple[str, dict, str]],
) -> list[tuple[str, str, list[dict]]]:
    """Group combos into (url, browser, [viewports]) in first-seen order."""
    groups: dict[tuple[str, str], list[dict]] = {}
    for url, viewport, browser_name in combos:
        groups.setdefault((url, browser_name), []).append(viewport)
    return [(url, browser, vps) for (url, browser), vps in groups.items()]


async def _close_quietly(context: Any | None) -> None:
    if context is None:
        return
    try:
        await context.close()
    except Exception as exc:
        logger.debug("browser context close failed: %s", exc)


async def run_screenshot_task(ctx: dict, task_id: str) -> None:
    """ARQ job: run all screenshots for a task concurrently.

    Steps:
    1. Load Task + ProjectConfig; mark task as "running".
    2. Build flat (url x viewport x browser) combination list.
    3. Launch Playwright browser(s).
    4. Run the combinations through a consumer pool; every capture holds a slot
       of the worker-wide CaptureLimiter, so concurrent tasks share SEM_LIMIT.
    5. Write progress to DB after each screenshot.
    6. Set final task status and completed_at.
    """
    engine = ctx["db_engine"]
    tid = uuid.UUID(task_id)
    job_started_monotonic = time.monotonic()

    # ------------------------------------------------------------------
    # Load Task + ProjectConfig, then immediately mark as running.
    # Capture all needed scalar values before the session closes to
    # avoid DetachedInstanceError on expired SQLAlchemy attributes.
    # ------------------------------------------------------------------
    async with AsyncSession(engine) as session:
        task = await session.get(Task, tid)
        if not task:
            logger.error("run_screenshot_task: task_id=%s not found", task_id)
            return
        if task.status == "cancelled":
            if task.completed_at is None:
                task.completed_at = datetime.utcnow()
                session.add(task)
                await session.commit()
            logger.info("run_screenshot_task: task_id=%s already cancelled", task_id)
            return

        config_result = await session.execute(
            select(ProjectConfig).where(ProjectConfig.project_id == task.project_id)
        )
        config = config_result.scalars().first()
        if not config:
            logger.error(
                "run_screenshot_task: ProjectConfig missing for project=%s task=%s",
                task.project_id, task_id,
            )
            task.status = "failed"
            session.add(task)
            await session.commit()
            return

        app_settings = await get_application_settings(session)
        effective = _build_effective_config(
            task,
            config,
            app_settings.screenshot_format,
            app_settings.screenshot_quality,
        )
        # Effective tuning values (DB override > .env). Resolved while the session
        # is open and kept as plain ints for use after it closes / in closures.
        sem_limit = max(int(resolve_setting(app_settings, "sem_limit")), 1)
        job_timeout_seconds = max(
            int(resolve_setting(app_settings, "task_job_timeout_seconds")), 0
        )
        reuse_context = bool(resolve_setting(app_settings, "reuse_browser_context"))
        viewports: list[dict] = task.override_viewports or config.viewports

        # Build the flat (url, viewport, browser) combination list.
        # Each viewport may declare a per-viewport browser override (browsers: [...]).
        # When browsers is null/absent the project-level default browser is used.
        # Effective browser list:
        # override_browsers > legacy override_browser > project defaults.
        task_browsers: list[str] = normalize_browsers(
            task.override_browsers
            or ([task.override_browser] if task.override_browser else None)
            or config.default_browsers
        )

        combos: list[tuple[str, dict, str]] = []
        for url in task.target_urls:
            for vp in viewports:
                vp_entry = ViewportEntry(**vp)
                if vp_entry.browsers:
                    # Per-viewport browser override takes highest priority
                    browsers_to_use = vp_entry.browsers
                else:
                    # Task-level (or project default) browser list
                    browsers_to_use = task_browsers
                for browser_name in browsers_to_use:
                    combos.append((url, vp, browser_name))

        # Capture scalars we need in closures after this session closes
        project_id = task.project_id
        total = len(combos)

        task.status = "running"
        task.started_at = datetime.utcnow()
        session.add(task)
        await session.commit()

    unique_browsers = {b for _, _, b in combos}
    logger.info(
        "task=%s starting: combinations=%d browsers=%s",
        task_id, total, sorted(unique_browsers),
    )

    limiter = _capture_limiter(ctx, sem_limit)
    # Queue items are (url, browser, [viewports]). With context reuse all
    # viewports of one URL in one browser form a group that shares a browser
    # context (and its HTTP cache); otherwise every combo is its own group.
    if reuse_context:
        work_items = _group_combos(combos)
    else:
        work_items = [(url, browser, [vp]) for url, vp, browser in combos]
    worker_count = sem_limit
    queue: asyncio.Queue[tuple[str, str, list[dict]] | None] = asyncio.Queue(
        maxsize=max(sem_limit * 2, 1)
    )
    progress_done = 0
    progress_failed = 0
    is_cancelled = False
    last_flushed_total = 0
    counter_lock = asyncio.Lock()
    browser_instances: dict[str, Any] = {}
    reconnect_lock = asyncio.Lock()
    browser_connection_lost = False

    async def _persist_progress(force: bool = False) -> None:
        nonlocal last_flushed_total, is_cancelled
        current_total = progress_done + progress_failed
        if not force and current_total - last_flushed_total < _PROGRESS_FLUSH_EVERY:
            return
        async with AsyncSession(engine) as session:
            t = await session.get(Task, tid)
            if not t:
                is_cancelled = True
                logger.info("task=%s missing from DB, stopping", task_id)
                return
            if t.status == "cancelled":
                is_cancelled = True
                logger.info("task=%s detected cancellation, stopping", task_id)
                return
            t.progress_done = progress_done
            t.progress_failed = progress_failed
            session.add(t)
            await session.commit()
            last_flushed_total = current_total

    async def _refresh_cancelled_status() -> bool:
        nonlocal is_cancelled
        if is_cancelled:
            return True
        async with AsyncSession(engine) as session:
            t = await session.get(Task, tid)
            if not t:
                is_cancelled = True
                logger.info("task=%s missing from DB, stopping", task_id)
            elif t.status == "cancelled":
                is_cancelled = True
                logger.info("task=%s detected cancellation, stopping", task_id)
        return is_cancelled

    async def _mark_cancelled() -> None:
        async with AsyncSession(engine) as session:
            t = await session.get(Task, tid)
            if t:
                t.status = "cancelled"
                t.progress_done = progress_done
                t.progress_failed = progress_failed
                t.completed_at = datetime.utcnow()
                session.add(t)
                await session.commit()

    def _combo_key(
        url: str,
        viewport: dict,
        browser_name: str,
    ) -> tuple[str, int, int, str]:
        return (
            url,
            int(viewport["width"]),
            int(viewport["height"]),
            browser_name,
        )

    def _is_job_timeout_cancel() -> bool:
        timeout_seconds = job_timeout_seconds
        if timeout_seconds <= 0:
            return False
        elapsed = time.monotonic() - job_started_monotonic
        return elapsed >= timeout_seconds - _JOB_TIMEOUT_GRACE_SECONDS

    async def _was_user_cancel_requested() -> bool:
        try:
            return await is_user_cancel_requested(tid)
        except Exception as exc:
            logger.warning("task=%s cancel marker lookup failed: %s", task_id, exc)
            return False

    async def _clear_cancel_marker() -> None:
        try:
            await _clear_user_cancel_marker(tid)
        except Exception as exc:
            logger.warning("task=%s cancel marker cleanup failed: %s", task_id, exc)

    async def _mark_failed_after_interrupt() -> None:
        async with AsyncSession(engine) as session:
            t = await session.get(Task, tid)
            if t:
                t.status = "failed"
                t.progress_done = progress_done
                t.progress_failed = progress_failed
                t.completed_at = datetime.utcnow()
                session.add(t)
                await session.commit()

    async def _mark_timed_out() -> None:
        nonlocal progress_done, progress_failed
        async with AsyncSession(engine) as session:
            t = await session.get(Task, tid)
            if not t:
                return

            result = await session.exec(
                select(Screenshot).where(Screenshot.task_id == tid)
            )
            screenshots = list(result.all())
            existing = {
                (s.url, int(s.width), int(s.height), s.browser)
                for s in screenshots
            }

            for url, viewport, browser_name in combos:
                key = _combo_key(url, viewport, browser_name)
                if key in existing:
                    continue
                _, width, height, _ = key
                relative_path = build_file_path(
                    project_id,
                    tid,
                    url,
                    width,
                    height,
                    browser_name,
                    effective.fmt,
                )
                filename = "/".join(relative_path.split("/")[2:])
                session.add(
                    Screenshot(
                        task_id=tid,
                        url=url,
                        browser=browser_name,
                        width=width,
                        height=height,
                        image_width=None,
                        image_height=None,
                        file_format=effective.fmt,
                        file_path=relative_path,
                        signed_url=generate_signed_url(str(tid), filename),
                        status="failed",
                        captured_with_unstable_dom=False,
                        error_message=_TIMEOUT_PLACEHOLDER_ERROR,
                        duration_seconds=0.0,
                        created_at=datetime.utcnow(),
                    )
                )
                existing.add(key)

            result = await session.exec(
                select(Screenshot).where(Screenshot.task_id == tid)
            )
            screenshots = list(result.all())
            progress_done = sum(1 for s in screenshots if s.status == "ok")
            progress_failed = sum(1 for s in screenshots if s.status == "failed")

            t.status = "timed_out"
            t.progress_total = progress_done + progress_failed
            t.progress_done = progress_done
            t.progress_failed = progress_failed
            t.completed_at = datetime.utcnow()
            session.add(t)
            await session.commit()

    async def _ensure_browser(browser_name: str) -> Any | None:
        """Return a connected Browser for *browser_name*.

        If the (remote) connection dropped, reconnect once under a lock so
        concurrent consumers don't stampede the browser server. Returns None
        after a failed reconnect — the whole task is then abandoned instead of
        grinding every remaining combo through guaranteed capture failures.
        """
        nonlocal browser_connection_lost
        browser = browser_instances[browser_name]
        if browser.is_connected():
            return browser
        async with reconnect_lock:
            browser = browser_instances[browser_name]
            if browser.is_connected():
                return browser
            if browser_connection_lost:
                return None
            logger.warning(
                "task=%s browser %s disconnected, reconnecting",
                task_id, browser_name,
            )
            try:
                browser_instances[browser_name] = await _launch_browser(
                    pw, browser_name
                )
            except Exception as exc:
                logger.error(
                    "task=%s browser %s reconnect failed: %s",
                    task_id, browser_name, exc,
                )
                browser_connection_lost = True
                return None
            return browser_instances[browser_name]

    async def _capture_one(
        url: str,
        viewport: dict,
        browser_name: str,
        browser: Any,
        context: Any | None,
    ) -> str:
        nonlocal progress_done, progress_failed
        combo_config = EffectiveConfig(
            browser=browser_name,
            fmt=effective.fmt,
            quality=effective.quality,
            wait_until=effective.wait_until,
            timeout=effective.timeout,
        )

        async with AsyncSession(engine) as session:
            result = await capture_screenshot(
                browser=browser,
                session=session,
                task_id=tid,
                project_id=project_id,
                url=url,
                viewport=viewport,
                config=combo_config,
                context=context,
            )

        async with counter_lock:
            if result.status == "ok":
                progress_done += 1
            else:
                progress_failed += 1
            snap_done = progress_done
            snap_failed = progress_failed
            should_flush = (
                (snap_done + snap_failed) - last_flushed_total
                >= _PROGRESS_FLUSH_EVERY
            )
        if should_flush:
            await _persist_progress()

        logger.debug(
            "task=%s progress=%d/%d url=%s viewport=%dx%d browser=%s status=%s",
            task_id,
            snap_done + snap_failed,
            total,
            url,
            viewport["width"],
            viewport["height"],
            browser_name,
            result.status,
        )
        return result.status

    async def _run_group(url: str, browser_name: str, group: list[dict]) -> None:
        context: Any | None = None
        context_browser: Any | None = None
        try:
            for viewport in group:
                async with limiter.slot():
                    # Re-check after the wait: a slot can free up long after the
                    # task was cancelled or lost its browser.
                    if (
                        browser_connection_lost
                        or is_cancelled
                        or await _refresh_cancelled_status()
                    ):
                        return
                    browser = await _ensure_browser(browser_name)
                    if browser is None:
                        return
                    if reuse_context and context_browser is not browser:
                        # First viewport, or the browser reconnected under a
                        # now-dead context.
                        await _close_quietly(context)
                        context = context_browser = None
                        try:
                            context = await open_capture_context(browser, viewport)
                            context_browser = browser
                        except Exception as exc:
                            logger.warning(
                                "task=%s shared context failed for %s %s, "
                                "capturing in an isolated context: %s",
                                task_id, browser_name, url, exc,
                            )
                    status = await _capture_one(
                        url, viewport, browser_name, browser, context
                    )
                    if context is not None and status != "ok":
                        # The failure may have left the shared context broken;
                        # the next viewport starts from a fresh one.
                        await _close_quietly(context)
                        context = context_browser = None
        finally:
            await _close_quietly(context)

    async def _producer() -> None:
        nonlocal is_cancelled
        for item in work_items:
            if is_cancelled:
                break
            await queue.put(item)
        for _ in range(worker_count):
            await queue.put(None)

    async def _consumer() -> None:
        while True:
            item = await queue.get()
            try:
                if item is None:
                    return
                if browser_connection_lost or is_cancelled:
                    continue
                await _run_group(*item)
            finally:
                queue.task_done()

    async def _close_browsers() -> None:
        for b_instance in browser_instances.values():
            try:
                await b_instance.close()
            except Exception as exc:
                logger.warning("task=%s browser close failed: %s", task_id, exc)

    # ------------------------------------------------------------------
    # Launch browser(s) and run all combinations
    # ------------------------------------------------------------------
    try:
        async with async_playwright() as pw:
            try:
                # Launch one Playwright Browser instance per unique browser name.
                # Per-viewport overrides mean we may need more than one browser type.
                for bn in unique_browsers:
                    try:
                        browser_instances[bn] = await _launch_browser(pw, bn)
                    except Exception as exc:
                        logger.error(
                            "task=%s browser launch failed browser=%s: %s",
                            task_id, bn, exc,
                        )
                        async with AsyncSession(engine) as session:
                            t = await session.get(Task, tid)
                            if t:
                                t.status = "failed"
                                t.completed_at = datetime.utcnow()
                                session.add(t)
                                await session.commit()
                        return

                producer = asyncio.create_task(_producer())
                consumers = [
                    asyncio.create_task(_consumer())
                    for _ in range(worker_count)
                ]
                await asyncio.gather(producer, *consumers)
                if browser_connection_lost:
                    raise RuntimeError(
                        "browser connection lost and reconnect failed"
                    )
            finally:
                await _close_browsers()
    except asyncio.CancelledError:
        if await _was_user_cancel_requested():
            logger.info("task=%s cancelled by user-requested worker abort", task_id)
            await _mark_cancelled()
            await _clear_cancel_marker()
        elif _is_job_timeout_cancel():
            logger.warning(
                "task=%s timed out after %ss",
                task_id,
                job_timeout_seconds,
            )
            await _mark_timed_out()
        else:
            logger.warning("task=%s interrupted before timeout", task_id)
            await _mark_failed_after_interrupt()
        raise
    except Exception as exc:
        logger.error(
            "task=%s unexpected error: %s", task_id, exc, exc_info=True
        )
        async with AsyncSession(engine) as session:
            t = await session.get(Task, tid)
            if t:
                t.status = "failed"
                t.completed_at = datetime.utcnow()
                session.add(t)
                await session.commit()
        return

    # ------------------------------------------------------------------
    # Finalise task status
    # ------------------------------------------------------------------
    final_status = "completed"
    await _persist_progress(force=True)
    async with AsyncSession(engine) as session:
        t = await session.get(Task, tid)
        if t:
            # Don't overwrite an externally set "cancelled" status
            if t.status != "cancelled":
                if progress_done == 0 and progress_failed > 0:
                    t.status = "failed"
                else:
                    t.status = "completed"
            final_status = t.status
            t.progress_done = progress_done
            t.progress_failed = progress_failed
            t.completed_at = datetime.utcnow()
            session.add(t)
            await session.commit()

    logger.info(
        "task=%s %s: done=%d failed=%d total=%d",
        task_id, final_status, progress_done, progress_failed, total,
    )


async def _recompute_task_progress(engine: Any, task_id: uuid.UUID) -> None:
    """Recompute a task's done/failed counters from its screenshots.

    Flips a ``failed``/``timed_out`` task to ``completed`` once every screenshot
    is OK again. Completed tasks keep their status; only counters are refreshed.
    """
    async with AsyncSession(engine) as session:
        t = await session.get(Task, task_id)
        if not t:
            return
        result = await session.exec(
            select(Screenshot).where(Screenshot.task_id == task_id)
        )
        screenshots = list(result.all())
        progress_done = sum(1 for s in screenshots if s.status == "ok")
        progress_failed = sum(1 for s in screenshots if s.status == "failed")
        t.progress_done = progress_done
        t.progress_failed = progress_failed
        if (
            t.status in ("failed", "timed_out")
            and progress_failed == 0
            and progress_done > 0
        ):
            t.status = "completed"
        session.add(t)
        await session.commit()


async def reset_orphaned_recapturing(engine: Any) -> int:
    """Flip screenshots stuck in 'recapturing' back to a terminal state.

    A recapture interrupted by a worker crash, redeploy, or abort leaves its row
    in 'recapturing' forever — nothing else resets it. Run on worker startup to
    mark those rows ``failed`` and refresh the parent tasks' counters. Returns
    the number of rows reset.
    """
    async with AsyncSession(engine) as session:
        result = await session.exec(
            select(Screenshot).where(Screenshot.status == "recapturing")
        )
        stuck = list(result.all())
        if not stuck:
            return 0
        task_ids: set[uuid.UUID] = set()
        for s in stuck:
            s.status = "failed"
            s.error_message = "Re-capture interrupted by worker restart."
            session.add(s)
            task_ids.add(s.task_id)
        await session.commit()

    for task_id in task_ids:
        await _recompute_task_progress(engine, task_id)

    logger.info("reset %d orphaned recapturing screenshot(s) on startup", len(stuck))
    return len(stuck)


async def recapture_screenshot(ctx: dict, screenshot_id: str) -> None:
    """ARQ job: re-capture a single existing screenshot in place.

    Re-runs capture for the same (url, viewport, browser) combination, overwrites
    the same file, updates the same DB row, then refreshes the parent task's
    counters/status.
    """
    engine = ctx["db_engine"]
    sid = uuid.UUID(screenshot_id)

    # Load everything we need, capturing scalars before the session closes.
    async with AsyncSession(engine) as session:
        screenshot = await session.get(Screenshot, sid)
        if not screenshot:
            logger.error(
                "recapture_screenshot: screenshot_id=%s not found", screenshot_id
            )
            return

        task = await session.get(Task, screenshot.task_id)
        if not task:
            logger.error(
                "recapture_screenshot: task missing for screenshot=%s", screenshot_id
            )
            screenshot.status = "failed"
            screenshot.error_message = "Parent task no longer exists."
            session.add(screenshot)
            await session.commit()
            return

        config_result = await session.execute(
            select(ProjectConfig).where(ProjectConfig.project_id == task.project_id)
        )
        config = config_result.scalars().first()
        if not config:
            logger.error(
                "recapture_screenshot: ProjectConfig missing project=%s screenshot=%s",
                task.project_id, screenshot_id,
            )
            screenshot.status = "failed"
            screenshot.error_message = "Project configuration not found."
            session.add(screenshot)
            await session.commit()
            return

        app_settings = await get_application_settings(session)
        sem_limit = max(int(resolve_setting(app_settings, "sem_limit")), 1)

        task_id = task.id
        project_id = task.project_id
        browser_name = screenshot.browser
        url = screenshot.url
        viewport = {"width": screenshot.width, "height": screenshot.height}
        # Reuse the screenshot's own browser + format so build_file_path resolves
        # to the identical path (same file overwritten in place).
        combo_config = EffectiveConfig(
            browser=browser_name,
            fmt=screenshot.file_format,
            quality=app_settings.screenshot_quality,
            wait_until=config.wait_until,
            timeout=config.timeout,
        )

    logger.info(
        "recapture screenshot=%s task=%s url=%s viewport=%dx%d browser=%s",
        screenshot_id, task_id, url,
        viewport["width"], viewport["height"], browser_name,
    )

    async def _mark_failed(message: str) -> None:
        """Reset the row out of 'recapturing' to a terminal state.

        Uses a fresh session (the capture session is already gone) and only
        touches rows still in 'recapturing' so a row that already reached a
        terminal status is never clobbered.
        """
        async with AsyncSession(engine) as session:
            target = await session.get(Screenshot, sid)
            if target and target.status == "recapturing":
                target.status = "failed"
                target.error_message = message
                session.add(target)
                await session.commit()
        await _recompute_task_progress(engine, task_id)

    recapture_timeout = settings.SCREENSHOT_RECAPTURE_TIMEOUT_SECONDS
    limiter = _capture_limiter(ctx, sem_limit)
    try:
        # Take the worker-wide capture slot before connecting, so a burst of
        # re-captures queues here instead of piling pages onto the browser host.
        async with limiter.slot(), async_playwright() as pw:
            browser = await _launch_browser(pw, browser_name)
            try:
                async with AsyncSession(engine) as session:
                    target = await session.get(Screenshot, sid)
                    if not target:
                        return
                    # Bound a single recapture independently of the (2h) batch
                    # job_timeout so a slow/hung page can't pin the row in
                    # "recapturing" for hours.
                    await asyncio.wait_for(
                        capture_screenshot(
                            browser=browser,
                            session=session,
                            task_id=task_id,
                            project_id=project_id,
                            url=url,
                            viewport=viewport,
                            config=combo_config,
                            existing=target,
                        ),
                        timeout=recapture_timeout,
                    )
            finally:
                try:
                    await browser.close()
                except Exception as exc:
                    logger.warning(
                        "recapture screenshot=%s browser close failed: %s",
                        screenshot_id, exc,
                    )
    except TimeoutError:
        logger.warning(
            "recapture screenshot=%s timed out after %ds",
            screenshot_id, recapture_timeout,
        )
        await _mark_failed(f"Re-capture timed out after {recapture_timeout}s.")
        return
    except asyncio.CancelledError:
        # Job aborted or hit the arq job_timeout. CancelledError is a
        # BaseException, so the generic handler below never sees it — reset the
        # row (best effort) before propagating so arq still records the abort.
        logger.warning("recapture screenshot=%s cancelled", screenshot_id)
        try:
            await _mark_failed("Re-capture was cancelled before it finished.")
        except Exception:
            logger.exception(
                "recapture screenshot=%s failed to reset status on cancel",
                screenshot_id,
            )
        raise
    except Exception as exc:
        logger.error(
            "recapture screenshot=%s failed: %s", screenshot_id, exc, exc_info=True
        )
        await _mark_failed(f"Re-capture failed: {exc}")
        return

    await _recompute_task_progress(engine, task_id)
    logger.info("recapture screenshot=%s complete task=%s", screenshot_id, task_id)
