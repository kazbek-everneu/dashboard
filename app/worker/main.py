import logging
from typing import ClassVar

from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.redis import close_redis_pool, get_redis_settings
from app.tasks.limiter import CaptureLimiter
from app.tasks.worker import (
    recapture_screenshot,
    reset_orphaned_recapturing,
    run_screenshot_task,
)

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


async def startup(ctx: dict) -> None:
    ctx["db_engine"] = create_async_engine(
        settings.DATABASE_URL,
        pool_pre_ping=True,
    )
    # One limiter for every job in this worker process: SEM_LIMIT bounds the
    # pages open on the browser host across all tasks and re-captures. Each
    # job resizes it to the current (DB-overridable) sem_limit when it starts.
    ctx["capture_limiter"] = CaptureLimiter(settings.SEM_LIMIT)
    logger.info("Worker started, DB engine created")
    # Recover any recapture left mid-flight by a previous crash/redeploy so its
    # row never stays stuck in "recapturing".
    await reset_orphaned_recapturing(ctx["db_engine"])


async def shutdown(ctx: dict) -> None:
    engine = ctx.get("db_engine")
    if engine:
        await engine.dispose()
    await close_redis_pool()
    logger.info("Worker shutdown, DB engine disposed")


class WorkerSettings:
    functions: ClassVar = [run_screenshot_task, recapture_screenshot]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    max_jobs = settings.WORKER_MAX_JOBS
    job_timeout = settings.TASK_JOB_TIMEOUT_SECONDS
    allow_abort_jobs = True
