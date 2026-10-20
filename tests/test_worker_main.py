"""The worker's loop exception handler demotes only Playwright's unretrieved
TargetClosedError futures."""

import asyncio
import gc
import logging
from unittest.mock import AsyncMock, patch

import pytest
from playwright._impl._errors import TargetClosedError
from playwright.async_api import Error as PlaywrightError

from app.worker import main as worker_main

MESSAGE = "Future exception was never retrieved"
_LOOP = object()  # stands in for the loop argument the handler passes along


class _SubclassedTargetClosedError(TargetClosedError):
    pass


@pytest.mark.parametrize(
    "exc", [TargetClosedError(), _SubclassedTargetClosedError("closed")]
)
def test_unretrieved_target_closed_is_logged_at_debug(
    exc: BaseException, caplog: pytest.LogCaptureFixture
) -> None:
    previous: list[dict] = []
    handler = worker_main.quiet_target_closed_handler(
        lambda _loop, context: previous.append(context)
    )
    caplog.set_level(logging.DEBUG, logger="app.worker.main")

    handler(_LOOP, {"message": MESSAGE, "exception": exc})

    assert previous == []
    assert [r.levelno for r in caplog.records] == [logging.DEBUG]


@pytest.mark.parametrize(
    "context",
    [
        # another Playwright error, a non-Playwright error, another message
        {"message": MESSAGE, "exception": PlaywrightError("net::ERR_ABORTED")},
        {"message": MESSAGE, "exception": RuntimeError("TargetClosedError")},
        {"message": "Task exception was never retrieved", "exception": TargetClosedError()},
        {"message": "Exception in callback", "exception": TargetClosedError()},
        {"message": MESSAGE},
    ],
)
def test_everything_else_goes_to_the_previous_handler(context: dict) -> None:
    previous: list[dict] = []
    handler = worker_main.quiet_target_closed_handler(
        lambda _loop, ctx: previous.append(ctx)
    )

    handler(_LOOP, context)

    assert previous == [context]


def test_without_a_previous_handler_the_loop_default_is_used() -> None:
    loop = asyncio.new_event_loop()
    context = {"message": "boom", "exception": RuntimeError("boom")}
    try:
        with patch.object(loop, "default_exception_handler") as default:
            worker_main.quiet_target_closed_handler(None)(loop, context)
    finally:
        loop.close()

    default.assert_called_once_with(context)


async def test_startup_installs_the_handler_on_the_running_loop(
    caplog: pytest.LogCaptureFixture,
) -> None:
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    other: list[dict] = []
    loop.set_exception_handler(lambda _loop, context: other.append(context))
    ctx: dict = {"redis": object()}
    try:
        with (
            patch.object(worker_main, "create_async_engine", return_value=object()),
            patch.object(worker_main, "reset_orphaned_recapturing", new=AsyncMock()),
            patch.object(worker_main, "reap_orphaned_tasks", new=AsyncMock()),
        ):
            await worker_main.startup(ctx)

        caplog.set_level(logging.ERROR, logger="asyncio")
        # A future failed with TargetClosedError that nobody retrieves, like
        # the protocol future of a cancelled Playwright call at shutdown.
        future = loop.create_future()
        future.set_exception(TargetClosedError())
        del future
        gc.collect()
        unrelated = loop.create_future()
        unrelated.set_exception(RuntimeError("unrelated"))
        del unrelated
        gc.collect()
    finally:
        loop.set_exception_handler(previous)

    # The TargetClosedError is gone; the unrelated one still reaches the
    # handler that was installed before.
    assert [type(c["exception"]).__name__ for c in other] == ["RuntimeError"]
    assert "capture_limiter" in ctx
