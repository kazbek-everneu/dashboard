"""Snapshot stage: measure the page, crop horizontal overflow to the layout
viewport and take the full-page screenshot — retried once in place after a
timeout.

Every script here carries a ``/*qa:<name>*/`` marker (see jsutil).
"""

import logging
import math
from dataclasses import dataclass
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import config as cfg
from app.screenshots.capture import jsutil, prepare
from app.screenshots.capture.errors import ScreenshotTimeoutError
from app.screenshots.capture.types import CaptureRun, SnapshotResult, Viewport

logger = logging.getLogger(__name__)

# Engines whose full-page screenshot coordinates start at the page's scroll
# origin, even on a right-to-left page (see overflow_clip). Every other name
# runs on Chromium (the worker launches unknown names as chrome).
_ORIGIN_AT_SCROLL_START = frozenset({"firefox", "webkit"})

SCREENSHOT_STYLE = """
*, *::before, *::after {
    animation-duration: 0.001s !important;
    animation-delay: 0s !important;
    transition-duration: 0.001s !important;
    transition-delay: 0s !important;
}

[data-aos],
[data-scroll],
[data-animate],
.wow,
.animate__animated,
.aos-init:not(.aos-animate),
.animate-on-scroll,
.fade-in:not(.visible),
.fade-up:not(.visible),
.slide-up:not(.visible),
.slide-in:not(.visible),
.reveal:not(.revealed),
.scroll-animate:not(.animated),
.lazy-animate,
.is-hidden-scroll,
.before-animate,
.gsap-hidden {
    opacity: 1 !important;
    visibility: visible !important;
    transform: none !important;
    clip-path: none !important;
    pointer-events: auto !important;
}
"""

# The document size exactly as Playwright's full-page screenshot measures it
# (Screenshotter._fullPageSize), the layout viewport width, whether html/body
# hide their horizontal overflow, and the document direction.
JS_PAGE_METRICS = """() => { /*qa:page-metrics*/
    const html = document.documentElement;
    const body = document.body;
    if (!html || !body) {
        return null;
    }
    const computed = (el) => {
        try {
            return window.getComputedStyle(el);
        } catch (_) {
            return null;
        }
    };
    const hidesOverflowX = (el) => {
        const style = computed(el);
        return !!style && (style.overflowX === 'hidden' || style.overflowX === 'clip');
    };
    let dir = (document.dir || '').toLowerCase();
    if (dir !== 'rtl' && dir !== 'ltr') {
        const style = computed(html);
        dir = style && style.direction === 'rtl' ? 'rtl' : 'ltr';
    }
    return {
        docWidth: Math.max(
            body.scrollWidth, html.scrollWidth,
            body.offsetWidth, html.offsetWidth,
            body.clientWidth, html.clientWidth
        ),
        docHeight: Math.max(
            body.scrollHeight, html.scrollHeight,
            body.offsetHeight, html.offsetHeight,
            body.clientHeight, html.clientHeight
        ),
        innerWidth: window.innerWidth,
        clientWidth: html.clientWidth,
        overflowXHidden: hidesOverflowX(html) || hidesOverflowX(body),
        dir,
    };
}"""


@dataclass(frozen=True)
class PageMetrics:
    doc_width: int
    doc_height: int
    inner_width: int
    overflow_hidden: bool = False
    rtl: bool = False

    @property
    def megapixels(self) -> float:
        return self.doc_width * self.doc_height / 1_000_000


def _int(raw: dict[str, Any], key: str) -> int:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return 0
    if not math.isfinite(value):
        return 0
    return max(0, int(value))


def page_metrics_from_raw(raw: object, *, mobile: bool = False) -> PageMetrics | None:
    """The metrics a ``/*qa:page-metrics*/`` result describes; None when it
    is not a dict or carries no usable size. ``inner_width`` is the layout
    viewport width: the root's clientWidth under *mobile* emulation (no
    scrollbars there, and innerWidth grows with the content Chrome zooms out
    to), else innerWidth unless clientWidth is narrower by more than a
    scrollbar (cfg.LAYOUT_SCROLLBAR_MAX_PX)."""
    if not isinstance(raw, dict):
        return None
    inner_width = _int(raw, "innerWidth")
    client_width = _int(raw, "clientWidth")
    if 0 < client_width < inner_width and (
        mobile or client_width < inner_width - cfg.LAYOUT_SCROLLBAR_MAX_PX
    ):
        inner_width = client_width
    metrics = PageMetrics(
        doc_width=_int(raw, "docWidth"),
        doc_height=_int(raw, "docHeight"),
        inner_width=inner_width,
        overflow_hidden=raw.get("overflowXHidden") is True,
        rtl=str(raw.get("dir") or "").lower() == "rtl",
    )
    if metrics.doc_width <= 0 or metrics.inner_width <= 0:
        return None
    return metrics


async def measure_page(run: CaptureRun) -> PageMetrics | None:
    """Page metrics right before the screenshot; None when the page did not
    answer (the screenshot then runs uncropped with the default timeout)."""
    try:
        raw = await jsutil.evaluate(run.page, JS_PAGE_METRICS)
    except Exception as exc:
        logger.warning("page metrics unavailable for %s: %s", run.url, exc)
        return None
    return page_metrics_from_raw(raw, mobile=run.profile.is_mobile)


def overflow_clip(
    metrics: PageMetrics | None, viewport: Viewport, *, browser: str = "chrome"
) -> dict | None:
    """The full-page clip that crops content wider than the layout viewport
    to it, or None when the page fits. The clip is twice the document height:
    Playwright trims it to the page it measures itself.

    A right-to-left page starts on the right and overflows to the left. In
    Chromium's screenshot coordinates that overflow comes first, so the clip
    starts docWidth - innerWidth in; Firefox and WebKit place x=0 at the
    page's scroll origin (the left-hand overflow is not capturable there), so
    the clip starts at 0 like for a left-to-right page."""
    if metrics is None:
        return None
    if metrics.doc_width <= metrics.inner_width + cfg.OVERFLOW_TOLERANCE_PX:
        return None
    x = 0
    if metrics.rtl and browser.lower() not in _ORIGIN_AT_SCROLL_START:
        x = metrics.doc_width - metrics.inner_width
    height = max(metrics.doc_height, int(viewport.get("height", 0) or 0), 1) * 2
    return {"x": x, "y": 0, "width": metrics.inner_width, "height": height}


def _cap_to_budget(run: CaptureRun, timeout_ms: int) -> int:
    remaining_s = run.deadline.remaining_s()
    if math.isfinite(remaining_s):
        timeout_ms = min(timeout_ms, int(remaining_s * 1000))
    # Never 0: Playwright reads a zero timeout as "no timeout".
    return max(timeout_ms, 1000)


def screenshot_timeout_ms(run: CaptureRun, metrics: PageMetrics | None) -> int:
    """max(project timeout, SCREENSHOT_MIN_TIMEOUT_MS, SCREENSHOT_MS_PER_MEGAPIXEL
    x document megapixels), capped by what is left of the attempt budget."""
    timeout_ms = max(run.config.timeout, cfg.SCREENSHOT_MIN_TIMEOUT_MS)
    if metrics is not None:
        per_size = math.ceil(cfg.SCREENSHOT_MS_PER_MEGAPIXEL * metrics.megapixels)
        timeout_ms = max(timeout_ms, per_size)
    return _cap_to_budget(run, timeout_ms)


def screenshot_kwargs(run: CaptureRun, *, timeout_ms: int) -> dict[str, Any]:
    config = run.config
    kwargs: dict[str, Any] = {
        "full_page": True,
        "type": config.fmt,
        "timeout": timeout_ms,
        "animations": "disabled",
        "caret": "hide",
        "style": SCREENSHOT_STYLE,
    }
    if config.fmt == "jpeg":
        kwargs["quality"] = config.quality
    return kwargs


def _record(run: CaptureRun, metrics: PageMetrics | None, clip: dict | None) -> None:
    page_meta: dict[str, Any] = {
        "viewport_width": run.viewport["width"],
        "viewport_height": run.viewport["height"],
        "clipped_width": clip["width"] if clip is not None else None,
    }
    if metrics is not None:
        page_meta["document_width"] = metrics.doc_width
        page_meta["document_height"] = metrics.doc_height
    run.report.set_meta("page", **page_meta)
    if clip is None or metrics is None:
        return
    run.report.warn(
        "overflow_cropped",
        f"Page content is {metrics.doc_width} px wide; the screenshot was "
        f"cropped to the {metrics.inner_width} px viewport",
        level="info" if metrics.overflow_hidden else "warning",
        document_width=metrics.doc_width,
        viewport_width=metrics.inner_width,
    )


async def _retry_screenshot(
    run: CaptureRun,
    kwargs: dict[str, Any],
    *,
    unstable: bool,
    first_error: PlaywrightTimeoutError,
) -> tuple[bytes, int]:
    """The in-place screenshot retry after a timeout; returns the image and
    the timeout it ran with."""
    timeout_ms = kwargs["timeout"]
    remaining_s = run.deadline.remaining_s()
    if remaining_s < cfg.SCREENSHOT_RETRY_MIN_S:
        run.report.warn(
            "recovery_skipped",
            "Screenshot retry skipped: not enough time left",
            step="screenshot_retry",
            remaining_s=round(remaining_s, 1),
            outcome="budget",
            samples=[
                {
                    "step": "screenshot_retry",
                    "remaining_s": round(remaining_s, 1),
                    "outcome": "budget",
                }
            ],
        )
        raise ScreenshotTimeoutError(
            f"Screenshot timed out after {timeout_ms} ms"
        ) from first_error
    logger.warning(
        "screenshot of %s timed out after %d ms, retrying in place",
        run.url,
        timeout_ms,
    )
    run.report.warn(
        "screenshot_retried",
        f"The screenshot timed out after {timeout_ms} ms and was retried",
        timeout_ms=timeout_ms,
    )
    await run.page.wait_for_timeout(cfg.SCREENSHOT_RETRY_PAUSE_MS)
    await prepare.final_touches(run, unstable=unstable)
    retry_kwargs = {**kwargs, "timeout": _cap_to_budget(run, timeout_ms)}
    try:
        data = await run.page.screenshot(**retry_kwargs)
    except PlaywrightTimeoutError as exc:
        raise ScreenshotTimeoutError(
            f"Screenshot timed out twice (after {timeout_ms} ms and "
            f"{retry_kwargs['timeout']} ms)"
        ) from exc
    return data, retry_kwargs["timeout"]


async def take_snapshot(run: CaptureRun, *, unstable: bool = False) -> SnapshotResult:
    """Full-page screenshot of ``run.page`` in the configured format.

    Content wider than the layout viewport (``window.innerWidth``, or the
    root's clientWidth where emulation widened innerWidth — not the
    configured width: an emulated mobile page without a meta viewport lays
    out at 980 px) is cropped to it with an ``overflow_cropped`` warning (info
    when html/body hide the overflow). The timeout scales with the document
    size within the attempt budget; a timed-out screenshot is retried once
    after the final touches are re-applied (*unstable* picks their variant),
    and a second timeout raises ScreenshotTimeoutError. Writes the ``page``
    meta section.
    """
    metrics = await measure_page(run)
    clip = overflow_clip(metrics, run.viewport, browser=run.config.browser)
    _record(run, metrics, clip)
    kwargs = screenshot_kwargs(run, timeout_ms=screenshot_timeout_ms(run, metrics))
    if clip is not None:
        kwargs["clip"] = clip
    retried = False
    try:
        data = await run.page.screenshot(**kwargs)
        timeout_ms = kwargs["timeout"]
    except PlaywrightTimeoutError as exc:
        data, timeout_ms = await _retry_screenshot(
            run, kwargs, unstable=unstable, first_error=exc
        )
        retried = True
    return SnapshotResult(
        data=data,
        document_width=metrics.doc_width if metrics is not None else 0,
        document_height=metrics.doc_height if metrics is not None else 0,
        clip_width=clip["width"] if clip is not None else None,
        retried=retried,
        layout_width=metrics.inner_width if metrics is not None else 0,
        overflow_hidden=metrics.overflow_hidden if metrics is not None else False,
        rtl=metrics.rtl if metrics is not None else False,
        timeout_ms=timeout_ms,
    )
