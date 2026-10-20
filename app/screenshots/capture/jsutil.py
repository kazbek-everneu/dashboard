"""Running page scripts.

Every page script of the capture package goes through :func:`evaluate` (or
:func:`evaluate_self_bounded` for scripts that bound themselves). Each script
carries a ``/*qa:<name>*/`` marker as the first token of its function body —
never before the function: Playwright only wraps a ``function`` expression in
parentheses when the string starts with it. Test fakes dispatch on the marker.
"""

import asyncio
import re
from typing import Any

from playwright.async_api import Page

from app.screenshots.capture import config as cfg
from app.screenshots.capture.errors import EvaluateTimeout

_MARKER_RE = re.compile(r"/\*qa:([a-z0-9-]+)\*/")


def script_marker(script: str) -> str | None:
    """The ``qa:`` marker name of *script*, if it has one."""
    match = _MARKER_RE.search(script)
    return match.group(1) if match else None


def _consume_task_result(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled():
        task.exception()


async def evaluate_self_bounded(page: Page, script: str, timeout_ms: int) -> Any:
    """Run ``page.evaluate(script, timeout_ms)`` for a self-bounding script.

    The script enforces *timeout_ms* itself; the outer asyncio timeout (plus
    config.JS_TIMEOUT_GRACE_MS) is only a safety net for a wedged page and
    raises the builtin TimeoutError, which the callers turn into their own
    errors. The evaluate runs as its own task whose result is always
    retrieved, so abandoning it never leaves Playwright's protocol future
    unobserved (which logged "Future exception was never retrieved:
    TargetClosedError" once the context closed under it).
    """
    task = asyncio.ensure_future(page.evaluate(script, timeout_ms))
    task.add_done_callback(_consume_task_result)
    return await asyncio.wait_for(
        asyncio.shield(task), (timeout_ms + cfg.JS_TIMEOUT_GRACE_MS) / 1000
    )


async def evaluate(
    page: Page,
    script: str,
    arg: Any = None,
    *,
    timeout_ms: int | None = None,
) -> Any:
    """Evaluate *script* on *page*, passing *arg* only when it is not None.

    Without *timeout_ms* this is a plain ``page.evaluate``. With it the
    evaluate runs shielded in its own task: after *timeout_ms* the caller gets
    EvaluateTimeout while the abandoned evaluate finishes in the background
    and its result or late failure is consumed.
    """
    call = page.evaluate(script) if arg is None else page.evaluate(script, arg)
    if timeout_ms is None:
        return await call
    task = asyncio.ensure_future(call)
    task.add_done_callback(_consume_task_result)
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout_ms / 1000)
    except TimeoutError as exc:
        name = script_marker(script) or "page script"
        raise EvaluateTimeout(f"{name} did not finish within {timeout_ms} ms") from exc
