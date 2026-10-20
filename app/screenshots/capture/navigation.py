"""Navigation stage: page.goto with the wait_until fallback chain, then the
post-navigation quiet period."""

import logging
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import config as cfg
from app.screenshots.capture import network
from app.screenshots.capture.config import EffectiveConfig
from app.screenshots.capture.types import CaptureRun, NavigationResult

logger = logging.getLogger(__name__)

_WEBKIT_BROWSER_NAMES = {"webkit", "safari"}


def navigation_wait_until(config: EffectiveConfig) -> str:
    if config.browser.lower() in _WEBKIT_BROWSER_NAMES and config.wait_until == "load":
        return "domcontentloaded"
    return config.wait_until


def navigation_wait_until_chain(config: EffectiveConfig) -> list[str]:
    """Ordered wait_until levels to try for navigation, heaviest → lightest.

    A `page.goto` that times out on a heavier condition (e.g. `load` blocked on a
    slow third-party subresource) falls through to a lighter one. The page's
    actual content readiness is still guaranteed afterwards by the scroll /
    decode / DOM-stability steps, so `domcontentloaded` is a safe final fallback;
    only a timeout there means the page is genuinely unreachable.
    """
    full_chain = ["networkidle", "load", "domcontentloaded"]
    start = navigation_wait_until(config)
    if start not in full_chain:
        return [start]
    chain = full_chain[full_chain.index(start) :]
    # WebKit treats `load` like `domcontentloaded` (see navigation_wait_until),
    # so collapse the two to avoid a redundant identical retry.
    if config.browser.lower() in _WEBKIT_BROWSER_NAMES:
        chain = [level for level in chain if level != "load"]
    return chain


def _response_status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


async def navigate(run: CaptureRun) -> NavigationResult:
    """Navigate ``run.page`` to ``run.url`` and let the page's own post-load
    requests settle. A goto timeout on the lightest wait level propagates
    unchanged (PlaywrightTimeoutError)."""
    page = run.page
    config = run.config
    response: Any = None
    # Try navigation conditions heaviest → lightest. A goto timeout on a
    # heavier condition (e.g. `load` blocked on a slow third-party
    # subresource) is non-fatal: fall through to a lighter one. Only a
    # timeout on the lightest level means the page is genuinely unreachable.
    nav_chain = navigation_wait_until_chain(config)
    nav_wait_until = nav_chain[0]
    with run.report.phase("goto"):
        for index, level in enumerate(nav_chain):
            try:
                response = await page.goto(
                    run.url, wait_until=level, timeout=config.timeout
                )
                nav_wait_until = level
                break
            except PlaywrightTimeoutError:
                if index == len(nav_chain) - 1:
                    raise
                logger.warning(
                    "navigation wait_until=%s timed out for %s, "
                    "falling back to wait_until=%s",
                    level,
                    run.url,
                    nav_chain[index + 1],
                )

    # Let the page's own post-load requests (xhr/fetch included) settle
    # before stabilization. Replaces a blind networkidle probe that cost its
    # full 5 s on pages that never go idle (analytics, long-polling).
    with run.report.phase("quiet"):
        await network.wait_for_quiet(
            page,
            run.tracker,
            min_ms=0,
            quiet_ms=cfg.POST_GOTO_QUIET_MS,
            max_ms=cfg.POST_GOTO_QUIET_CAP_MS,
            include_data=True,
        )

    final_url = getattr(page, "url", None)
    return NavigationResult(
        requested_url=run.url,
        final_url=final_url if isinstance(final_url, str) and final_url else run.url,
        status=_response_status(response),
        load_event_fired=nav_wait_until != "domcontentloaded",
        wait_strategy=nav_wait_until,
    )


async def reload(run: CaptureRun, *, reason: str) -> NavigationResult:
    """Reload ``run.page`` in place (same context, cookies kept)."""
    raise NotImplementedError("navigation.reload is not implemented yet")
