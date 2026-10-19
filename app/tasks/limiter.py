"""Process-wide cap on concurrent page captures.

ARQ runs every job of a worker process in one asyncio event loop, so a single
limiter stored in the worker ctx bounds the pages open on the browser host
across all running tasks and re-captures — not just within one task.
"""

import asyncio
from collections import deque
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager


class CaptureLimiter:
    """FIFO counting limiter whose limit can change at runtime.

    Waiters are served strictly in arrival order (no barging), so the capture
    that has waited longest gets the next free slot and concurrent tasks share
    the browser host fairly. A waiter cancelled right after being granted a slot
    passes it on instead of leaking it. Shrinking the limit never revokes slots
    already held; it only delays new grants until enough are released.
    """

    def __init__(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._in_use = 0
        self._waiters: deque[asyncio.Future[None]] = deque()

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def in_use(self) -> int:
        return self._in_use

    def set_limit(self, limit: int) -> None:
        self._limit = max(1, limit)
        self._wake()

    async def acquire(self) -> None:
        if not self._waiters and self._in_use < self._limit:
            self._in_use += 1
            return
        waiter: asyncio.Future[None] = asyncio.get_running_loop().create_future()
        self._waiters.append(waiter)
        try:
            await waiter
        except asyncio.CancelledError:
            if waiter.done() and not waiter.cancelled():
                # Granted just as we were cancelled: hand the slot on.
                self.release()
            else:
                try:
                    self._waiters.remove(waiter)
                except ValueError:
                    pass
            raise

    def release(self) -> None:
        self._in_use -= 1
        self._wake()

    def _wake(self) -> None:
        while self._waiters and self._in_use < self._limit:
            waiter = self._waiters.popleft()
            if waiter.done():  # cancelled while waiting
                continue
            self._in_use += 1
            waiter.set_result(None)

    @asynccontextmanager
    async def slot(self) -> AsyncIterator[None]:
        await self.acquire()
        try:
            yield
        finally:
            self.release()
