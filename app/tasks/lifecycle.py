"""Task liveness rules shared by the API and the worker.

A running capture job beats ``tasks.heartbeat_at`` every
``HEARTBEAT_INTERVAL_SECONDS``. A task that is still ``pending``/``running``
but whose worker is gone (crash, OOM, ``docker kill``, lost Redis job) would
otherwise block its project forever through the one-active-task-per-project
index, so ``reap_orphaned_tasks`` (app/tasks/worker.py) — run at worker
startup and every few minutes by an ARQ cron job — fails such tasks.
"""

import uuid
from datetime import datetime, timedelta

from app.core.time import as_utc, utcnow
from app.tasks.models import Task

ACTIVE_TASK_STATUSES: tuple[str, ...] = ("pending", "running")
HEARTBEAT_INTERVAL_SECONDS = 30
# A running task whose last beat is older than this has lost its worker.
HEARTBEAT_STALE_AFTER = timedelta(seconds=150)
# A pending task gets this long to be picked up before its job is checked.
PENDING_GRACE = timedelta(minutes=2)


def worker_job_id_for(task_id: uuid.UUID | str) -> str:
    """The deterministic ARQ job id of a task's capture job."""
    return f"screenshot-task:{task_id}"


def task_is_alive(task: Task, now: datetime | None = None) -> bool:
    """Whether a worker has shown signs of life for *task* recently.

    Uses the last heartbeat, or the start time before the first beat.
    """
    last_seen = task.heartbeat_at or task.started_at
    if last_seen is None:
        return False
    return (now or utcnow()) - as_utc(last_seen) < HEARTBEAT_STALE_AFTER
