"""Screenshot persistence and single-screenshot re-capture.

The capture itself lives in :mod:`app.screenshots.capture`; this module runs
it (``capture_screenshot``), writes the file and the ``screenshots`` row, and
re-exports ``EffectiveConfig`` / ``open_capture_context`` for the worker.
"""

import logging
import uuid
from collections.abc import Mapping

from playwright.async_api import Browser, BrowserContext
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import User
from app.core.exceptions import ConflictError, NotFoundError
from app.core.time import utcnow
from app.screenshots.capture import pipeline, profiles
from app.screenshots.capture.config import EffectiveConfig
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.image_dimensions import read_image_dimensions_from_bytes
from app.screenshots.models import Screenshot
from app.screenshots.storage import (
    abs_file_path,
    build_file_path,
    ensure_parent,
    generate_signed_url,
)

logger = logging.getLogger(__name__)


async def open_capture_context(
    browser: Browser,
    viewport: Mapping[str, int],
    profile: DeviceProfile | None = None,
) -> BrowserContext:
    """New isolated capture context (see profiles.open_capture_context)."""
    return await profiles.open_capture_context(browser, viewport, profile)


def _browser_version(browser: Browser) -> str | None:
    version = getattr(browser, "version", None)
    return version if isinstance(version, str) else None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def capture_screenshot(
    *,
    browser: Browser,
    session: AsyncSession,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    url: str,
    viewport: dict[str, int],
    config: EffectiveConfig,
    existing: Screenshot | None = None,
    context: BrowserContext | None = None,
    profile: DeviceProfile | None = None,
    device_type: str | None = None,
    browser_provider: pipeline.BrowserProvider | None = None,
    deadline_at: float | None = None,
) -> Screenshot:
    """Take a screenshot (with one retry on failure), save to disk, write DB record.

    Concurrency is controlled by the caller. For an ``ok`` row duration_seconds
    measures page.goto → page.screenshot only — context setup/teardown and DB
    write are excluded; for a ``failed`` row it is the wall time spent across
    all attempts (retry sleep excluded).

    When *context* is given the first attempt opens its page in that shared
    context; a retry always runs in a fresh isolated context.

    When *existing* is provided the same DB row is updated in place (re-capture)
    instead of inserting a new record. The file on disk is overwritten at the
    same deterministic path, and ``created_at`` is bumped so clients can
    cache-bust the served image.

    *profile* defaults to the profile for *device_type* (else derived from the
    viewport width). *browser_provider* and *deadline_at* are passed through to
    the capture's retry policy.
    """
    width = viewport["width"]
    height = viewport["height"]
    if profile is None:
        profile = profiles.build_profile(
            config.browser,
            profiles.resolve_device_type(viewport, device_type),
            browser_version=_browser_version(browser),
        )

    result = await pipeline.capture_with_retries(
        browser=browser,
        url=url,
        viewport=viewport,
        config=config,
        profile=profile,
        context=context,
        browser_provider=browser_provider,
        deadline_at=deadline_at,
        task_id=task_id,
    )
    last_error = result.error
    duration = result.duration
    data: bytes = b""
    captured_with_unstable_dom = False
    warning_message: str | None = None
    if last_error is None and result.outcome is not None:
        data = result.outcome.data
        captured_with_unstable_dom = result.outcome.captured_with_unstable_dom
        warning_message = result.outcome.warning_message

    # Build storage path and signed access URL
    relative_path = build_file_path(
        project_id, task_id, url, width, height, config.browser, config.fmt
    )
    # filename is everything after {project_id}/{task_id}/ — used in signed URL
    filename = "/".join(relative_path.split("/")[2:])
    signed_url = generate_signed_url(str(task_id), filename)

    if last_error is None:
        ensure_parent(relative_path)
        abs_file_path(relative_path).write_bytes(data)
        image_dimensions = read_image_dimensions_from_bytes(data)
        status = "ok"
        error_message = warning_message
        logger.info(
            "task=%s saved url=%s viewport=%dx%d browser=%s path=%s "
            "duration=%.3fs unstable_dom=%s",
            task_id,
            url,
            width,
            height,
            config.browser,
            relative_path,
            duration,
            captured_with_unstable_dom,
        )
    else:
        image_dimensions = None
        status = "failed"
        error_message = str(last_error)
        captured_with_unstable_dom = False
        logger.error(
            "task=%s failed url=%s viewport=%dx%d browser=%s duration=%.3fs",
            task_id,
            url,
            width,
            height,
            config.browser,
            duration,
        )
    logger.info(
        "capture task=%s url=%s vp=%dx%d browser=%s device=%s status=%s "
        "warnings=%s timings_ms=%s",
        task_id,
        url,
        width,
        height,
        config.browser,
        profile.device_type,
        status,
        result.report.warning_codes(),
        result.report.timings_ms,
    )

    if existing is not None:
        # Re-capture: update the same row in place (stable id, same file path).
        existing.image_width = image_dimensions[0] if image_dimensions else None
        existing.image_height = image_dimensions[1] if image_dimensions else None
        existing.file_format = config.fmt
        existing.file_path = relative_path
        existing.signed_url = signed_url
        existing.status = status
        existing.captured_with_unstable_dom = captured_with_unstable_dom
        existing.error_message = error_message
        existing.duration_seconds = duration
        existing.created_at = utcnow()
        record = existing
    else:
        record = Screenshot(
            task_id=task_id,
            url=url,
            browser=config.browser,
            width=width,
            height=height,
            image_width=image_dimensions[0] if image_dimensions else None,
            image_height=image_dimensions[1] if image_dimensions else None,
            file_format=config.fmt,
            file_path=relative_path,
            signed_url=signed_url,
            status=status,
            captured_with_unstable_dom=captured_with_unstable_dom,
            error_message=error_message,
            duration_seconds=duration,
            created_at=utcnow(),
        )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Single-screenshot re-capture ("redo")
# ---------------------------------------------------------------------------

_RECAPTURABLE_TASK_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


def build_recapture_job_id(screenshot_id: uuid.UUID | str) -> str:
    return f"screenshot-recapture:{screenshot_id}"


async def request_screenshot_recapture(
    session: AsyncSession, screenshot_id: uuid.UUID, user: User
) -> Screenshot:
    """Mark a single screenshot for re-capture and enqueue a worker job.

    Re-capture re-runs the exact same combination (url x viewport x browser) in
    place, overwriting the same file and updating the same DB row. Only allowed
    once the parent task has reached a terminal state.
    """
    from app.core.redis import get_redis_pool
    from app.projects.service import get_project
    from app.tasks.models import Task

    screenshot = await session.get(Screenshot, screenshot_id)
    if not screenshot:
        raise NotFoundError("Screenshot not found")

    task = await session.get(Task, screenshot.task_id)
    if not task:
        raise NotFoundError("Task not found")
    # Verify the task's project exists (projects are shared across all users)
    await get_project(session, task.project_id, user)

    if task.status not in _RECAPTURABLE_TASK_STATUSES:
        raise ConflictError(
            f"Cannot re-capture a screenshot while the task is '{task.status}'. "
            "Wait for the task to finish."
        )

    # Capture before commit — expire_on_commit=True expires the session-bound
    # task object, and reading task.id afterwards would trigger a sync lazy-load
    # (MissingGreenlet) outside the async greenlet context.
    task_id = task.id
    previous_status = screenshot.status
    previous_error = screenshot.error_message

    screenshot.status = "recapturing"
    screenshot.error_message = None
    session.add(screenshot)
    await session.commit()
    await session.refresh(screenshot)

    # Unique job id per request. A deterministic id (screenshot-recapture:{id})
    # is de-duplicated by ARQ against a *completed* job whose result is still
    # cached (default keep_result=1h), so a second redo of the same screenshot
    # would silently never run and leave the row stuck in "recapturing".
    job_id = f"{build_recapture_job_id(screenshot.id)}:{uuid.uuid4().hex}"
    redis = await get_redis_pool()
    job = None
    try:
        job = await redis.enqueue_job(
            "recapture_screenshot",
            str(screenshot.id),
            _job_id=job_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to enqueue recapture for screenshot=%s: %s", screenshot.id, exc
        )

    if job is None:
        # Enqueue failed (or an unexpected job-id collision) — restore the prior
        # state so the row never sticks in "recapturing".
        screenshot.status = previous_status
        screenshot.error_message = previous_error
        session.add(screenshot)
        await session.commit()
        await session.refresh(screenshot)
        raise ConflictError("Failed to enqueue re-capture. Please try again.")

    logger.info("Enqueued recapture for screenshot=%s task=%s", screenshot.id, task_id)
    return screenshot
