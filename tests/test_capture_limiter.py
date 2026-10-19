import asyncio

import pytest

from app.tasks.limiter import CaptureLimiter


async def _settle() -> None:
    for _ in range(5):
        await asyncio.sleep(0)


async def test_limiter_never_exceeds_limit_under_contention() -> None:
    limiter = CaptureLimiter(2)
    running = 0
    peak = 0

    async def capture() -> None:
        nonlocal running, peak
        async with limiter.slot():
            running += 1
            peak = max(peak, running)
            await asyncio.sleep(0)
            running -= 1

    await asyncio.gather(*(capture() for _ in range(10)))

    assert peak == 2
    assert limiter.in_use == 0


async def test_limiter_grants_waiters_in_arrival_order() -> None:
    limiter = CaptureLimiter(1)
    await limiter.acquire()
    order: list[int] = []

    async def waiter(n: int) -> None:
        async with limiter.slot():
            order.append(n)

    tasks = [asyncio.create_task(waiter(n)) for n in range(4)]
    await _settle()
    limiter.release()
    await asyncio.gather(*tasks)

    assert order == [0, 1, 2, 3]


async def test_limiter_grow_and_shrink_take_effect() -> None:
    limiter = CaptureLimiter(1)
    await limiter.acquire()
    second = asyncio.create_task(limiter.acquire())
    await _settle()
    assert not second.done()

    limiter.set_limit(2)  # growing wakes the waiter immediately
    await _settle()
    assert second.done()
    assert limiter.in_use == 2

    limiter.set_limit(1)  # shrinking never revokes held slots...
    third = asyncio.create_task(limiter.acquire())
    limiter.release()
    await _settle()
    assert not third.done()  # ...but holds new grants until below the limit
    limiter.release()
    await _settle()
    assert third.done()
    assert limiter.in_use == 1


async def test_limiter_cancelled_waiter_is_skipped() -> None:
    limiter = CaptureLimiter(1)
    await limiter.acquire()
    cancelled = asyncio.create_task(limiter.acquire())
    survivor = asyncio.create_task(limiter.acquire())
    await _settle()

    cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled
    limiter.release()
    await _settle()

    assert survivor.done()
    assert limiter.in_use == 1


async def test_limiter_cancel_after_grant_passes_slot_on() -> None:
    limiter = CaptureLimiter(1)
    await limiter.acquire()
    granted_then_cancelled = asyncio.create_task(limiter.acquire())
    next_in_line = asyncio.create_task(limiter.acquire())
    await _settle()

    # Grant the slot, then cancel before the waiter gets to run.
    limiter.release()
    granted_then_cancelled.cancel()
    with pytest.raises(asyncio.CancelledError):
        await granted_then_cancelled
    await _settle()

    assert next_in_line.done()
    assert limiter.in_use == 1
