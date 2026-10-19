import asyncio
import logging
import os
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from playwright.async_api import Browser, Page, Request, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import User
from app.core.exceptions import ConflictError, NotFoundError
from app.screenshots.image_dimensions import read_image_dimensions_from_bytes
from app.screenshots.models import Screenshot
from app.screenshots.storage import (
    abs_file_path,
    build_file_path,
    ensure_parent,
    generate_signed_url,
)

logger = logging.getLogger(__name__)

# Playwright waits for document.fonts.ready inside page.screenshot(). That wait
# is unbounded except by the screenshot action timeout and is flaky in WebKit on
# pages with problematic web fonts. We already do a bounded font readiness probe
# before taking the screenshot, so do not let the final screenshot block on it.
os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")

_RETRY_COUNT = 1
_RETRY_DELAY_S = 2.0  # seconds between attempts
_STABILITY_SAMPLE_MS = 500
_STABILITY_REQUIRED_CYCLES = 3
_MIN_SETTLE_TIMEOUT_MS = 8000
_MAX_SETTLE_TIMEOUT_MS = 25000
_RETRY_SETTLE_MULTIPLIER = 1.5

# Scroll/settle tuning. The fixed pauses below only exist to *trigger* lazy
# loads as we scroll; the actual wait-for-readiness is handled adaptively by
# _decode_visible_images and _wait_for_dom_stability. So pages with no lazy
# content can use the fast profile and finish in a fraction of the time, while
# lazy-heavy pages keep the generous "slow" pauses they need.
_SCROLL_PAUSE_LAZY_PASS1_MS = 2000  # slow lazy page, first (trigger) pass
_SCROLL_PAUSE_LAZY_PASS2_MS = 1000  # slow lazy page, second (render) pass
_SCROLL_PAUSE_FAST_PASS1_MS = 500  # no lazy content, first pass
_SCROLL_PAUSE_FAST_PASS2_MS = 300  # no lazy content, second pass
_SETTLE_PROBE_LAZY_MS = 2000  # interlude/bottom settle when lazy content present
_SETTLE_PROBE_FAST_MS = 500  # interlude/bottom settle when no lazy content
_NETWORKIDLE_PROBE_LAZY_MS = 5000  # non-fatal networkidle probe, lazy pages
_NETWORKIDLE_PROBE_FAST_MS = 1500  # non-fatal networkidle probe, no lazy content
_POST_TRIGGER_WAIT_LAZY_MS = 500  # pause after firing lazy triggers, lazy pages
_POST_TRIGGER_WAIT_FAST_MS = 150  # pause after firing lazy triggers, no lazy


# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

# Full selector list sourced from screenshot_viewport_patch.py.
# Covers cookie/GDPR banners, popular consent managers, and chat widgets.
_JS_HIDE_OVERLAYS = """() => {
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
    ];
    SELECTORS.forEach(sel => {
        try {
            document.querySelectorAll(sel).forEach(el => {
                el.style.setProperty('display', 'none', 'important');
            });
        } catch (_) {}
    });
}"""


_JS_TRIGGER_LAZY = """() => {
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


_JS_FORCE_WP_ROCKET_LAZY_RENDER = """() => {
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
# _JS_TRIGGER_LAZY / _JS_FORCE_WP_ROCKET_LAZY_RENDER plus WP Rocket lazy-render.
_JS_DETECT_LAZY_CONTENT = """() => {
    const sel = [
        'img[loading="lazy"]',
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


_JS_NORMALIZE_FIXED_BACKGROUNDS = """() => {
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


_JS_IO_OVERRIDE = """
(function() {
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


_JS_WAIT_FOR_FONTS = """async () => {
    if (!document.fonts || !document.fonts.ready) {
        return true;
    }
    await document.fonts.ready;
    return true;
}"""


_JS_DECODE_VISIBLE_IMAGES = """async (timeoutMs) => {
    const perAssetTimeout = Math.max(500, Math.min(timeoutMs || 5000, 5000));
    const missingSamples = [];

    const visible = (el) => {
        try {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return (
                rect.width > 1 &&
                rect.height > 1 &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                style.visibility !== 'collapse' &&
                Number(style.opacity || '1') > 0
            );
        } catch (_) {
            return false;
        }
    };

    const waitWithTimeout = (promise) => Promise.race([
        promise,
        new Promise((resolve) => setTimeout(() => resolve('timeout'), perAssetTimeout)),
    ]);

    const waitForElementImage = async (img) => {
        const src = img.currentSrc || img.src;
        if (!src) {
            return { ok: false, kind: 'img', url: '' };
        }
        if (!img.complete) {
            await waitWithTimeout(new Promise((resolve) => {
                const done = () => resolve(null);
                img.addEventListener('load', done, { once: true });
                img.addEventListener('error', done, { once: true });
            }));
        }
        if (img.complete && img.naturalWidth > 0 && img.decode) {
            try {
                await waitWithTimeout(img.decode());
            } catch (_) {}
        }
        return {
            ok: img.complete && img.naturalWidth > 0,
            kind: 'img',
            url: src,
        };
    };

    const extractBackgroundUrls = (backgroundImage) => {
        const urls = [];
        const re = /url\\((?:"([^"]*)"|'([^']*)'|([^)]*))\\)/g;
        let match;
        while ((match = re.exec(backgroundImage || '')) !== null) {
            const raw = (match[1] || match[2] || match[3] || '').trim();
            if (!raw) {
                continue;
            }
            try {
                urls.push(new URL(raw, document.baseURI).href);
            } catch (_) {}
        }
        return urls;
    };

    const waitForBackgroundUrl = async (url) => {
        const loaded = await waitWithTimeout(new Promise((resolve) => {
            const img = new Image();
            img.onload = () => resolve(true);
            img.onerror = () => resolve(false);
            img.src = url;
            if (img.complete && img.naturalWidth > 0) {
                resolve(true);
            }
        }));
        return {
            ok: loaded === true,
            kind: 'background',
            url,
        };
    };

    const allImages = Array.from(document.images).filter((img) => {
        const src = img.currentSrc || img.src;
        return !!src;
    });
    const visibleImages = allImages.filter(visible);
    const hiddenPendingImages = allImages.filter((img) => (
        !visible(img) && (!img.complete || img.naturalWidth === 0)
    ));

    const backgroundUrls = [];
    Array.from(document.querySelectorAll('*')).forEach((el) => {
        if (!visible(el)) {
            return;
        }
        try {
            extractBackgroundUrls(window.getComputedStyle(el).backgroundImage)
                .forEach((url) => backgroundUrls.push(url));
        } catch (_) {}
    });
    const uniqueBackgroundUrls = Array.from(new Set(backgroundUrls));

    const imageResults = await Promise.allSettled(
        visibleImages.map((img) => waitForElementImage(img))
    );
    const backgroundResults = await Promise.allSettled(
        uniqueBackgroundUrls.map((url) => waitForBackgroundUrl(url))
    );

    const normalizeResults = (results) => results
        .filter((result) => result.status === 'fulfilled')
        .map((result) => result.value);

    const missingImages = normalizeResults(imageResults).filter((item) => !item.ok);
    const missingBackgrounds = normalizeResults(backgroundResults)
        .filter((item) => !item.ok);
    missingImages.concat(missingBackgrounds).slice(0, 5).forEach((item) => {
        missingSamples.push({ kind: item.kind, url: item.url });
    });

    return {
        visibleImageCount: visibleImages.length,
        visibleBackgroundCount: uniqueBackgroundUrls.length,
        missingVisibleImageCount: missingImages.length,
        missingBackgroundImageCount: missingBackgrounds.length,
        hiddenPendingImageCount: hiddenPendingImages.length,
        missingSamples,
    };
}"""


_JS_COLLECT_STABILITY_STATE = """() => {
    const visible = (el) => {
        try {
            const rect = el.getBoundingClientRect();
            const style = window.getComputedStyle(el);
            return (
                rect.width > 1 &&
                rect.height > 1 &&
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                style.visibility !== 'collapse' &&
                Number(style.opacity || '1') > 0
            );
        } catch (_) {
            return false;
        }
    };

    const pendingImages = Array.from(document.images).filter((img) => {
        const src = img.currentSrc || img.src;
        return src && (!img.complete || img.naturalWidth === 0);
    });
    const pendingVisibleImages = pendingImages.filter(visible).length;
    const hiddenPendingImages = pendingImages.length - pendingVisibleImages;

    const pendingLazyRender = Array.from(
        document.querySelectorAll('[data-wpr-lazyrender="1"]')
    ).filter((el) => {
        try {
            return window.getComputedStyle(el).contentVisibility === 'auto';
        } catch (_) {
            return false;
        }
    }).length;

    return {
        readyState: document.readyState,
        scrollHeight: Math.max(
            document.body ? document.body.scrollHeight : 0,
            document.documentElement ? document.documentElement.scrollHeight : 0
        ),
        pendingImages: pendingImages.length,
        pendingVisibleImages,
        hiddenPendingImages,
        pendingLazyRender,
        textLength: document.body && document.body.innerText
            ? document.body.innerText.length
            : 0,
    };
}"""


_SCREENSHOT_STYLE = """
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


# ---------------------------------------------------------------------------
# EffectiveConfig — resolved screenshot parameters for one task
# ---------------------------------------------------------------------------


@dataclass
class EffectiveConfig:
    """Resolved screenshot parameters after combining app and project settings."""

    browser: str
    fmt: str  # "jpeg" | "png"
    quality: int  # 0-100, jpeg only
    wait_until: str  # "load" | "domcontentloaded" | "networkidle"
    timeout: int  # milliseconds


class PageStabilizationTimeoutError(Exception):
    """Raised when page readiness heuristics fail to settle before screenshot."""


class PageAssetLoadError(Exception):
    """Raised when visible page assets are still missing after readiness checks."""


@dataclass(frozen=True)
class AssetReadinessResult:
    visible_image_count: int = 0
    visible_background_count: int = 0
    missing_visible_image_count: int = 0
    missing_background_image_count: int = 0
    hidden_pending_image_count: int = 0
    missing_samples: tuple[str, ...] = ()

    @property
    def missing_count(self) -> int:
        return self.missing_visible_image_count + self.missing_background_image_count

    @property
    def visible_asset_count(self) -> int:
        return self.visible_image_count + self.visible_background_count


@dataclass
class NetworkDiagnostics:
    failed_requests: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)

    def record_failed_request(self, request: Request) -> None:
        if len(self.failed_requests) >= _MAX_NETWORK_DIAGNOSTICS:
            return
        resource_type = str(getattr(request, "resource_type", ""))
        if resource_type not in _NETWORK_DIAGNOSTIC_TYPES:
            return
        failure = getattr(request, "failure", "")
        if callable(failure):
            failure = failure()
        self.failed_requests.append(
            f"{resource_type} {_shorten(str(failure or 'failed'), 80)} "
            f"{_shorten(str(getattr(request, 'url', '')), 180)}"
        )

    def record_response(self, response: Response) -> None:
        if len(self.bad_responses) >= _MAX_NETWORK_DIAGNOSTICS:
            return
        status = int(getattr(response, "status", 0) or 0)
        if status < 400:
            return
        request = getattr(response, "request", None)
        resource_type = str(getattr(request, "resource_type", ""))
        if resource_type not in _NETWORK_DIAGNOSTIC_TYPES:
            return
        self.bad_responses.append(
            f"{resource_type} HTTP {status} "
            f"{_shorten(str(getattr(response, 'url', '')), 180)}"
        )

    def summary(self) -> str | None:
        parts: list[str] = []
        if self.failed_requests:
            parts.append("failed requests: " + "; ".join(self.failed_requests))
        if self.bad_responses:
            parts.append("bad responses: " + "; ".join(self.bad_responses))
        if not parts:
            return None
        return "Network diagnostics: " + " | ".join(parts)


@dataclass
class CaptureOutcome:
    data: bytes
    duration: float
    captured_with_unstable_dom: bool = False
    warning_message: str | None = None


# ---------------------------------------------------------------------------
# Page helpers
# ---------------------------------------------------------------------------

_MAX_NETWORK_DIAGNOSTICS = 8
_NETWORK_DIAGNOSTIC_TYPES = {"image", "media", "font", "stylesheet"}
_WEBKIT_BROWSER_NAMES = {"webkit", "safari"}


def _shorten(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def _navigation_wait_until(config: EffectiveConfig) -> str:
    if config.browser.lower() in _WEBKIT_BROWSER_NAMES and config.wait_until == "load":
        return "domcontentloaded"
    return config.wait_until


def _navigation_wait_until_chain(config: EffectiveConfig) -> list[str]:
    """Ordered wait_until levels to try for navigation, heaviest → lightest.

    A `page.goto` that times out on a heavier condition (e.g. `load` blocked on a
    slow third-party subresource) falls through to a lighter one. The page's
    actual content readiness is still guaranteed afterwards by the scroll /
    decode / DOM-stability steps, so `domcontentloaded` is a safe final fallback;
    only a timeout there means the page is genuinely unreachable.
    """
    full_chain = ["networkidle", "load", "domcontentloaded"]
    start = _navigation_wait_until(config)
    if start not in full_chain:
        return [start]
    chain = full_chain[full_chain.index(start) :]
    # WebKit treats `load` like `domcontentloaded` (see _navigation_wait_until),
    # so collapse the two to avoid a redundant identical retry.
    if config.browser.lower() in _WEBKIT_BROWSER_NAMES:
        chain = [level for level in chain if level != "load"]
    return chain


def _stabilization_timeout_ms(config: EffectiveConfig, retry: bool = False) -> int:
    base = max(_MIN_SETTLE_TIMEOUT_MS, min(config.timeout, _MAX_SETTLE_TIMEOUT_MS))
    if retry:
        return min(int(base * _RETRY_SETTLE_MULTIPLIER), _MAX_SETTLE_TIMEOUT_MS)
    return base


async def _wait_for_fonts(page: Page, timeout_ms: int) -> None:
    try:
        await asyncio.wait_for(page.evaluate(_JS_WAIT_FOR_FONTS), timeout_ms / 1000)
    except TimeoutError as exc:
        msg = "Timed out waiting for fonts to be ready"
        raise PageStabilizationTimeoutError(msg) from exc


def _int_from_mapping(raw: dict[str, Any], key: str) -> int:
    try:
        return int(raw.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def _asset_readiness_from_raw(raw: object) -> AssetReadinessResult:
    if not isinstance(raw, dict):
        return AssetReadinessResult()

    samples: list[str] = []
    raw_samples = raw.get("missingSamples", [])
    if isinstance(raw_samples, list):
        for sample in raw_samples[:5]:
            if not isinstance(sample, dict):
                continue
            kind = str(sample.get("kind") or "asset")
            url = str(sample.get("url") or "")
            samples.append(f"{kind}: {_shorten(url, 180)}")

    return AssetReadinessResult(
        visible_image_count=_int_from_mapping(raw, "visibleImageCount"),
        visible_background_count=_int_from_mapping(raw, "visibleBackgroundCount"),
        missing_visible_image_count=_int_from_mapping(raw, "missingVisibleImageCount"),
        missing_background_image_count=_int_from_mapping(
            raw,
            "missingBackgroundImageCount",
        ),
        hidden_pending_image_count=_int_from_mapping(raw, "hiddenPendingImageCount"),
        missing_samples=tuple(samples),
    )


def _format_asset_failure(result: AssetReadinessResult) -> str:
    missing: list[str] = []
    if result.missing_visible_image_count:
        missing.append(f"{result.missing_visible_image_count} visible img")
    if result.missing_background_image_count:
        missing.append(f"{result.missing_background_image_count} visible background")
    message = "Visible page assets did not load: " + ", ".join(missing)
    if result.missing_samples:
        message += "; samples: " + "; ".join(result.missing_samples)
    if result.hidden_pending_image_count:
        message += (
            f"; ignored hidden pending images: {result.hidden_pending_image_count}"
        )
    return message


def _append_network_diagnostics(message: str, diagnostics: NetworkDiagnostics) -> str:
    summary = diagnostics.summary()
    if not summary:
        return message
    return f"{message}; {summary}"


def _is_blank_capture_state(
    state: dict[str, int | str],
    asset_state: AssetReadinessResult,
) -> bool:
    try:
        text_length = int(state.get("textLength", 0) or 0)
    except (TypeError, ValueError):
        text_length = 0
    return text_length == 0 and asset_state.visible_asset_count == 0


async def _decode_visible_images(page: Page, timeout_ms: int) -> AssetReadinessResult:
    try:
        raw = await asyncio.wait_for(
            page.evaluate(_JS_DECODE_VISIBLE_IMAGES, timeout_ms),
            timeout_ms / 1000,
        )
    except TimeoutError as exc:
        msg = "Timed out waiting for visible image/background assets"
        raise PageAssetLoadError(msg) from exc

    result = _asset_readiness_from_raw(raw)
    if result.hidden_pending_image_count:
        logger.debug(
            "ignoring %d hidden pending image(s) during asset readiness",
            result.hidden_pending_image_count,
        )
    if result.missing_count:
        raise PageAssetLoadError(_format_asset_failure(result))
    return result


async def _collect_stability_state(page: Page) -> dict[str, int | str]:
    state = await page.evaluate(_JS_COLLECT_STABILITY_STATE)
    if not isinstance(state, dict):
        return {
            "readyState": "unknown",
            "scrollHeight": 0,
            "pendingImages": 0,
            "pendingVisibleImages": 0,
            "hiddenPendingImages": 0,
            "pendingLazyRender": 0,
            "textLength": 0,
        }
    return state


def _same_stability_state(
    previous: dict[str, int | str] | None,
    current: dict[str, int | str],
) -> bool:
    if previous is None:
        return False
    keys = (
        "readyState",
        "scrollHeight",
        "pendingVisibleImages",
        "pendingLazyRender",
        "textLength",
    )
    return all(previous.get(key) == current.get(key) for key in keys)


async def _wait_for_dom_stability(
    page: Page,
    timeout_ms: int,
    *,
    allow_interactive_ready_state: bool = False,
) -> None:
    deadline = time.monotonic() + (timeout_ms / 1000)
    stable_cycles = 0
    previous_state: dict[str, int | str] | None = None
    ready_states = {"complete"}
    if allow_interactive_ready_state:
        ready_states.add("interactive")

    while time.monotonic() < deadline:
        current_state = await _collect_stability_state(page)
        is_ready = (
            current_state.get("readyState") in ready_states
            and current_state.get(
                "pendingVisibleImages",
                current_state.get("pendingImages"),
            )
            == 0
            and current_state.get("pendingLazyRender") == 0
        )
        if is_ready and _same_stability_state(previous_state, current_state):
            stable_cycles += 1
            if stable_cycles >= _STABILITY_REQUIRED_CYCLES:
                return
        else:
            stable_cycles = 0
        previous_state = current_state
        await page.wait_for_timeout(_STABILITY_SAMPLE_MS)

    raise PageStabilizationTimeoutError("Timed out waiting for DOM/layout stability")


async def _scroll_pass(page: Page, step: int, pause_ms: int) -> None:
    """Single top-to-bottom scroll pass.

    Starts at y=0 and advances by *step* pixels per iteration, pausing
    *pause_ms* milliseconds at each position to give the browser time to
    decode images and fire IntersectionObserver callbacks.
    """
    await page.evaluate("window.scrollTo(0, 0)")
    y = 0
    while True:
        total_height: int = await page.evaluate("document.body.scrollHeight")
        if y >= total_height:
            break
        await page.evaluate(f"window.scrollTo(0, {y})")
        await page.wait_for_timeout(pause_ms)
        y += step


async def _scroll_to_trigger_lazy(
    page: Page,
    viewport_height: int,
    *,
    has_lazy_content: bool,
) -> None:
    """Scroll the page twice to ensure all lazy-loaded content is captured.

    Pass 1 (slow) — triggers lazy-load initiation.
    Interlude — wait + non-fatal networkidle probe so newly loaded content has
                time to render before the second pass.
    Pass 2 (fast) — content is loaded; ensures everything is in view before the
                final screenshot.

    When *has_lazy_content* is False (no lazy markers detected on the page) the
    much shorter "fast" timing profile is used — the generous pauses only exist
    to trigger lazy loads, and content readiness is still guaranteed afterwards
    by the adaptive _decode_visible_images / _wait_for_dom_stability checks.

    Step size is half the viewport height (min 300 px) so IntersectionObserver
    elements with any rootMargin are not skipped between jumps.
    """
    step = max(300, viewport_height // 2)
    if has_lazy_content:
        pass1_ms = _SCROLL_PAUSE_LAZY_PASS1_MS
        pass2_ms = _SCROLL_PAUSE_LAZY_PASS2_MS
        settle_ms = _SETTLE_PROBE_LAZY_MS
        networkidle_ms = _NETWORKIDLE_PROBE_LAZY_MS
    else:
        pass1_ms = _SCROLL_PAUSE_FAST_PASS1_MS
        pass2_ms = _SCROLL_PAUSE_FAST_PASS2_MS
        settle_ms = _SETTLE_PROBE_FAST_MS
        networkidle_ms = _NETWORKIDLE_PROBE_FAST_MS

    # Pass 1: trigger lazy-load
    await _scroll_pass(page, step, pause_ms=pass1_ms)

    # Wait for triggered content to load
    await page.wait_for_timeout(settle_ms)
    try:
        await page.wait_for_load_state("networkidle", timeout=networkidle_ms)
    except Exception:
        pass

    # Pass 2: content is loaded — ensure everything rendered
    await _scroll_pass(page, step, pause_ms=pass2_ms)

    # Settle at bottom, then scroll back to top
    await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    await page.wait_for_timeout(settle_ms)
    try:
        await page.wait_for_load_state("networkidle", timeout=networkidle_ms)
    except Exception:
        pass
    await page.evaluate("window.scrollTo(0, 0)")
    await page.wait_for_timeout(min(settle_ms, _SETTLE_PROBE_FAST_MS * 2))


async def _stabilize_page(
    page: Page,
    viewport_height: int,
    settle_timeout_ms: int,
    *,
    allow_interactive_ready_state: bool = False,
) -> None:
    await page.evaluate(_JS_HIDE_OVERLAYS)
    await _wait_for_fonts(page, min(settle_timeout_ms, 5000))

    # Detect lazy markers BEFORE triggering, since _JS_TRIGGER_LAZY consumes
    # them (swaps data-src→src, strips lazy classes). Pages with no lazy content
    # use the much faster scroll/settle timing profile.
    has_lazy_content = bool(await page.evaluate(_JS_DETECT_LAZY_CONTENT))
    post_trigger_ms = (
        _POST_TRIGGER_WAIT_LAZY_MS if has_lazy_content else _POST_TRIGGER_WAIT_FAST_MS
    )

    await page.evaluate(_JS_TRIGGER_LAZY)
    await page.evaluate(_JS_FORCE_WP_ROCKET_LAZY_RENDER)
    await page.evaluate(_JS_NORMALIZE_FIXED_BACKGROUNDS)
    await page.wait_for_timeout(post_trigger_ms)

    await _scroll_to_trigger_lazy(
        page,
        viewport_height,
        has_lazy_content=has_lazy_content,
    )

    await page.evaluate(_JS_TRIGGER_LAZY)
    await page.evaluate(_JS_FORCE_WP_ROCKET_LAZY_RENDER)
    await page.evaluate(_JS_NORMALIZE_FIXED_BACKGROUNDS)
    await page.evaluate(_JS_HIDE_OVERLAYS)

    await _wait_for_fonts(page, min(settle_timeout_ms, 5000))
    asset_state = await _decode_visible_images(page, min(settle_timeout_ms, 12000))
    await _wait_for_dom_stability(
        page,
        settle_timeout_ms,
        allow_interactive_ready_state=allow_interactive_ready_state,
    )
    final_state = await _collect_stability_state(page)
    if _is_blank_capture_state(final_state, asset_state):
        raise PageAssetLoadError(
            "Page rendered blank after stabilization: no visible text or "
            "image/background assets"
        )

    await page.evaluate(_JS_HIDE_OVERLAYS)
    await page.wait_for_timeout(500)


# ---------------------------------------------------------------------------
# Core capture
# ---------------------------------------------------------------------------


async def _do_capture(
    browser: Browser,
    url: str,
    viewport: dict[str, int],
    config: EffectiveConfig,
    *,
    retry: bool = False,
) -> CaptureOutcome:
    """Open a fresh browser context, navigate, apply page transforms, and return
    PNG/JPEG bytes and the duration from page.goto → page.screenshot.

    A new context is created for every call so cookies/localStorage never
    leak between screenshots.  The context is always closed in the finally block.

    Duration intentionally excludes:
    - browser.new_context() / context.close() overhead
    - semaphore wait time
    - retry sleep time
    - DB write time
    """
    context = await browser.new_context(
        viewport={"width": viewport["width"], "height": viewport["height"]},
        reduced_motion="reduce",
    )
    await context.add_init_script(_JS_IO_OVERRIDE)
    try:
        page = await context.new_page()
        captured_with_unstable_dom = False
        warning_message: str | None = None
        network_diagnostics = NetworkDiagnostics()
        page.on("requestfailed", network_diagnostics.record_failed_request)
        page.on("response", network_diagnostics.record_response)

        t_start = time.monotonic()  # start: right before navigation
        # Try navigation conditions heaviest → lightest. A goto timeout on a
        # heavier condition (e.g. `load` blocked on a slow third-party
        # subresource) is non-fatal: fall through to a lighter one. Only a
        # timeout on the lightest level means the page is genuinely unreachable.
        nav_chain = _navigation_wait_until_chain(config)
        nav_wait_until = nav_chain[0]
        for index, level in enumerate(nav_chain):
            try:
                await page.goto(url, wait_until=level, timeout=config.timeout)
                nav_wait_until = level
                break
            except PlaywrightTimeoutError:
                if index == len(nav_chain) - 1:
                    raise
                logger.warning(
                    "navigation wait_until=%s timed out for %s, "
                    "falling back to wait_until=%s",
                    level,
                    url,
                    nav_chain[index + 1],
                )

        # Non-fatal networkidle probe — same pattern as _scroll_to_trigger_lazy().
        # Gives JS-heavy pages a chance to settle without blocking on widgets.
        try:
            await page.wait_for_load_state("networkidle", timeout=5000)
        except Exception:
            logger.debug("networkidle probe timed out for %s — continuing", url)

        settle_timeout_ms = _stabilization_timeout_ms(config, retry=retry)
        try:
            await _stabilize_page(
                page,
                viewport["height"],
                settle_timeout_ms,
                allow_interactive_ready_state=nav_wait_until == "domcontentloaded",
            )
        except PageAssetLoadError as exc:
            message = _append_network_diagnostics(str(exc), network_diagnostics)
            logger.warning(
                "asset readiness failed for %s (retry=%s settle_timeout_ms=%d): %s",
                url,
                retry,
                settle_timeout_ms,
                message,
            )
            raise PageAssetLoadError(message) from exc
        except PageStabilizationTimeoutError:
            if not retry:
                logger.warning(
                    "stabilization timed out for %s (retry=%s settle_timeout_ms=%d)",
                    url,
                    retry,
                    settle_timeout_ms,
                )
                raise
            captured_with_unstable_dom = True
            warning_message = (
                "Captured with unstable DOM after stabilization timeout. "
                "Content may be incomplete or still settling."
            )
            logger.warning(
                "capturing %s with unstable DOM after retry stabilization timeout "
                "(settle_timeout_ms=%d)",
                url,
                settle_timeout_ms,
            )
            await page.evaluate(_JS_HIDE_OVERLAYS)
            await page.evaluate(_JS_NORMALIZE_FIXED_BACKGROUNDS)
            await page.wait_for_timeout(300)

        shot_kwargs: dict = {
            "full_page": True,
            "type": config.fmt,
            "timeout": config.timeout,
            "animations": "disabled",
            "caret": "hide",
            "style": _SCREENSHOT_STYLE,
        }
        if config.fmt == "jpeg":
            shot_kwargs["quality"] = config.quality

        await page.evaluate(_JS_NORMALIZE_FIXED_BACKGROUNDS)
        data = await page.screenshot(**shot_kwargs)
        duration = round(time.monotonic() - t_start, 3)  # stop: right after screenshot
        return CaptureOutcome(
            data=data,
            duration=duration,
            captured_with_unstable_dom=captured_with_unstable_dom,
            warning_message=warning_message,
        )
    finally:
        await context.close()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


async def capture_screenshot(
    *,
    browser: Browser,
    session: AsyncSession,
    task_id: uuid.UUID,
    project_id: uuid.UUID,
    url: str,
    viewport: dict[str, int],
    config: EffectiveConfig,
    existing: Screenshot | None = None,
) -> Screenshot:
    """Take a screenshot (with one retry on failure), save to disk, write DB record.

    Concurrency is controlled by the caller. duration_seconds measures
    page.goto → page.screenshot only — context setup/teardown and DB write
    are excluded.

    When *existing* is provided the same DB row is updated in place (re-capture)
    instead of inserting a new record. The file on disk is overwritten at the
    same deterministic path, and ``created_at`` is bumped so clients can
    cache-bust the served image.
    """
    width = viewport["width"]
    height = viewport["height"]
    last_error: Exception | None = None
    duration = 0.0
    data: bytes = b""
    captured_with_unstable_dom = False
    warning_message: str | None = None

    for attempt in range(_RETRY_COUNT + 1):
        if attempt > 0:
            logger.warning(
                "task=%s retry %d/%d url=%s viewport=%dx%d",
                task_id,
                attempt,
                _RETRY_COUNT,
                url,
                width,
                height,
            )
            await asyncio.sleep(_RETRY_DELAY_S)

        try:
            outcome = await _do_capture(
                browser,
                url,
                viewport,
                config,
                retry=attempt > 0,
            )
            data = outcome.data
            duration = outcome.duration
            captured_with_unstable_dom = outcome.captured_with_unstable_dom
            warning_message = outcome.warning_message
            last_error = None
            break
        except PageStabilizationTimeoutError as exc:
            last_error = exc
            logger.warning(
                "task=%s stabilization timeout attempt=%d url=%s viewport=%dx%d",
                task_id,
                attempt + 1,
                url,
                width,
                height,
            )
        except PageAssetLoadError as exc:
            last_error = exc
            logger.warning(
                "task=%s visible asset load failure attempt=%d url=%s "
                "viewport=%dx%d: %s",
                task_id,
                attempt + 1,
                url,
                width,
                height,
                exc,
            )
        except PlaywrightTimeoutError as exc:
            # Timeouts are deterministic — the page will not succeed on retry.
            # Break immediately to avoid wasting another timeout period.
            last_error = exc
            logger.warning(
                "task=%s timeout url=%s viewport=%dx%d — skipping retry",
                task_id,
                url,
                width,
                height,
            )
            break
        except (PlaywrightError, Exception) as exc:
            last_error = exc
            logger.error(
                "task=%s attempt=%d url=%s viewport=%dx%d error=%s: %s",
                task_id,
                attempt + 1,
                url,
                width,
                height,
                type(exc).__name__,
                exc,
            )

    # Build storage path and signed access URL
    relative_path = build_file_path(
        project_id, task_id, url, width, height, config.browser, config.fmt
    )
    # filename is everything after {project_id}/{task_id}/ — used in signed URL
    filename = "/".join(relative_path.split("/")[2:])
    signed_url = generate_signed_url(str(task_id), filename)

    if last_error is None:
        ensure_parent(relative_path)
        abs_file_path(relative_path).write_bytes(data)
        image_dimensions = read_image_dimensions_from_bytes(data)
        status = "ok"
        error_message = warning_message
        logger.info(
            "task=%s saved url=%s viewport=%dx%d browser=%s path=%s "
            "duration=%.3fs unstable_dom=%s",
            task_id,
            url,
            width,
            height,
            config.browser,
            relative_path,
            duration,
            captured_with_unstable_dom,
        )
    else:
        image_dimensions = None
        status = "failed"
        error_message = str(last_error)
        captured_with_unstable_dom = False
        logger.error(
            "task=%s failed url=%s viewport=%dx%d browser=%s duration=%.3fs",
            task_id,
            url,
            width,
            height,
            config.browser,
            duration,
        )

    if existing is not None:
        # Re-capture: update the same row in place (stable id, same file path).
        existing.image_width = image_dimensions[0] if image_dimensions else None
        existing.image_height = image_dimensions[1] if image_dimensions else None
        existing.file_format = config.fmt
        existing.file_path = relative_path
        existing.signed_url = signed_url
        existing.status = status
        existing.captured_with_unstable_dom = captured_with_unstable_dom
        existing.error_message = error_message
        existing.duration_seconds = duration
        existing.created_at = datetime.utcnow()
        record = existing
    else:
        record = Screenshot(
            task_id=task_id,
            url=url,
            browser=config.browser,
            width=width,
            height=height,
            image_width=image_dimensions[0] if image_dimensions else None,
            image_height=image_dimensions[1] if image_dimensions else None,
            file_format=config.fmt,
            file_path=relative_path,
            signed_url=signed_url,
            status=status,
            captured_with_unstable_dom=captured_with_unstable_dom,
            error_message=error_message,
            duration_seconds=duration,
            created_at=datetime.utcnow(),
        )
    session.add(record)
    await session.commit()
    await session.refresh(record)
    return record


# ---------------------------------------------------------------------------
# Single-screenshot re-capture ("redo")
# ---------------------------------------------------------------------------

_RECAPTURABLE_TASK_STATUSES = {"completed", "failed", "timed_out", "cancelled"}


def build_recapture_job_id(screenshot_id: uuid.UUID | str) -> str:
    return f"screenshot-recapture:{screenshot_id}"


async def request_screenshot_recapture(
    session: AsyncSession, screenshot_id: uuid.UUID, user: User
) -> Screenshot:
    """Mark a single screenshot for re-capture and enqueue a worker job.

    Re-capture re-runs the exact same combination (url x viewport x browser) in
    place, overwriting the same file and updating the same DB row. Only allowed
    once the parent task has reached a terminal state.
    """
    from app.core.redis import get_redis_pool
    from app.projects.service import get_project
    from app.tasks.models import Task

    screenshot = await session.get(Screenshot, screenshot_id)
    if not screenshot:
        raise NotFoundError("Screenshot not found")

    task = await session.get(Task, screenshot.task_id)
    if not task:
        raise NotFoundError("Task not found")
    # Verify the task's project exists (projects are shared across all users)
    await get_project(session, task.project_id, user)

    if task.status not in _RECAPTURABLE_TASK_STATUSES:
        raise ConflictError(
            f"Cannot re-capture a screenshot while the task is '{task.status}'. "
            "Wait for the task to finish."
        )

    # Capture before commit — expire_on_commit=True expires the session-bound
    # task object, and reading task.id afterwards would trigger a sync lazy-load
    # (MissingGreenlet) outside the async greenlet context.
    task_id = task.id
    previous_status = screenshot.status
    previous_error = screenshot.error_message

    screenshot.status = "recapturing"
    screenshot.error_message = None
    session.add(screenshot)
    await session.commit()
    await session.refresh(screenshot)

    # Unique job id per request. A deterministic id (screenshot-recapture:{id})
    # is de-duplicated by ARQ against a *completed* job whose result is still
    # cached (default keep_result=1h), so a second redo of the same screenshot
    # would silently never run and leave the row stuck in "recapturing".
    job_id = f"{build_recapture_job_id(screenshot.id)}:{uuid.uuid4().hex}"
    redis = await get_redis_pool()
    job = None
    try:
        job = await redis.enqueue_job(
            "recapture_screenshot",
            str(screenshot.id),
            _job_id=job_id,
        )
    except Exception as exc:
        logger.error(
            "Failed to enqueue recapture for screenshot=%s: %s", screenshot.id, exc
        )

    if job is None:
        # Enqueue failed (or an unexpected job-id collision) — restore the prior
        # state so the row never sticks in "recapturing".
        screenshot.status = previous_status
        screenshot.error_message = previous_error
        session.add(screenshot)
        await session.commit()
        await session.refresh(screenshot)
        raise ConflictError("Failed to enqueue re-capture. Please try again.")

    logger.info("Enqueued recapture for screenshot=%s task=%s", screenshot.id, task_id)
    return screenshot
