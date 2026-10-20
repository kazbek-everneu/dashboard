import logging
import uuid

from arq.jobs import Job, JobStatus
from sqlalchemy.exc import IntegrityError
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import User
from app.core.exceptions import ConflictError, NotFoundError
from app.core.time import utcnow
from app.core.url_security import normalize_project_child_url
from app.projects.models import ProjectConfig, SitemapURL
from app.projects.service import get_project
from app.screenshots.models import Screenshot
from app.screenshots.storage import delete_task_files
from app.tasks.lifecycle import ACTIVE_TASK_STATUSES, task_is_alive, worker_job_id_for
from app.tasks.models import (
    Task,
    TaskCreate,
    TaskListResponse,
    TaskRead,
    TaskStatsResponse,
)

logger = logging.getLogger(__name__)

_WORKER_JOB_ABORT_TIMEOUT_SECONDS = 15.0
_WORKER_JOB_ABORT_POLL_SECONDS = 0.2
_ACTIVE_WORKER_JOB_STATUSES = {
    JobStatus.deferred,
    JobStatus.queued,
    JobStatus.in_progress,
}
_TERMINAL_WORKER_JOB_STATUSES = {
    JobStatus.complete,
    JobStatus.not_found,
}
_USER_CANCEL_MARKER_TTL_SECONDS = 24 * 60 * 60


def _user_cancel_marker_key(task_id: uuid.UUID | str) -> str:
    return f"screenshot-task:user-cancel:{task_id}"


async def _set_user_cancel_marker(task_id: uuid.UUID | str) -> None:
    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    await redis.set(
        _user_cancel_marker_key(task_id),
        "1",
        ex=_USER_CANCEL_MARKER_TTL_SECONDS,
    )


async def _clear_user_cancel_marker(task_id: uuid.UUID | str) -> None:
    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    await redis.delete(_user_cancel_marker_key(task_id))


async def is_user_cancel_requested(task_id: uuid.UUID | str) -> bool:
    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    return await redis.get(_user_cancel_marker_key(task_id)) is not None


async def _abort_worker_job(worker_job_id: str, *, failure_detail: str) -> None:
    """Abort an ARQ job or verify that it is already terminal."""
    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    job = Job(job_id=worker_job_id, redis=redis)
    try:
        job_status = await job.status()
        if job_status in _TERMINAL_WORKER_JOB_STATUSES:
            return

        if job_status not in _ACTIVE_WORKER_JOB_STATUSES:
            logger.warning(
                "Task worker job id=%s has unexpected status=%s",
                worker_job_id,
                job_status,
            )
            return

        try:
            aborted = await job.abort(
                timeout=_WORKER_JOB_ABORT_TIMEOUT_SECONDS,
                poll_delay=_WORKER_JOB_ABORT_POLL_SECONDS,
            )
        except TimeoutError as exc:
            logger.warning("Timed out aborting worker job id=%s", worker_job_id)
            raise ConflictError(failure_detail) from exc

        if aborted:
            return

        refreshed_status = await job.status()
        if refreshed_status in _TERMINAL_WORKER_JOB_STATUSES:
            return

        logger.warning(
            "Worker job id=%s abort was not confirmed; status=%s",
            worker_job_id,
            refreshed_status,
        )
        raise ConflictError(failure_detail)
    except ConflictError:
        raise
    except Exception as exc:
        logger.error("Failed to abort worker job id=%s: %s", worker_job_id, exc)
        raise ConflictError(failure_detail) from exc


async def _load_config(session: AsyncSession, project_id: uuid.UUID) -> ProjectConfig:
    result = await session.exec(
        select(ProjectConfig).where(ProjectConfig.project_id == project_id)
    )
    config = result.first()
    if not config:
        raise NotFoundError("Project config not found")
    return config


async def _resolve_target_urls(
    session: AsyncSession,
    project_id: uuid.UUID,
    project_url: str,
    requested: list[str] | None,
) -> list[str]:
    if requested is not None:
        if not requested:
            raise ConflictError("target_urls must not be empty when provided")
        normalized = []
        seen: set[str] = set()
        for url in requested:
            n = await normalize_project_child_url(project_url, url)
            if n not in seen:
                seen.add(n)
                normalized.append(n)
        return normalized

    result = await session.exec(
        select(SitemapURL).where(
            SitemapURL.project_id == project_id,
            SitemapURL.is_enabled == True,  # noqa: E712
        )
    )
    urls = [row.url for row in result.all()]
    if not urls:
        raise ConflictError(
            "No enabled sitemap URLs found for this project. "
            "Fetch the sitemap first or provide target_urls explicitly."
        )
    return urls


async def create_task(session: AsyncSession, data: TaskCreate, user: User) -> TaskRead:
    # Verify the project exists (projects are shared across all users)
    project = await get_project(session, data.project_id, user)

    # Guard: no two running tasks for the same project simultaneously
    result = await session.exec(
        select(Task).where(
            Task.project_id == data.project_id,
            Task.status.in_(["pending", "running"]),  # type: ignore[attr-defined]
        )
    )
    if result.first():
        raise ConflictError(
            "A task is already pending or running for this project. "
            "Wait for it to complete before creating a new one."
        )

    config = await _load_config(session, data.project_id)

    # Resolve effective values (override > project default)
    effective_browsers: list[str] = data.override_browsers or config.default_browsers
    effective_viewports: list[dict] = data.override_viewports or config.viewports
    target_urls = await _resolve_target_urls(
        session,
        data.project_id,
        project.url,
        data.target_urls,
    )

    # Count combos: each viewport may declare its own browser list
    total_combos = sum(
        len(vp.get("browsers") or effective_browsers) for vp in effective_viewports
    )
    progress_total = len(target_urls) * total_combos

    task = Task(
        project_id=data.project_id,
        created_by=user.id,
        status="pending",
        progress_total=progress_total,
        progress_done=0,
        progress_failed=0,
        override_browser=data.override_browser,
        override_browsers=data.override_browsers,
        override_viewports=data.override_viewports,
        target_urls=target_urls,
    )
    session.add(task)
    try:
        await session.commit()
    except IntegrityError as exc:
        await session.rollback()
        raise ConflictError(
            "A task is already pending or running for this project. "
            "Wait for it to complete before creating a new one."
        ) from exc
    await session.refresh(task)

    # Enqueue ARQ job — if this fails, mark task failed immediately so it
    # never stays permanently stuck as "pending".
    from app.core.redis import get_redis_pool

    worker_job_id = worker_job_id_for(task.id)
    redis = await get_redis_pool()
    try:
        job = await redis.enqueue_job(
            "run_screenshot_task",
            str(task.id),
            _job_id=worker_job_id,
        )
        if job is None:
            raise ConflictError(
                "Failed to enqueue screenshot task — worker job already exists."
            )
    except Exception as exc:
        logger.error("Failed to enqueue task id=%s: %s", task.id, exc)
        task.status = "failed"
        task.completed_at = utcnow()
        session.add(task)
        await session.commit()
        if isinstance(exc, ConflictError):
            raise
        raise ConflictError(
            "Failed to enqueue screenshot task — Redis unavailable. Please try again."
        ) from exc

    task.worker_job_id = job.job_id
    session.add(task)
    await session.commit()
    await session.refresh(task)

    logger.info(
        "Created task id=%s worker_job_id=%s project=%s urls=%d viewports=%d "
        "combos=%d total=%d",
        str(task.id),
        task.worker_job_id,
        str(task.project_id),
        len(target_urls),
        len(effective_viewports),
        total_combos,
        progress_total,
    )
    return TaskRead.from_task(task)


async def get_task(session: AsyncSession, task_id: uuid.UUID, user: User) -> TaskRead:
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    # Verify the task's project exists
    await get_project(session, task.project_id, user)
    return TaskRead.from_task(task)


async def cancel_task(
    session: AsyncSession, task_id: uuid.UUID, user: User
) -> TaskRead:
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    await get_project(session, task.project_id, user)
    if task.status not in ACTIVE_TASK_STATUSES:
        raise ConflictError(f"Cannot cancel a task with status '{task.status}'.")
    user_id = user.id  # capture before commit expires the session-bound user object
    # A running task whose worker died has nothing left to abort — waiting on
    # its stale ARQ job would only time out — so it is cancelled directly.
    if task.worker_job_id and (task.status == "pending" or task_is_alive(task)):
        await _set_user_cancel_marker(task.id)
        try:
            await _abort_worker_job(
                task.worker_job_id,
                failure_detail="Task is still stopping. Please try again.",
            )
        except Exception:
            await _clear_user_cancel_marker(task.id)
            raise
    # Re-read under a lock: the job may have finished, or been cancelled by
    # the worker itself, while the abort was in flight. Keep that outcome.
    await session.refresh(task, with_for_update=True)
    if task.status in ACTIVE_TASK_STATUSES:
        task.status = "cancelled"
    if task.completed_at is None:
        task.completed_at = utcnow()
    session.add(task)
    await session.commit()
    await session.refresh(task)
    logger.info("Task id=%s cancelled by user=%s", task_id, user_id)
    return TaskRead.from_task(task)


async def delete_task(session: AsyncSession, task_id: uuid.UUID, user: User) -> None:
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    await get_project(session, task.project_id, user)
    if task.status in ACTIVE_TASK_STATUSES:
        raise ConflictError("Cannot delete a running task. Cancel it first.")
    recapturing = await session.exec(
        select(Screenshot.id)
        .where(Screenshot.task_id == task_id)
        .where(Screenshot.status == "recapturing")
        .limit(1)
    )
    if recapturing.first() is not None:
        raise ConflictError(
            "A screenshot of this task is being re-captured. "
            "Wait for it to finish before deleting the task."
        )
    # A just-cancelled job may still be shutting down; one whose worker is gone
    # (no recent heartbeat) has nothing left to abort.
    if task.worker_job_id and task_is_alive(task):
        await _abort_worker_job(
            task.worker_job_id,
            failure_detail="Task is still stopping. Please try again.",
        )
    user_id = user.id
    project_id = task.project_id
    await session.delete(task)
    await session.commit()
    await delete_task_files(project_id, task_id)
    logger.info("Task id=%s deleted by user=%s", task_id, user_id)


async def list_tasks(
    session: AsyncSession,
    user: User,
    project_id: uuid.UUID | None = None,
    skip: int = 0,
    limit: int = 50,
) -> TaskListResponse:
    base_query = select(Task)
    if project_id is not None:
        # Verify the project exists when filtering
        await get_project(session, project_id, user)
        base_query = base_query.where(Task.project_id == project_id)
        count_query = (
            select(func.count()).select_from(Task).where(Task.project_id == project_id)
        )
    else:
        # Projects are shared across all users — return all tasks
        count_query = select(func.count()).select_from(Task)

    total = int((await session.exec(count_query)).one())
    result = await session.exec(
        base_query.order_by(Task.created_at.desc())  # type: ignore[attr-defined]
        .offset(skip)
        .limit(limit)
    )

    return TaskListResponse(
        items=[TaskRead.from_task(t) for t in result.all()],
        total=total,
    )


async def get_task_stats(session: AsyncSession, user: User) -> TaskStatsResponse:
    # Projects are shared across all users — count all tasks
    rows = await session.exec(select(Task.status, func.count()).group_by(Task.status))
    counts = {status: count for status, count in rows.all()}
    return TaskStatsResponse(
        total=sum(counts.values()),
        pending=counts.get("pending", 0),
        running=counts.get("running", 0),
        completed=counts.get("completed", 0),
        failed=counts.get("failed", 0),
        cancelled=counts.get("cancelled", 0),
        timed_out=counts.get("timed_out", 0),
    )
