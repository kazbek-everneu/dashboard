"""Running page scripts.

Every page script of the capture package goes through :func:`evaluate` (or
:func:`evaluate_self_bounded` for scripts that bound themselves). Each script
carries a ``/*qa:<name>*/`` marker as the first token of its function body —
never before the function: Playwright only wraps a ``function`` expression in
parentheses when the string starts with it. Test fakes dispatch on the marker.

Both helpers run the evaluate as its own task whose result is always
retrieved, so a script abandoned on timeout never leaves Playwright's protocol
future unobserved (that logged "Future exception was never retrieved:
TargetClosedError" once the context closed under it). A script whose document
is replaced while it runs (a late client-side redirect: "Execution context was
destroyed") is re-run once on the new document.
"""

import asyncio
import logging
import re
from collections.abc import Awaitable
from typing import Any

from playwright.async_api import Page

from app.screenshots.capture import config as cfg
from app.screenshots.capture.errors import EvaluateTimeout

logger = logging.getLogger(__name__)

_MARKER_RE = re.compile(r"/\*qa:([a-z0-9-]+)\*/")
# What every engine reports when a navigation replaced the document under a
# running evaluate (see Playwright's *ExecutionContext.rewriteError).
_CONTEXT_DESTROYED = "execution context was destroyed"


def script_marker(script: str) -> str | None:
    """The ``qa:`` marker name of *script*, if it has one."""
    match = _MARKER_RE.search(script)
    return match.group(1) if match else None


def is_context_destroyed(error: BaseException) -> bool:
    """True for the error of an evaluate whose document was navigated away."""
    return _CONTEXT_DESTROYED in str(error).lower()


def _consume_task_result(task: "asyncio.Future[Any]") -> None:
    if not task.cancelled():
        task.exception()


async def _await_bounded(awaitable: Awaitable[Any], timeout_s: float) -> Any:
    """Await *awaitable* as a shielded task for at most *timeout_s*.

    On expiry the builtin TimeoutError is raised (callers translate it) and
    the task keeps running unobserved — its result or late failure is
    consumed. Cancelling the caller never cancels the task either.
    """
    task = asyncio.ensure_future(awaitable)
    task.add_done_callback(_consume_task_result)
    return await asyncio.wait_for(asyncio.shield(task), timeout_s)


async def _wait_for_new_document(page: Page, script: str) -> None:
    """Bounded, best-effort wait for the document that replaced the one a
    script was running in."""
    logger.debug(
        "%s: execution context destroyed, waiting for the new document",
        script_marker(script) or "page script",
    )
    timeout_ms = cfg.CONTEXT_DESTROYED_WAIT_MS
    try:
        await _await_bounded(
            page.wait_for_load_state("domcontentloaded", timeout=timeout_ms),
            (timeout_ms + cfg.JS_TIMEOUT_GRACE_MS) / 1000,
        )
    except Exception as exc:  # the TimeoutError of the bound included
        logger.debug("waiting for the new document failed: %s", exc)


async def evaluate_self_bounded(page: Page, script: str, timeout_ms: int) -> Any:
    """Run ``page.evaluate(script, timeout_ms)`` for a self-bounding script.

    The script enforces *timeout_ms* itself; the outer asyncio timeout (plus
    config.JS_TIMEOUT_GRACE_MS) is only a safety net for a wedged page and
    raises the builtin TimeoutError, which the callers turn into their own
    errors. Re-run once on the new document after a late client-side redirect.
    """
    timeout_s = (timeout_ms + cfg.JS_TIMEOUT_GRACE_MS) / 1000
    try:
        return await _await_bounded(page.evaluate(script, timeout_ms), timeout_s)
    except Exception as exc:
        if not is_context_destroyed(exc):
            raise
    await _wait_for_new_document(page, script)
    return await _await_bounded(page.evaluate(script, timeout_ms), timeout_s)


async def _evaluate_once(page: Page, script: str, arg: Any, timeout_ms: int) -> Any:
    call = page.evaluate(script) if arg is None else page.evaluate(script, arg)
    try:
        return await _await_bounded(call, timeout_ms / 1000)
    except TimeoutError as exc:
        name = script_marker(script) or "page script"
        raise EvaluateTimeout(f"{name} did not finish within {timeout_ms} ms") from exc


async def evaluate(
    page: Page,
    script: str,
    arg: Any = None,
    *,
    timeout_ms: int | None = None,
) -> Any:
    """Evaluate *script* on *page*, passing *arg* only when it is not None.

    Bounded by *timeout_ms* (default config.EVALUATE_TIMEOUT_MS): past it the
    caller gets EvaluateTimeout — never the builtin TimeoutError — while the
    abandoned evaluate finishes in the background and its result or late
    failure is consumed. When a navigation destroys the script's execution
    context (a late client-side redirect), the script runs once more after the
    new document's DOMContentLoaded (a bounded, best-effort wait).
    """
    bound_ms = cfg.EVALUATE_TIMEOUT_MS if timeout_ms is None else timeout_ms
    try:
        return await _evaluate_once(page, script, arg, bound_ms)
    except EvaluateTimeout:
        raise
    except Exception as exc:
        if not is_context_destroyed(exc):
            raise
    await _wait_for_new_document(page, script)
    return await _evaluate_once(page, script, arg, bound_ms)
