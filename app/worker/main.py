import asyncio
import logging
from collections.abc import Callable
from typing import Any, ClassVar

from arq.cron import cron
from arq.worker import func
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import settings
from app.core.redis import close_redis_pool, get_redis_settings
from app.tasks.limiter import CaptureLimiter
from app.tasks.worker import (
    reap_orphaned_tasks,
    reap_orphaned_tasks_job,
    recapture_screenshot,
    reset_orphaned_recapturing,
    run_screenshot_task,
)

logger = logging.getLogger(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
)


_UNRETRIEVED_FUTURE_MESSAGE = "Future exception was never retrieved"

ExceptionHandler = Callable[[asyncio.AbstractEventLoop, dict[str, Any]], object]


def _is_target_closed_error(exc: object) -> bool:
    """Playwright's TargetClosedError (or a subclass), matched by name: the
    class lives in a private module (playwright._impl._errors)."""
    return any(
        cls.__name__ == "TargetClosedError" and cls.__module__.startswith("playwright")
        for cls in type(exc).__mro__
    )


def quiet_target_closed_handler(previous: ExceptionHandler | None) -> ExceptionHandler:
    """A loop exception handler that logs only Playwright's "Future exception
    was never retrieved" for a TargetClosedError at DEBUG; everything else
    goes to *previous* (or the loop's default handler).

    Playwright fails the protocol future of every call still pending when a
    connection closes (Connection.cleanup). A call whose caller was cancelled
    without its task ending cancelled leaves that future unobserved, so a
    shutdown or abort mid-capture used to log it as an error. The worker's own
    call sites avoid it (closes and cancellable work run in their own tasks);
    this catches what remains inside the capture pipeline and Playwright.
    """

    def handler(loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        exc = context.get("exception")
        if context.get("message") == _UNRETRIEVED_FUTURE_MESSAGE and (
            _is_target_closed_error(exc)
        ):
            logger.debug("ignored unretrieved Playwright future: %r", exc)
            return
        if previous is not None:
            previous(loop, context)
        else:
            loop.default_exception_handler(context)

    return handler


def install_exception_handler() -> None:
    """Install quiet_target_closed_handler on the running loop."""
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(
        quiet_target_closed_handler(loop.get_exception_handler())
    )


async def startup(ctx: dict) -> None:
    install_exception_handler()
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
    # Fail tasks a previous worker died on, so they stop blocking their project.
    await reap_orphaned_tasks(ctx["db_engine"], ctx["redis"])


async def shutdown(ctx: dict) -> None:
    engine = ctx.get("db_engine")
    if engine:
        await engine.dispose()
    await close_redis_pool()
    logger.info("Worker shutdown, DB engine disposed")


class WorkerSettings:
    # max_tries=1: ARQ re-queues a job its worker was killed or stopped on and
    # would run the whole capture again (duplicate rows). The interrupted task
    # is failed instead — by the job's own interrupt handling or by the reaper.
    functions: ClassVar = [
        func(run_screenshot_task, max_tries=1),
        recapture_screenshot,
    ]
    cron_jobs: ClassVar = [
        cron(
            reap_orphaned_tasks_job,
            minute=set(range(0, 60, 5)),
            unique=True,
            timeout=60,
        )
    ]
    on_startup = startup
    on_shutdown = shutdown
    redis_settings = get_redis_settings()
    max_jobs = settings.WORKER_MAX_JOBS
    job_timeout = settings.TASK_JOB_TIMEOUT_SECONDS
    allow_abort_jobs = True
