"""Prepare stage: page transforms (overlays, lazy content, fixed backgrounds,
videos) and the two scroll passes that trigger lazy loading.

Every script here carries a ``/*qa:<name>*/`` marker (see jsutil).
"""

import logging

from playwright.async_api import Page

from app.screenshots.capture import config as cfg
from app.screenshots.capture import jsutil, network, readiness
from app.screenshots.capture.errors import PageStabilizationTimeoutError
from app.screenshots.capture.network import InflightTracker
from app.screenshots.capture.types import CaptureRun, PrepareResult

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

# Full selector list sourced from screenshot_viewport_patch.py.
# Covers cookie/GDPR banners, popular consent managers, and chat widgets, plus
# the backdrops behind them, open modal <dialog>s and the scroll lock they set.
# Backdrops are found by geometry, not by name: a generic [class*="overlay"]
# would also hide decorative layers (.elementor-background-overlay, hero tints).
JS_HIDE_OVERLAYS = """() => { /*qa:hide-overlays*/
    const MIN_Z_INDEX = 10;
    const MIN_COVERAGE = 0.9;
    const MAX_SUBTREE_SHARE = 0.5;

    const SELECTORS = [
        '[class*="cookie"]',       '[id*="cookie"]',
        '[class*="gdpr"]',         '[id*="gdpr"]',
        '[class*="consent"]',      '[id*="consent"]',
        '[class*="privacy-banner"]',
        '[class*="cc-banner"]',    '[id*="cc-banner"]',
        '#CybotCookiebotDialog',
        '#onetrust-banner-sdk',    '.onetrust-pc-dark-filter',
        '#cookielaw-banner',       '.cookie-law-info-bar',
        '#cookie-notice',          '.cookie-notice',
        '.pea_cook_wrapper',
        '[class*="chat-widget"]',  '[id*="chat-widget"]',
        '[class*="chat-button"]',  '[id*="chat-button"]',
        '[class*="livechat"]',     '[id*="livechat"]',
        '#tidio-chat',             '#tidio-chat-iframe',
        '#crisp-chatbox',          '.crisp-client',
        '#intercom-container',     '.intercom-lightweight-app',
        '#hubspot-messages-iframe-container',
        '#drift-widget-container', '.drift-frame-controller',
        '#fc_frame',               '.freshchat-widget',
        '#ze-snippet',             '.zEWidget-launcher',
        '#jvlabelWrap',            '#jcont',
        '.chatra--webkit',         '#chatra',
        '.tawkto-chat-widget',
        '[class*="popup"]',        '[id*="popup"]',
        '[class*="modal"]',        '[id*="modal"]',
        '[class*="banner"]',       '[id*="banner"]',
        // Backdrops the substrings above miss (class match is case-sensitive)
        '.cky-overlay',            '.mfp-bg',
        '.fancybox-overlay',       '.pum-overlay',
        '.truste_overlay',         '.swal2-container',
        '.MuiBackdrop-root',       '.ReactModal__Overlay',
    ];
    SELECTORS.forEach(sel => {
        try {
            document.querySelectorAll(sel).forEach(el => {
                // Lock classes like body.modal-open / body.cookies-not-set
                // match too; hiding <html>/<body> would blank the page.
                if (el === document.documentElement || el === document.body) {
                    return;
                }
                el.style.setProperty('display', 'none', 'important');
            });
        } catch (_) {}
    });

    // ── Native modal dialogs (closing also removes their ::backdrop) ──
    document.querySelectorAll('dialog').forEach(d => {
        try {
            if (d.open && d.matches(':modal')) d.close();
        } catch (_) {}
    });

    // ── Generic backdrops: a visible, click-blocking fixed layer stacked
    // above the page that covers the viewport but is not the page shell.
    // Decorative fixed layers (grain, vignette) use pointer-events: none.
    const vw = document.documentElement.clientWidth || window.innerWidth;
    const vh = document.documentElement.clientHeight || window.innerHeight;
    const total = document.body ? document.body.getElementsByTagName('*').length : 0;
    document.querySelectorAll('body *').forEach(el => {
        try {
            const cs = window.getComputedStyle(el);
            if (cs.position !== 'fixed') return;
            if (cs.display === 'none' || cs.visibility === 'hidden') return;
            if (parseFloat(cs.opacity) === 0) return;
            if (cs.pointerEvents === 'none') return;
            if (!(parseInt(cs.zIndex, 10) >= MIN_Z_INDEX)) return;
            const r = el.getBoundingClientRect();
            const w = Math.min(r.right, vw) - Math.max(r.left, 0);
            const h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
            if (w <= 0 || h <= 0 || (w * h) / (vw * vh) < MIN_COVERAGE) return;
            if (el.getElementsByTagName('*').length > total * MAX_SUBTREE_SHARE) {
                return;
            }
            el.style.setProperty('display', 'none', 'important');
            el.setAttribute('data-qa-overlay-hidden', '1');
        } catch (_) {}
    });

    // ── Scroll lock left by the popup. overflow-x stays: themes set
    // overflow-x: hidden on purpose to clip off-canvas menus.
    [document.documentElement, document.body].forEach(el => {
        if (!el) return;
        try {
            const cs = window.getComputedStyle(el);
            if (cs.overflowY === 'hidden' || cs.overflowY === 'clip') {
                el.style.setProperty('overflow-y', 'visible', 'important');
            }
            if (el === document.body && cs.position === 'fixed') {
                el.style.setProperty('position', 'static', 'important');
                el.style.setProperty('top', 'auto', 'important');
            }
        } catch (_) {}
    });
}"""


JS_TRIGGER_LAZY = """() => { /*qa:trigger-lazy*/
    // ── Native lazy loading ──────────────────────────────────────
    document.querySelectorAll('img[loading="lazy"]').forEach(img => {
        img.loading = 'eager';
    });
    document.querySelectorAll('iframe[loading="lazy"]').forEach(el => {
        el.loading = 'eager';
    });

    // ── data-src variants (lazysizes, vanilla-lazyload, lozad) ──
    document.querySelectorAll('img[data-src]').forEach(img => {
        if (!img.src || img.src.includes('data:') ||
            img.src.includes('placeholder') || img.naturalWidth === 0) {
            img.src = img.dataset.src;
        }
    });
    document.querySelectorAll('img[data-srcset]').forEach(img => {
        img.srcset = img.dataset.srcset;
    });
    document.querySelectorAll('source[data-srcset]').forEach(s => {
        s.srcset = s.dataset.srcset;
    });

    // ── jQuery Lazy Load / Slick carousel ────────────────────────
    document.querySelectorAll('img[data-original]').forEach(img => {
        img.src = img.dataset.original;
    });
    document.querySelectorAll('img[data-lazy]').forEach(img => {
        img.src = img.dataset.lazy;
    });
    document.querySelectorAll('img[data-lazy-src]').forEach(img => {
        img.src = img.dataset.lazySrc;
    });

    // ── Background images (WordPress themes) ─────────────────────
    document.querySelectorAll('[data-bg]').forEach(el => {
        el.style.backgroundImage = 'url(' + el.dataset.bg + ')';
    });
    document.querySelectorAll('[data-background]').forEach(el => {
        el.style.backgroundImage = 'url(' + el.dataset.background + ')';
    });

    // ── Swap lazy classes ─────────────────────────────────────────
    document.querySelectorAll('.lazy, .lazyload').forEach(el => {
        el.classList.remove('lazy', 'lazyload');
        el.classList.add('lazyloaded', 'loaded');
    });

    // ── Trigger animation libraries ───────────────────────────────
    try { if (window.AOS) AOS.refreshHard(); } catch(e) {}
    try { if (window.ScrollTrigger) ScrollTrigger.refresh(true); } catch(e) {}
    try {
        document.querySelectorAll('.wow').forEach(el => {
            el.classList.add('animated');
            el.style.visibility = 'visible';
        });
    } catch(e) {}

    // ── Dispatch scroll events for custom listeners ───────────────
    window.dispatchEvent(new Event('scroll', { bubbles: true }));
    document.dispatchEvent(new Event('scroll', { bubbles: true }));
}"""


JS_FORCE_WP_ROCKET_LAZY_RENDER = """() => { /*qa:wp-rocket-render*/
    document.querySelectorAll('[data-wpr-lazyrender="1"]').forEach(el => {
        try {
            el.style.setProperty('content-visibility', 'visible', 'important');
            el.style.setProperty('contain-intrinsic-size', 'auto', 'important');
            el.style.setProperty('contain', 'none', 'important');
            el.setAttribute('data-qa-wpr-force-render', '1');
        } catch (_) {}
    });
    window.dispatchEvent(new Event('scroll', { bubbles: true }));
    window.dispatchEvent(new Event('resize', { bubbles: true }));
}"""


# Cheap one-shot probe: does this page have any deferred/lazy content that needs
# the generous scroll-and-settle profile? Mirrors the selectors handled by
# JS_TRIGGER_LAZY / JS_FORCE_WP_ROCKET_LAZY_RENDER plus WP Rocket lazy-render.
# Native img[loading="lazy"] is deliberately NOT a trigger: WordPress puts it on
# nearly every image, and JS_TRIGGER_LAZY flips those to eager before any
# scrolling, so they start loading without the slow profile.
JS_DETECT_LAZY_CONTENT = """() => { /*qa:detect-lazy*/
    const sel = [
        'iframe[loading="lazy"]',
        'img[data-src]',
        'img[data-srcset]',
        'source[data-srcset]',
        'img[data-original]',
        'img[data-lazy]',
        'img[data-lazy-src]',
        '[data-bg]',
        '[data-background]',
        '.lazy',
        '.lazyload',
        '[data-wpr-lazyrender="1"]',
    ];
    for (const s of sel) {
        if (document.querySelector(s)) return true;
    }
    return false;
}"""


JS_NORMALIZE_FIXED_BACKGROUNDS = """() => { /*qa:normalize-fixed-bg*/
    const normalizeAttachment = (attachment) => {
        const values = attachment.split(',').map((value) => value.trim());
        let changed = false;
        const normalized = values.map((value) => {
            if (value.toLowerCase() === 'fixed') {
                changed = true;
                return 'local';
            }
            return value;
        });
        return changed ? normalized.join(', ') : null;
    };

    document.querySelectorAll('*').forEach((el) => {
        try {
            const attachment = window.getComputedStyle(el).backgroundAttachment;
            if (!attachment) {
                return;
            }
            const nextAttachment = normalizeAttachment(attachment);
            if (nextAttachment) {
                el.style.setProperty(
                    'background-attachment',
                    nextAttachment,
                    'important'
                );
                el.setAttribute('data-qa-fixed-bg-normalized', '1');
            }
        } catch (_) {}
    });
}"""


# Init script (added to every capture context, see profiles), not evaluated.
JS_IO_OVERRIDE = """
(function() { /*qa:io-override*/
    const NativeIO = window.IntersectionObserver;
    window.IntersectionObserver = function(callback, options) {
        const instance = {
            _els: new Set(),
            root: options?.root || null,
            rootMargin: options?.rootMargin || '0px',
            thresholds: options?.threshold
                ? (Array.isArray(options.threshold)
                    ? options.threshold : [options.threshold])
                : [0],
            observe: function(el) {
                this._els.add(el);
                const rect = el.getBoundingClientRect();
                setTimeout(() => callback([{
                    isIntersecting: true,
                    intersectionRatio: 1.0,
                    intersectionRect: rect,
                    boundingClientRect: rect,
                    rootBounds: null,
                    target: el,
                    time: performance.now()
                }], instance), 0);
            },
            unobserve: function(el) { this._els.delete(el); },
            disconnect: function() { this._els.clear(); },
            takeRecords: function() { return []; }
        };
        return instance;
    };
    window.IntersectionObserver.prototype = {};
})();
"""


# Pauses videos that already have a frame (readyState >= HAVE_CURRENT_DATA):
# the screenshot still shows a real video frame, but the decoder stops burning
# CPU through the long scroll/settle phase. Videos without a frame keep loading.
JS_PAUSE_VIDEOS = """() => { /*qa:pause-videos*/
    document.querySelectorAll('video').forEach((video) => {
        try {
            if (!video.paused && video.readyState >= 2) {
                video.pause();
            }
        } catch (_) {}
    });
}"""


# Scroll helpers (were inline expressions; same effect, now marked functions).
JS_SCROLL_TO = "(y) => { /*qa:scroll-to*/ window.scrollTo(0, y); }"
JS_SCROLL_HEIGHT = "() => { /*qa:scroll-height*/ return document.body.scrollHeight; }"
JS_SCROLL_TO_BOTTOM = (
    "() => { /*qa:scroll-to-bottom*/ window.scrollTo(0, document.body.scrollHeight); }"
)


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------


async def scroll_pass(
    page: Page,
    step: int,
    *,
    tracker: InflightTracker | None,
    min_ms: float,
    quiet_ms: float,
    max_ms: float,
) -> int:
    """Single top-to-bottom scroll pass; returns the number of positions
    visited.

    Starts at y=0 and advances by *step* pixels per iteration. At each position
    it waits (adaptively, see network.wait_for_quiet) for the requests the
    scroll triggered, capped at *max_ms*.
    """
    await jsutil.evaluate(page, JS_SCROLL_TO, 0)
    y = 0
    steps = 0
    for _ in range(cfg.MAX_SCROLL_STEPS):
        total_height: int = await jsutil.evaluate(page, JS_SCROLL_HEIGHT)
        if y >= total_height:
            return steps
        await jsutil.evaluate(page, JS_SCROLL_TO, y)
        await network.wait_for_quiet(
            page, tracker, min_ms=min_ms, quiet_ms=quiet_ms, max_ms=max_ms
        )
        y += step
        steps += 1
    logger.warning("scroll pass stopped after %d steps", cfg.MAX_SCROLL_STEPS)
    return steps


async def scroll_to_trigger_lazy(
    page: Page,
    viewport_height: int,
    *,
    has_lazy_content: bool,
    tracker: InflightTracker | None = None,
) -> int:
    """Scroll the page twice to ensure all lazy-loaded content is captured;
    returns the number of scroll positions visited over both passes.

    Pass 1 — triggers lazy-load initiation.
    Interlude — wait for the triggered requests so newly loaded content has
                time to render before the second pass.
    Pass 2 — content is loaded; ensures everything is in view before the final
             screenshot.

    When *has_lazy_content* is False (no lazy markers detected on the page) the
    shorter "fast" caps are used. Either way every pause is adaptive: it ends
    once the page's tracked requests have gone quiet, and content readiness is
    still guaranteed afterwards by the readiness stage.

    Step size is half the viewport height (min 300 px) so IntersectionObserver
    elements with any rootMargin are not skipped between jumps.
    """
    step = max(300, viewport_height // 2)
    if has_lazy_content:
        pass1_ms = cfg.SCROLL_PAUSE_LAZY_PASS1_MS
        pass2_ms = cfg.SCROLL_PAUSE_LAZY_PASS2_MS
        settle_ms = cfg.SETTLE_PROBE_LAZY_MS
        networkidle_ms = cfg.NETWORKIDLE_PROBE_LAZY_MS
        pass1_min_ms = cfg.STEP_MIN_LAZY_PASS1_MS
    else:
        pass1_ms = cfg.SCROLL_PAUSE_FAST_PASS1_MS
        pass2_ms = cfg.SCROLL_PAUSE_FAST_PASS2_MS
        settle_ms = cfg.SETTLE_PROBE_FAST_MS
        networkidle_ms = cfg.NETWORKIDLE_PROBE_FAST_MS
        pass1_min_ms = cfg.STEP_MIN_FAST_PASS1_MS

    async def settle() -> None:
        await network.wait_for_quiet(
            page,
            tracker,
            min_ms=cfg.SETTLE_MIN_MS,
            quiet_ms=cfg.SETTLE_QUIET_MS,
            max_ms=settle_ms + networkidle_ms,
        )

    # Pass 1: trigger lazy-load
    steps = await scroll_pass(
        page,
        step,
        tracker=tracker,
        min_ms=pass1_min_ms,
        quiet_ms=cfg.STEP_QUIET_MS,
        max_ms=pass1_ms,
    )

    # Wait for triggered content to load
    await settle()

    # Pass 2: content is loaded — ensure everything rendered
    steps += await scroll_pass(
        page,
        step,
        tracker=tracker,
        min_ms=cfg.STEP_MIN_PASS2_MS,
        quiet_ms=cfg.STEP_QUIET_PASS2_MS,
        max_ms=pass2_ms,
    )

    # Settle at bottom, then scroll back to top
    await jsutil.evaluate(page, JS_SCROLL_TO_BOTTOM)
    await settle()
    await jsutil.evaluate(page, JS_SCROLL_TO, 0)
    await network.wait_for_quiet(
        page,
        tracker,
        min_ms=cfg.SETTLE_MIN_MS,
        quiet_ms=cfg.STEP_QUIET_MS,
        max_ms=min(settle_ms, cfg.SETTLE_PROBE_FAST_MS * 2),
    )
    return steps


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


async def prepare_page(run: CaptureRun) -> PrepareResult:
    """Trigger lazy content and scroll the page through once (no readiness
    verdict). Never raises PageStabilizationTimeoutError, so a readiness retry
    can always run on an already scrolled page."""
    page = run.page
    settle_timeout_ms = cfg.stabilization_timeout_ms(run.config, retry=run.attempt > 0)
    await jsutil.evaluate(page, JS_HIDE_OVERLAYS)
    await jsutil.evaluate(page, JS_PAUSE_VIDEOS)
    try:
        await readiness.wait_for_fonts(
            page, min(settle_timeout_ms, cfg.FONTS_WAIT_CAP_MS)
        )
    except PageStabilizationTimeoutError:
        # Fonts are awaited again by readiness.finalize, which owns the verdict.
        logger.debug("fonts not ready before scrolling — continuing")

    # Detect lazy markers BEFORE triggering, since JS_TRIGGER_LAZY consumes
    # them (swaps data-src→src, strips lazy classes). Pages with no lazy content
    # use the much faster scroll/settle timing profile.
    has_lazy_content = bool(await jsutil.evaluate(page, JS_DETECT_LAZY_CONTENT))
    post_trigger_ms = (
        cfg.POST_TRIGGER_WAIT_LAZY_MS
        if has_lazy_content
        else cfg.POST_TRIGGER_WAIT_FAST_MS
    )

    await jsutil.evaluate(page, JS_TRIGGER_LAZY)
    await jsutil.evaluate(page, JS_FORCE_WP_ROCKET_LAZY_RENDER)
    await jsutil.evaluate(page, JS_NORMALIZE_FIXED_BACKGROUNDS)
    await page.wait_for_timeout(post_trigger_ms)

    scroll_steps = await scroll_to_trigger_lazy(
        page,
        run.viewport["height"],
        has_lazy_content=has_lazy_content,
        tracker=run.tracker,
    )
    await jsutil.evaluate(page, JS_PAUSE_VIDEOS)
    return PrepareResult(
        has_lazy_content=has_lazy_content,
        scroll_steps=scroll_steps,
        pass2_ran=True,
    )


async def reapply_transforms(run: CaptureRun) -> None:
    """Re-trigger lazy content and re-apply the page transforms right before
    a readiness verdict (content added since prepare gets them too)."""
    page = run.page
    await jsutil.evaluate(page, JS_TRIGGER_LAZY)
    await jsutil.evaluate(page, JS_FORCE_WP_ROCKET_LAZY_RENDER)
    await jsutil.evaluate(page, JS_NORMALIZE_FIXED_BACKGROUNDS)
    await jsutil.evaluate(page, JS_HIDE_OVERLAYS)


async def prepare_unstable(run: CaptureRun) -> None:
    """Best-effort cleanup before capturing a page whose DOM never settled."""
    page = run.page
    await jsutil.evaluate(page, JS_HIDE_OVERLAYS)
    await jsutil.evaluate(page, JS_NORMALIZE_FIXED_BACKGROUNDS)
    await page.wait_for_timeout(cfg.UNSTABLE_SETTLE_MS)


async def final_touches(run: CaptureRun, *, unstable: bool) -> None:
    """Last transforms right before the screenshot: hide overlays again and
    let the page settle (shorter cleanup for an *unstable* page), then pause
    videos and normalize fixed backgrounds."""
    page = run.page
    if unstable:
        await prepare_unstable(run)
    else:
        await jsutil.evaluate(page, JS_HIDE_OVERLAYS)
        await page.wait_for_timeout(cfg.FINAL_SETTLE_MS)
    await jsutil.evaluate(page, JS_PAUSE_VIDEOS)
    await jsutil.evaluate(page, JS_NORMALIZE_FIXED_BACKGROUNDS)
