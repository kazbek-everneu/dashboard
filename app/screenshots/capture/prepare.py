"""Prepare stage: page transforms (overlays, render normalization, lazy content,
videos, carousels) and the scroll passes that trigger lazy loading.

Every script here carries a ``/*qa:<name>*/`` marker (see jsutil) and runs
through ``jsutil.evaluate`` bounded by ``cfg.PREPARE_SCRIPT_TIMEOUT_MS``. Scripts
that change the DOM end by draining the readiness activity tracker
(``window.__qaActivity.drain()``), so our own edits never count as page
activity.

What it reports: the ``overlays_hidden``, ``inner_scroll_expanded``,
``infinite_scroll_stopped`` and ``carousels_paused`` warnings, the ``overlays``
meta section, the ``page`` meta keys ``inner_scroller`` / ``scroll_steps`` /
``pass2`` / ``pass2_reasons`` and the ``scroll`` phase timing.
"""

import logging
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import Page

from app.screenshots.capture import config as cfg
from app.screenshots.capture import jsutil, network, readiness
from app.screenshots.capture.errors import PageStabilizationTimeoutError
from app.screenshots.capture.types import CaptureRun, PrepareResult

logger = logging.getLogger(__name__)

OVERLAYS_MESSAGE = (
    "Overlays covering the page were hidden (cookie banners, popups, chat widgets)"
)
INNER_SCROLL_MESSAGE = (
    "The page scrolls inside a container; the container was expanded to capture "
    "its full content"
)
INFINITE_SCROLL_MESSAGE = (
    "Stopped scrolling a page that kept growing (infinite scroll); content "
    "further down may be missing"
)
CAROUSELS_MESSAGE = "Carousels were paused on their first slide"

# Warning codes this stage owns; a re-prepared (reloaded) page reports afresh.
_OWN_WARNING_CODES = frozenset(
    {
        "overlays_hidden",
        "inner_scroll_expanded",
        "infinite_scroll_stopped",
        "carousels_paused",
    }
)
_CAROUSEL_LIBRARIES = ("swiper", "slick", "owl", "flickity", "splide", "flexslider")
_OVERLAY_LOG = "prepare.overlays"  # run.scratch key

# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

# Three tiers, each hidden element is recorded (tier, rule, tag#id.class, rect):
#   A  exact vendor selectors (consent managers, chat widgets, popup builders
#      and their backdrops) — always hidden;
#   B  class/id substrings — only when the position says "overlay":
#      cookie|consent|gdpr|cc-*|privacy-banner: the element or an ancestor
#        within ANCESTOR_LEVELS is fixed/sticky (links/buttons: the element
#        itself; never inside a <form>, e.g. a contact form's consent box);
#      popup|modal: a fixed ancestor (or self), visible, and a dialog role,
#        aria-modal, ≥ POPUP_MIN_AREA of the viewport or z ≥ POPUP_MIN_Z_INDEX;
#        legacy: absolute, z ≥ POPUP_MIN_Z_INDEX and ≥ LEGACY_POPUP_MIN_AREA;
#      banner: fixed and its text talks about cookies/consent/privacy/tracking;
#      chat|messenger: fixed;
#   C  geometric backdrops: a visible, click-blocking fixed layer stacked above
#      the page that covers the viewport but is not the page shell. Decorative
#      fixed layers (grain, vignette) use pointer-events: none.
# Plus: open modal <dialog>s are closed and the scroll lock a popup left on
# <html>/<body> is undone. <html>/<body> themselves are never hidden (lock
# classes like body.modal-open match the substrings too).
JS_HIDE_OVERLAYS = r"""() => { /*qa:hide-overlays*/
    const MIN_Z_INDEX = 10;
    const MIN_COVERAGE = 0.9;
    const MAX_SUBTREE_SHARE = 0.5;
    const MIN_GUARDED_SUBTREE = 100;
    const ANCESTOR_LEVELS = 6;
    const POPUP_MIN_AREA = 0.15;
    const LEGACY_POPUP_MIN_AREA = 0.3;
    const POPUP_MIN_Z_INDEX = 1000;
    const MAX_RECORDS = 50;

    const VENDOR_SELECTORS = [
        // Consent managers
        '.cky-consent-container',  '.cky-overlay',
        '#onetrust-banner-sdk',    '#onetrust-consent-sdk',
        '.onetrust-pc-dark-filter',
        '#CybotCookiebotDialog',   '#CybotCookiebotDialogBodyUnderlay',
        '.cmplz-cookiebanner',
        '#cookie-law-info-bar',    '.cookie-law-info-bar',
        '.cli-modal-backdrop',     '#cookielaw-banner',
        '#cookie-notice',          '.cookie-notice',
        '#moove_gdpr_cookie_info_bar',
        '#BorlabsCookieBox',
        '#iubenda-cs-banner',
        '#termly-code-snippet-support',
        '.osano-cm-window',
        '#didomi-host',
        '#qc-cmp2-container',
        '#truste-consent-track',   '.truste_overlay',
        '#usercentrics-root',
        '.klaro',
        '.cc-window',              '.cc-banner',
        '.pea_cook_wrapper',
        // Chat widgets
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
        // Popup builders and modal libraries, with their backdrops
        '.pum-overlay',            '.elementor-popup-modal',
        '.hustle-popup',           '.et_bloom_popup',
        '.mfp-bg',                 '.mfp-wrap',
        '.fancybox-overlay',       '.fancybox-container',
        '.swal2-container',        '.MuiBackdrop-root',
        '.ReactModal__Overlay',
    ];
    const SUBSTRINGS = [
        'cookie', 'consent', 'gdpr', 'privacy-banner',
        'popup', 'modal', 'banner', 'chat', 'messenger',
    ];
    const COOKIE_RE = /cookie|consent|gdpr|privacy-banner/;
    const POPUP_RE = /popup|modal/;
    const BANNER_RE = /banner/;
    const CHAT_RE = /chat|messenger/;
    const BANNER_TEXT_RE = /cookie|consent|privacy|gdpr|tracking|we use/;
    const INTERACTIVE = ['A', 'BUTTON', 'INPUT', 'LABEL', 'SELECT', 'TEXTAREA'];

    const html = document.documentElement;
    const body = document.body;
    const vw = html.clientWidth || window.innerWidth;
    const vh = html.clientHeight || window.innerHeight;
    const viewportArea = Math.max(1, vw * vh);
    const total = body ? body.getElementsByTagName('*').length : 0;
    const records = [];
    let count = 0;

    const styleOf = (el) => {
        try {
            return window.getComputedStyle(el);
        } catch (_) {
            return null;
        }
    };
    const describe = (el) => {
        let text = String(el.tagName || '').toLowerCase();
        if (el.id) {
            text += '#' + el.id;
        }
        const classes = String(el.getAttribute('class') || '').trim();
        if (classes) {
            text += '.' + classes.split(/\s+/).join('.');
        }
        return text.slice(0, 80);
    };
    const rectOf = (r) => ({
        x: Math.round(r.left),
        y: Math.round(r.top),
        width: Math.round(r.width),
        height: Math.round(r.height),
    });
    const shown = (cs, r) => !!cs
        && cs.display !== 'none'
        && cs.visibility !== 'hidden'
        && cs.visibility !== 'collapse'
        && r.width > 0 && r.height > 0;
    const visibleArea = (r) => {
        const w = Math.min(r.right, vw) - Math.max(r.left, 0);
        const h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
        return w > 0 && h > 0 ? w * h : 0;
    };
    const tooBig = (el) => {
        const n = el.getElementsByTagName('*').length;
        return n > MIN_GUARDED_SUBTREE && n > total * MAX_SUBTREE_SHARE;
    };
    // The element itself or an ancestor up to `levels` above it has one of
    // `positions` (never looking at <body>/<html>).
    const positionedWithin = (el, levels, positions) => {
        let node = el;
        for (let i = 0; node && i <= levels; i += 1) {
            if (node === body || node === html) {
                return false;
            }
            const cs = styleOf(node);
            if (cs && positions.indexOf(cs.position) !== -1) {
                return true;
            }
            node = node.parentElement;
        }
        return false;
    };
    const record = (el, tier, rule, r) => {
        el.setAttribute('data-qa-overlay-counted', '1');
        count += 1;
        if (records.length < MAX_RECORDS) {
            records.push({ tier, rule, element: describe(el), rect: rectOf(r) });
        }
    };
    const hide = (el, tier, rule) => {
        if (!el || el === html || el === body) {
            return;
        }
        let rect = null;
        if (!el.hasAttribute('data-qa-overlay-counted')) {
            const r = el.getBoundingClientRect();
            if (shown(styleOf(el), r)) {
                rect = r;
            }
        }
        el.style.setProperty('display', 'none', 'important');
        if (!el.hasAttribute('data-qa-overlay-hidden')) {
            el.setAttribute('data-qa-overlay-hidden', tier + ':' + rule);
        }
        if (rect) {
            record(el, tier, rule, rect);
        }
    };

    // ── A: vendor selectors ──────────────────────────────────────────
    VENDOR_SELECTORS.forEach((sel) => {
        try {
            document.querySelectorAll(sel).forEach((el) => hide(el, 'A', sel));
        } catch (_) {}
    });

    // ── B: substrings, only where the position allows ────────────────
    const ruleFor = (el) => {
        const key = (
            String(el.getAttribute('class') || '') + ' ' + String(el.id || '')
        ).toLowerCase();
        const cs = styleOf(el);
        if (!cs) {
            return null;
        }
        const ccToken = key.split(/\s+/).some((t) => t.indexOf('cc-') === 0);
        if ((COOKIE_RE.test(key) || ccToken)
            && !(el.parentElement && el.parentElement.closest('form'))) {
            const tag = String(el.tagName || '').toUpperCase();
            const levels = INTERACTIVE.indexOf(tag) !== -1 ? 0 : ANCESTOR_LEVELS;
            if (positionedWithin(el, levels, ['fixed', 'sticky']) && !tooBig(el)) {
                return 'cookie';
            }
        }
        if (POPUP_RE.test(key)) {
            const r = el.getBoundingClientRect();
            if (shown(cs, r) && parseFloat(cs.opacity) > 0 && !tooBig(el)) {
                const z = parseInt(cs.zIndex, 10) || 0;
                const area = visibleArea(r) / viewportArea;
                if (positionedWithin(el, Infinity, ['fixed'])) {
                    const role = String(el.getAttribute('role') || '').toLowerCase();
                    if (role === 'dialog' || role === 'alertdialog'
                        || el.getAttribute('aria-modal') === 'true'
                        || area >= POPUP_MIN_AREA || z >= POPUP_MIN_Z_INDEX) {
                        return 'popup';
                    }
                } else if (cs.position === 'absolute' && z >= POPUP_MIN_Z_INDEX
                           && area >= LEGACY_POPUP_MIN_AREA) {
                    return 'popup-legacy';
                }
            }
        }
        if (BANNER_RE.test(key) && cs.position === 'fixed'
            && !el.querySelector('nav, [role="navigation"]')
            && BANNER_TEXT_RE.test(
                String(el.textContent || '').slice(0, 5000).toLowerCase()
            )
            && !tooBig(el)) {
            return 'banner';
        }
        if (CHAT_RE.test(key) && cs.position === 'fixed' && !tooBig(el)) {
            return 'chat';
        }
        return null;
    };
    const substringSelector = (flag) => SUBSTRINGS
        .map((s) => '[class*="' + s + '"' + flag + '],[id*="' + s + '"' + flag + ']')
        .concat([
            '[class^="cc-"' + flag + '],[class*=" cc-"' + flag + '],'
            + '[id^="cc-"' + flag + ']',
        ])
        .join(',');
    let candidates = [];
    try {
        candidates = Array.from(document.querySelectorAll(substringSelector(' i')));
    } catch (_) {
        try {
            candidates = Array.from(document.querySelectorAll(substringSelector('')));
        } catch (_) {}
    }
    candidates.forEach((el) => {
        try {
            if (el === html || el === body) {
                return;
            }
            const hiddenAs = el.getAttribute('data-qa-overlay-hidden');
            if (hiddenAs) {
                // Ours already: keep it hidden if the page showed it again.
                const sep = hiddenAs.indexOf(':');
                hide(el, hiddenAs.slice(0, sep), hiddenAs.slice(sep + 1));
                return;
            }
            const parent = el.parentElement;
            if (parent && parent.closest('[data-qa-overlay-hidden]')) {
                return;
            }
            const rule = ruleFor(el);
            if (rule) {
                hide(el, 'B', rule);
            }
        } catch (_) {}
    });

    // ── Native modal dialogs (closing also removes their ::backdrop) ──
    document.querySelectorAll('dialog').forEach((d) => {
        try {
            if (d.open && d.matches(':modal')) {
                const r = d.getBoundingClientRect();
                d.close();
                if (!d.hasAttribute('data-qa-overlay-counted')) {
                    record(d, 'A', 'dialog', r);
                }
            }
        } catch (_) {}
    });

    // ── C: generic backdrops ─────────────────────────────────────────
    document.querySelectorAll('body *').forEach((el) => {
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
            hide(el, 'C', 'backdrop');
        } catch (_) {}
    });

    // ── Scroll lock left by the popup. overflow-x stays: themes set
    // overflow-x: hidden on purpose to clip off-canvas menus.
    [html, body].forEach((el) => {
        if (!el) return;
        try {
            const cs = window.getComputedStyle(el);
            if (cs.overflowY === 'hidden' || cs.overflowY === 'clip') {
                el.style.setProperty('overflow-y', 'visible', 'important');
            }
            if (el === body && cs.position === 'fixed') {
                el.style.setProperty('position', 'static', 'important');
                el.style.setProperty('top', 'auto', 'important');
            }
        } catch (_) {}
    });
    try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}
    return { count, records };
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
    try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}
}"""


# Cheap one-shot probe: does this page have any deferred/lazy content that needs
# the generous scroll-and-settle profile? Mirrors the selectors handled by
# JS_TRIGGER_LAZY plus WP Rocket lazy-render.
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


# One walk over the whole DOM:
# - background-attachment: fixed → local (a full-page screenshot would repeat
#   or misplace fixed backgrounds);
# - computed content-visibility: auto → inline visible + data-qa-cv-forced
#   (otherwise off-screen sections are skipped by the renderer and come out
#   blank); content-visibility: hidden is the page's intent and stays;
# - WP Rocket lazy render ([data-wpr-lazyrender]) also loses its containment
#   and placeholder size.
JS_NORMALIZE_RENDER = """() => { /*qa:normalize-render*/
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

    let fixedBackgrounds = 0;
    let cvForced = 0;
    const all = document.querySelectorAll('*');
    for (let i = 0; i < all.length; i += 1) {
        const el = all[i];
        try {
            const cs = window.getComputedStyle(el);
            const attachment = cs.backgroundAttachment;
            const nextAttachment = attachment ? normalizeAttachment(attachment) : null;
            if (nextAttachment) {
                el.style.setProperty(
                    'background-attachment',
                    nextAttachment,
                    'important'
                );
                el.setAttribute('data-qa-fixed-bg-normalized', '1');
                fixedBackgrounds += 1;
            }
            if (cs.contentVisibility === 'auto') {
                el.style.setProperty('content-visibility', 'visible', 'important');
                el.setAttribute('data-qa-cv-forced', '1');
                cvForced += 1;
            }
        } catch (_) {}
    }
    document.querySelectorAll('[data-wpr-lazyrender]').forEach((el) => {
        try {
            el.style.setProperty('contain', 'none', 'important');
            el.style.setProperty('contain-intrinsic-size', 'none', 'important');
        } catch (_) {}
    });
    if (cvForced) {
        // Layout code (sliders, masonry) re-measures the sections we revealed.
        try { window.dispatchEvent(new Event('resize')); } catch (_) {}
    }
    const cvForcedTotal = document.querySelectorAll('[data-qa-cv-forced]').length;
    try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}
    return { fixedBackgrounds, cvForced, cvForcedTotal };
}"""


# Init script (added to every capture context, see profiles), not evaluated.
# IntersectionObserver v2 override: pretend an observed element is in view so
# lazy loaders and reveal-on-scroll code fire without scrolling — but at most
# MAX_FAKE_PER_TARGET times per element and MAX_FAKE_TOTAL times per page. After
# that the native observer watches the element for real (the class extends the
# native one, so the instance itself is that observer, built with the page's
# callback and options): an infinite-scroll sentinel that re-observes itself
# can no longer load pages forever, and pages with thousands of observed
# elements fall back to real visibility. ``new IntersectionObserver(...)``,
# ``instanceof`` and the native getters (root, rootMargin, thresholds) keep
# working. Counters: ``window.__qaIO = {fake, native, observes}``.
JS_IO_OVERRIDE = """
(function() { /*qa:io-override*/
    const NativeIO = window.IntersectionObserver;
    if (typeof NativeIO !== 'function' || NativeIO.__qaOverride) {
        return;
    }
    const MAX_FAKE_PER_TARGET = 2;
    const MAX_FAKE_TOTAL = 300;
    const stats = { fake: 0, native: 0, observes: 0 };
    const fakeDeliveries = new WeakMap();  // target -> fake deliveries so far
    const observers = new WeakMap();  // observer -> { callback, pending }

    const fakeEntry = (target) => {
        const rect = target.getBoundingClientRect();
        return {
            isIntersecting: true,
            isVisible: true,
            intersectionRatio: 1.0,
            intersectionRect: rect,
            boundingClientRect: rect,
            rootBounds: null,
            target: target,
            time: performance.now(),
        };
    };

    class IntersectionObserver extends NativeIO {
        constructor(callback, options) {
            super(callback, options);
            observers.set(this, { callback: callback, pending: new Set() });
        }

        observe(target) {
            stats.observes += 1;
            const own = observers.get(this);
            const isElement = !!target && target.nodeType === 1;
            const used = isElement ? (fakeDeliveries.get(target) || 0) : 0;
            if (own && isElement && used < MAX_FAKE_PER_TARGET
                && stats.fake < MAX_FAKE_TOTAL) {
                fakeDeliveries.set(target, used + 1);
                stats.fake += 1;
                own.pending.add(target);
                setTimeout(() => {
                    if (own.pending.delete(target)) {
                        own.callback.call(this, [fakeEntry(target)], this);
                    }
                }, 0);
                return;
            }
            stats.native += 1;
            return super.observe(target);
        }

        unobserve(target) {
            const own = observers.get(this);
            if (own) {
                own.pending.delete(target);
            }
            return super.unobserve(target);
        }

        disconnect() {
            const own = observers.get(this);
            if (own) {
                own.pending.clear();
            }
            return super.disconnect();
        }

        takeRecords() {
            return super.takeRecords();
        }
    }

    Object.defineProperty(IntersectionObserver, '__qaOverride', { value: true });
    Object.defineProperty(window, '__qaIO', {
        value: stats,
        configurable: true,
        writable: true,
        enumerable: false,
    });
    window.IntersectionObserver = IntersectionObserver;
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


# Before scrolling: instant scrolling (a smooth-scroll page would otherwise
# still be gliding when we measure), and the inner scroll container of an app
# shell whose document itself does not scroll — the largest element with
# overflow-y auto|scroll that has real overflow and covers most of the
# viewport is marked data-qa-scroller and scrolled instead of the window.
JS_SCROLL_SETUP = r"""() => { /*qa:scroll-setup*/
    const MAX_DOCUMENT_SCROLL = 2;
    const MIN_OVERFLOW = 100;
    const MIN_WIDTH_SHARE = 0.6;
    const MIN_HEIGHT_SHARE = 0.5;

    const html = document.documentElement;
    const body = document.body;
    [html, body].forEach((el) => {
        if (el) {
            try {
                el.style.setProperty('scroll-behavior', 'auto', 'important');
            } catch (_) {}
        }
    });
    document.querySelectorAll('[data-qa-scroller]').forEach((el) => {
        el.removeAttribute('data-qa-scroller');
    });
    const describe = (el) => {
        let text = String(el.tagName || '').toLowerCase();
        if (el.id) {
            text += '#' + el.id;
        }
        const classes = String(el.getAttribute('class') || '').trim();
        if (classes) {
            text += '.' + classes.split(/\s+/).join('.');
        }
        return text.slice(0, 80);
    };

    const se = document.scrollingElement || html;
    const vw = window.innerWidth;
    const vh = window.innerHeight;
    const documentScroll = Math.max(0, se.scrollHeight - vh);
    const result = { inner: false, documentScroll };
    if (documentScroll <= MAX_DOCUMENT_SCROLL && body) {
        let best = null;
        let bestArea = 0;
        const nodes = [body].concat(Array.from(body.querySelectorAll('*')));
        nodes.forEach((el) => {
            try {
                const cs = window.getComputedStyle(el);
                if (cs.overflowY !== 'auto' && cs.overflowY !== 'scroll') return;
                if (!(el.scrollHeight > el.clientHeight + MIN_OVERFLOW)) return;
                const r = el.getBoundingClientRect();
                const w = Math.min(r.right, vw) - Math.max(r.left, 0);
                const h = Math.min(r.bottom, vh) - Math.max(r.top, 0);
                if (w < MIN_WIDTH_SHARE * vw || h < MIN_HEIGHT_SHARE * vh) return;
                if (w * h > bestArea) {
                    best = el;
                    bestArea = w * h;
                }
            } catch (_) {}
        });
        if (best) {
            best.setAttribute('data-qa-scroller', '1');
            best.style.setProperty('scroll-behavior', 'auto', 'important');
            result.inner = true;
            result.element = describe(best);
            result.scrollHeight = best.scrollHeight;
            result.clientHeight = best.clientHeight;
        }
    }
    try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}
    return result;
}"""


# maxScroll of the window (Playwright's full-page height formula minus the
# viewport) or of the marked inner scroller, plus the IntersectionObserver
# observe() count of the IO override.
JS_SCROLL_METRICS = """(opts) => { /*qa:scroll-metrics*/
    const io = window.__qaIO;
    const ioObserves = io && typeof io.observes === 'number' ? io.observes : 0;
    const scroller = opts && opts.inner
        ? document.querySelector('[data-qa-scroller]')
        : null;
    if (scroller) {
        return {
            maxScroll: Math.max(0, scroller.scrollHeight - scroller.clientHeight),
            height: scroller.scrollHeight,
            viewport: scroller.clientHeight,
            ioObserves,
        };
    }
    const html = document.documentElement;
    const body = document.body;
    const se = document.scrollingElement || html;
    const height = Math.max(
        se ? se.scrollHeight : 0,
        body ? body.scrollHeight : 0,
        html ? html.scrollHeight : 0
    );
    return {
        maxScroll: Math.max(0, height - window.innerHeight),
        height,
        viewport: window.innerHeight,
        ioObserves,
    };
}"""


JS_SCROLL_TO = """(opts) => { /*qa:scroll-to*/
    const y = Math.max(0, Number(opts && opts.y) || 0);
    const scroller = opts && opts.inner
        ? document.querySelector('[data-qa-scroller]')
        : null;
    try {
        (scroller || window).scrollTo({ top: y, left: 0, behavior: 'instant' });
    } catch (_) {
        if (scroller) {
            scroller.scrollTop = y;
        } else {
            window.scrollTo(0, y);
        }
    }
}"""


# Read after pass 1 + settle, for should_run_pass2.
JS_PASS2_SIGNALS = """() => { /*qa:pass2-signals*/
    const visible = (el) => {
        try {
            const rect = el.getBoundingClientRect();
            if (rect.width <= 1 || rect.height <= 1) return false;
            const style = window.getComputedStyle(el);
            return (
                style.display !== 'none' &&
                style.visibility !== 'hidden' &&
                style.visibility !== 'collapse' &&
                Number(style.opacity || '1') > 0
            );
        } catch (_) {
            return false;
        }
    };
    let pendingVisibleImages = 0;
    Array.from(document.images).forEach((img) => {
        const src = img.currentSrc || img.src;
        if (src && !img.complete && visible(img)) {
            pendingVisibleImages += 1;
        }
    });
    return {
        pendingVisibleImages,
        cvForced: document.querySelectorAll('[data-qa-cv-forced]').length,
    };
}"""


# End of prepare (and again before the readiness verdict and the screenshot):
# the marked inner scroller, its ancestors and <html>/<body> lose their height
# limits and scrolling, so the document is as tall as the content and the
# full-page screenshot shows all of it. A horizontal clip stays a clip
# (overflow-x: clip keeps off-canvas menus hidden without creating a scroll
# container); a fixed app shell becomes relative so it can grow the document.
JS_EXPAND_SCROLLER = r"""() => { /*qa:expand-scroller*/
    const scroller = document.querySelector('[data-qa-scroller]');
    if (!scroller) {
        return null;
    }
    const html = document.documentElement;
    const body = document.body;
    const describe = (el) => {
        let text = String(el.tagName || '').toLowerCase();
        if (el.id) {
            text += '#' + el.id;
        }
        const classes = String(el.getAttribute('class') || '').trim();
        if (classes) {
            text += '.' + classes.split(/\s+/).join('.');
        }
        return text.slice(0, 80);
    };
    const release = (el, chain) => {
        try {
            const cs = window.getComputedStyle(el);
            el.style.setProperty('height', 'auto', 'important');
            if (cs.overflowX === 'hidden' || cs.overflowX === 'clip') {
                el.style.setProperty('overflow-x', 'clip', 'important');
                el.style.setProperty('overflow-y', 'visible', 'important');
            } else {
                el.style.setProperty('overflow', 'visible', 'important');
            }
            if (chain) {
                el.style.setProperty('max-height', 'none', 'important');
                if (cs.position === 'fixed') {
                    el.style.setProperty('position', 'relative', 'important');
                }
            }
        } catch (_) {}
    };
    [html, body].forEach((el) => {
        if (el) release(el, false);
    });
    for (let el = scroller; el && el !== body && el !== html; el = el.parentElement) {
        release(el, true);
    }
    scroller.setAttribute('data-qa-scroller-expanded', '1');
    try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}
    const se = document.scrollingElement || html;
    return {
        element: describe(scroller),
        scrollHeight: Math.max(
            se ? se.scrollHeight : 0,
            body ? body.scrollHeight : 0,
            html.scrollHeight
        ),
    };
}"""


# Stops autoplay and shows the first slide of every carousel instance found,
# each library in its own try/catch. bxSlider keeps no instance on its element
# and is not supported.
JS_PAUSE_CAROUSELS = """() => { /*qa:pause-carousels*/
    const counts = {
        swiper: 0, slick: 0, owl: 0, flickity: 0, splide: 0, flexslider: 0,
    };
    const jq = typeof window.jQuery === 'function' ? window.jQuery : null;

    // Swiper (Elementor's carousels too): the instance lives on the container.
    try {
        const seen = new Set();
        const containers = '[class*="swiper"], swiper-container';
        document.querySelectorAll(containers).forEach((el) => {
            try {
                let swiper = el.swiper;
                if (!swiper && jq) {
                    swiper = jq(el).data('swiper');
                }
                if (!swiper || typeof swiper !== 'object' || seen.has(swiper)) return;
                seen.add(swiper);
                if (swiper.autoplay && typeof swiper.autoplay.stop === 'function') {
                    swiper.autoplay.stop();
                }
                const loop = !!(swiper.params && swiper.params.loop);
                if (loop && typeof swiper.slideToLoop === 'function') {
                    swiper.slideToLoop(0, 0);
                } else if (typeof swiper.slideTo === 'function') {
                    swiper.slideTo(0, 0);
                }
                counts.swiper += 1;
            } catch (_) {}
        });
    } catch (_) {}

    // Slick
    try {
        if (jq && jq.fn && jq.fn.slick) {
            document.querySelectorAll('.slick-initialized').forEach((el) => {
                try {
                    jq(el).slick('slickPause');
                    jq(el).slick('slickGoTo', 0, true);
                    counts.slick += 1;
                } catch (_) {}
            });
        }
    } catch (_) {}

    // Owl Carousel 2
    try {
        if (jq) {
            document.querySelectorAll('.owl-carousel').forEach((el) => {
                try {
                    const $el = jq(el);
                    if (!$el.data('owl.carousel')) return;
                    $el.trigger('stop.owl.autoplay');
                    $el.trigger('to.owl.carousel', [0, 0]);
                    counts.owl += 1;
                } catch (_) {}
            });
        }
    } catch (_) {}

    // Flickity
    try {
        document.querySelectorAll('.flickity-enabled').forEach((el) => {
            try {
                let flkty = null;
                if (window.Flickity && typeof window.Flickity.data === 'function') {
                    flkty = window.Flickity.data(el);
                }
                if (!flkty && jq) {
                    flkty = jq(el).data('flickity');
                }
                if (!flkty) return;
                if (typeof flkty.stopPlayer === 'function') flkty.stopPlayer();
                if (typeof flkty.select === 'function') flkty.select(0, false, true);
                counts.flickity += 1;
            } catch (_) {}
        });
    } catch (_) {}

    // Splide
    try {
        document.querySelectorAll('.splide').forEach((el) => {
            try {
                const splide = el.splide;
                if (!splide) return;
                const autoplay = splide.Components && splide.Components.Autoplay;
                if (autoplay && typeof autoplay.pause === 'function') autoplay.pause();
                if (typeof splide.go === 'function') splide.go(0);
                counts.splide += 1;
            } catch (_) {}
        });
    } catch (_) {}

    // FlexSlider
    try {
        if (jq) {
            document.querySelectorAll('.flexslider').forEach((el) => {
                try {
                    const slider = jq(el).data('flexslider');
                    if (!slider || typeof slider.pause !== 'function') return;
                    slider.pause();
                    counts.flexslider += 1;
                } catch (_) {}
            });
        }
    } catch (_) {}

    try{window.__qaActivity&&window.__qaActivity.drain()}catch(_){}
    return counts;
}"""


# Two animation frames: the page has rendered what we just changed. The
# timeout covers pages that get no frames (hidden/background renderers).
JS_NEXT_FRAME = """() => { /*qa:next-frame*/
    return new Promise((resolve) => {
        let done = false;
        const finish = () => {
            if (!done) {
                done = true;
                resolve(true);
            }
        };
        setTimeout(finish, 200);
        try {
            requestAnimationFrame(() => requestAnimationFrame(finish));
        } catch (_) {
            finish();
        }
    });
}"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


async def _script(page: Page, script: str, arg: Any = None) -> Any:
    return await jsutil.evaluate(
        page, script, arg, timeout_ms=cfg.PREPARE_SCRIPT_TIMEOUT_MS
    )


def _as_dict(raw: object) -> dict[str, Any]:
    return raw if isinstance(raw, dict) else {}


def _as_int(value: object) -> int:
    try:
        return int(value or 0)  # type: ignore[call-overload]
    except (TypeError, ValueError, OverflowError):
        return 0


# ---------------------------------------------------------------------------
# Scroll decisions (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ScrollMetrics:
    """What JS_SCROLL_METRICS measured (zeros when the page answered nothing)."""

    max_scroll: int = 0
    height: int = 0
    viewport: int = 0
    io_observes: int = 0

    @classmethod
    def from_raw(cls, raw: object) -> "ScrollMetrics":
        data = _as_dict(raw)
        return cls(
            max_scroll=max(0, _as_int(data.get("maxScroll"))),
            height=max(0, _as_int(data.get("height"))),
            viewport=max(0, _as_int(data.get("viewport"))),
            io_observes=max(0, _as_int(data.get("ioObserves"))),
        )


@dataclass(frozen=True)
class Pass2Signals:
    """What pass 1 and the settle after it saw; see should_run_pass2."""

    lazy_markers: bool = False  # JS_DETECT_LAZY_CONTENT found lazy markers
    height_growth: int = 0  # px the scrolled height grew
    requests_started: int = 0  # tracked requests started (InflightTracker)
    io_observes_growth: int = 0  # new IntersectionObserver.observe() calls
    pending_visible_images: int = 0  # visible <img> still loading
    cv_forced: int = 0  # elements whose content-visibility was forced


def pass2_reasons(signals: Pass2Signals) -> tuple[str, ...]:
    """The signals that ask for a second scroll pass, by name."""
    reasons: list[str] = []
    if signals.lazy_markers:
        reasons.append("lazy_markers")
    if signals.height_growth >= cfg.PASS2_MIN_HEIGHT_GROWTH_PX:
        reasons.append("height_growth")
    if signals.requests_started > 0:
        reasons.append("requests")
    if signals.io_observes_growth > 0:
        reasons.append("io_observes")
    if signals.pending_visible_images > 0:
        reasons.append("pending_images")
    if signals.cv_forced > 0:
        reasons.append("content_visibility")
    return tuple(reasons)


def should_run_pass2(signals: Pass2Signals) -> bool:
    """Whether pass 1 left anything a second pass could still trigger: lazy
    markers, a page that grew, requests or IntersectionObserver targets that
    the scrolling started, visible images still loading, or sections whose
    content-visibility was forced. (cfg.FORCE_PASS2 overrides this in
    scroll_to_trigger_lazy.)"""
    return bool(pass2_reasons(signals))


class GrowthGuard:
    """Infinite-scroll guard over both scroll passes.

    A bottom growth is the page growing by at least
    INFINITE_SCROLL_GROWTH_RATIO * viewport height while we waited at its
    bottom. INFINITE_SCROLL_GROWTHS of them in a row, or a page taller than
    INFINITE_SCROLL_MAX_HEIGHT_PX, stop the scrolling for good.
    """

    def __init__(self, viewport_height: int) -> None:
        self.threshold = cfg.INFINITE_SCROLL_GROWTH_RATIO * max(viewport_height, 1)
        self.consecutive = 0
        self.events = 0
        self.stopped = False

    def check_height(self, height: int) -> bool:
        if height > cfg.INFINITE_SCROLL_MAX_HEIGHT_PX:
            self.stopped = True
        return self.stopped

    def at_bottom(self, growth: int, height: int) -> bool:
        """Record what waiting at the bottom did; True once scrolling must
        stop."""
        if growth >= self.threshold:
            self.consecutive += 1
            self.events += 1
            if self.consecutive >= cfg.INFINITE_SCROLL_GROWTHS:
                self.stopped = True
        else:
            self.consecutive = 0
        return self.check_height(height)


# ---------------------------------------------------------------------------
# Scrolling
# ---------------------------------------------------------------------------


async def measure_scroll(page: Page, *, inner: bool = False) -> ScrollMetrics:
    return ScrollMetrics.from_raw(
        await _script(page, JS_SCROLL_METRICS, {"inner": inner})
    )


async def scroll_to(page: Page, y: int, *, inner: bool = False) -> None:
    await _script(page, JS_SCROLL_TO, {"y": y, "inner": inner})


async def scroll_pass(
    run: CaptureRun,
    step: int,
    *,
    inner: bool,
    guard: GrowthGuard,
    min_ms: float,
    quiet_ms: float,
    max_ms: float,
    metrics: ScrollMetrics | None = None,
) -> tuple[int, ScrollMetrics]:
    """One top-to-bottom pass; returns (positions visited, last metrics).

    Positions are 0, step, 2·step, … clamped to the max scroll, which is
    re-measured after every position, so the last position is exactly the
    bottom and the pass ends there — unless waiting at the bottom grew the
    page, then it scrolls on (bounded by *guard*). At each position it waits
    (adaptively, see network.wait_for_quiet) for the requests the scroll
    triggered, capped at *max_ms*. *metrics* are the current metrics when the
    caller just measured them.
    """
    page = run.page
    if metrics is None:
        metrics = await measure_scroll(page, inner=inner)
    positions: list[int] = []
    y = 0
    while len(positions) < cfg.MAX_SCROLL_STEPS:
        if guard.check_height(metrics.height):
            break
        target = min(y, metrics.max_scroll)
        if positions and target <= positions[-1]:
            break  # at the bottom, and waiting there did not grow the page
        await scroll_to(page, target, inner=inner)
        await network.wait_for_quiet(
            page, run.tracker, min_ms=min_ms, quiet_ms=quiet_ms, max_ms=max_ms
        )
        positions.append(target)
        before = metrics
        metrics = await measure_scroll(page, inner=inner)
        if target >= before.max_scroll and guard.at_bottom(
            metrics.height - before.height, metrics.height
        ):
            break
        y = target + step
    else:
        logger.warning("scroll pass stopped after %d steps", cfg.MAX_SCROLL_STEPS)
    return len(positions), metrics


@dataclass
class ScrollOutcome:
    steps: int = 0  # positions visited over both passes
    pass2_ran: bool = False
    # The signals that asked for pass 2 (pass2_reasons), led by "forced" when
    # cfg.FORCE_PASS2 ran it: the rest then says whether it would have run.
    pass2_reasons: tuple[str, ...] = ()
    growth_stopped: bool = False
    growth_events: int = 0
    height: int = 0  # last measured scroll height (window or inner scroller)


async def scroll_to_trigger_lazy(
    run: CaptureRun, *, has_lazy_content: bool, inner: bool = False
) -> ScrollOutcome:
    """Scroll the page (or its marked inner scroller) to trigger lazy loading.

    Pass 1 steps by half the viewport height (min SCROLL_STEP_MIN_PX, so
    IntersectionObserver elements with any rootMargin are not skipped) and
    ends at the bottom, where the settle lets the triggered content load.
    Pass 2 (step PASS2_STEP_RATIO * viewport height, then another settle) runs
    only when should_run_pass2 sees a reason, or cfg.FORCE_PASS2. Finally back
    to the top. The infinite-scroll guard can end the scrolling early.

    When *has_lazy_content* is False (no lazy markers detected on the page) the
    shorter "fast" caps are used. Either way every pause is adaptive: it ends
    once the page's tracked requests have gone quiet, and content readiness is
    still guaranteed afterwards by the readiness stage.
    """
    page = run.page
    tracker = run.tracker
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

    start = await measure_scroll(page, inner=inner)
    viewport_height = start.viewport or run.viewport["height"]
    guard = GrowthGuard(viewport_height)
    requests_before = tracker.started_count if tracker is not None else 0
    outcome = ScrollOutcome()

    # Pass 1: trigger lazy loading, ending at the bottom.
    outcome.steps, last = await scroll_pass(
        run,
        max(cfg.SCROLL_STEP_MIN_PX, viewport_height // 2),
        inner=inner,
        guard=guard,
        metrics=start,
        min_ms=pass1_min_ms,
        quiet_ms=cfg.STEP_QUIET_MS,
        max_ms=pass1_ms,
    )
    outcome.height = last.height

    if not guard.stopped:
        # Let what pass 1 triggered load, then decide whether pass 2 can
        # still change anything.
        await settle()
        after = await measure_scroll(page, inner=inner)
        extra = _as_dict(await _script(page, JS_PASS2_SIGNALS))
        signals = Pass2Signals(
            lazy_markers=has_lazy_content,
            height_growth=after.height - start.height,
            requests_started=(
                tracker.started_count - requests_before if tracker is not None else 0
            ),
            io_observes_growth=after.io_observes - start.io_observes,
            pending_visible_images=_as_int(extra.get("pendingVisibleImages")),
            cv_forced=_as_int(extra.get("cvForced")),
        )
        outcome.height = after.height
        forced = bool(cfg.FORCE_PASS2)
        reasons = pass2_reasons(signals)
        outcome.pass2_reasons = ("forced", *reasons) if forced else reasons
        logger.debug(
            "scroll pass 2 for %s: %s",
            run.url,
            ", ".join(outcome.pass2_reasons) or "skipped (no signals)",
        )
        if forced or reasons:
            steps, last = await scroll_pass(
                run,
                max(
                    cfg.SCROLL_STEP_MIN_PX, int(viewport_height * cfg.PASS2_STEP_RATIO)
                ),
                inner=inner,
                guard=guard,
                metrics=after,
                min_ms=cfg.STEP_MIN_PASS2_MS,
                quiet_ms=cfg.STEP_QUIET_PASS2_MS,
                max_ms=pass2_ms,
            )
            outcome.steps += steps
            outcome.pass2_ran = True
            outcome.height = last.height
            if not guard.stopped:
                await settle()

    outcome.growth_stopped = guard.stopped
    outcome.growth_events = guard.events
    await scroll_to(page, 0, inner=inner)
    await network.wait_for_quiet(
        page,
        tracker,
        min_ms=cfg.SETTLE_MIN_MS,
        quiet_ms=cfg.STEP_QUIET_MS,
        max_ms=min(settle_ms, cfg.SETTLE_PROBE_FAST_MS * 2),
    )
    return outcome


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------


@dataclass
class _OverlayLog:
    """Overlays hidden on the current page (run.scratch[_OVERLAY_LOG])."""

    hidden: int = 0
    records: list[dict[str, Any]] = field(default_factory=list)


def _overlay_log(run: CaptureRun) -> _OverlayLog:
    log = run.scratch.get(_OVERLAY_LOG)
    if not isinstance(log, _OverlayLog):
        log = _OverlayLog()
        run.scratch[_OVERLAY_LOG] = log
    return log


def _overlay_record(raw: dict[str, Any]) -> dict[str, Any]:
    """``{tier, rule, element, rect: {x, y, width, height}}`` — the shape of
    both the warning samples and the meta samples."""
    rect = _as_dict(raw.get("rect"))
    return {
        "tier": str(raw.get("tier") or ""),
        "rule": str(raw.get("rule") or "")[:80],
        "element": str(raw.get("element") or "")[:80],
        "rect": {key: _as_int(rect.get(key)) for key in ("x", "y", "width", "height")},
    }


async def hide_overlays(run: CaptureRun) -> int:
    """Hide cookie banners, popups, chat widgets and backdrops; returns how
    many visible ones this call hid. Each one goes into the ``overlays`` meta
    section (``hidden`` and the first OVERLAY_SAMPLES records) and counts once
    in the ``overlays_hidden`` warning (``count`` = elements, ``samples`` =
    the same records: ``{tier, rule, element, rect: {x, y, width, height}}``)."""
    raw = _as_dict(await _script(run.page, JS_HIDE_OVERLAYS))
    raw_records = raw.get("records")
    records = [
        _overlay_record(item)
        for item in (raw_records if isinstance(raw_records, list) else [])
        if isinstance(item, dict)
    ]
    hidden = max(_as_int(raw.get("count")), len(records))
    if not hidden:
        return 0
    log = _overlay_log(run)
    log.hidden += hidden
    for index in range(hidden):
        sample: dict[str, Any] | None = None
        if index < len(records):
            if len(log.records) < cfg.OVERLAY_SAMPLES:
                log.records.append(records[index])
            sample = dict(records[index])
        run.report.warn("overlays_hidden", OVERLAYS_MESSAGE, samples=sample)
    run.report.set_meta(
        "overlays",
        hidden=log.hidden,
        samples=[{**r, "rect": dict(r["rect"])} for r in log.records],
    )
    if run.prep is not None:
        run.prep.overlays_hidden = log.hidden
    return hidden


async def normalize_render(run: CaptureRun) -> dict[str, Any]:
    """Fixed backgrounds → local, content-visibility: auto → visible (see
    JS_NORMALIZE_RENDER); returns the page's counts."""
    return _as_dict(await _script(run.page, JS_NORMALIZE_RENDER))


async def pause_carousels(run: CaptureRun) -> int:
    """Stop autoplay and go to the first slide; returns the carousels found.
    The first call that finds some records ``carousels_paused`` (flat details:
    ``total`` plus one count per library found, e.g. ``swiper``)."""
    counts = _as_dict(await _script(run.page, JS_PAUSE_CAROUSELS))
    libraries = {
        name: _as_int(counts.get(name))
        for name in _CAROUSEL_LIBRARIES
        if _as_int(counts.get(name)) > 0
    }
    total = sum(libraries.values())
    if total and run.report.warning("carousels_paused") is None:
        run.report.warn("carousels_paused", CAROUSELS_MESSAGE, total=total, **libraries)
    if run.prep is not None and total > run.prep.carousels_paused:
        run.prep.carousels_paused = total
    return total


async def expand_inner_scroller(run: CaptureRun) -> dict[str, Any] | None:
    """Expand the marked inner scroller (JS_EXPAND_SCROLLER); None when the
    page has none."""
    raw = await _script(run.page, JS_EXPAND_SCROLLER)
    return raw if isinstance(raw, dict) else None


async def _reapply_expansion(run: CaptureRun) -> None:
    # The page may have re-rendered the shell (React resets inline styles).
    if run.prep is not None and run.prep.inner_scroller:
        await expand_inner_scroller(run)


async def next_frame(page: Page) -> None:
    """Wait for two animation frames (200 ms at most)."""
    await _script(page, JS_NEXT_FRAME)


def _start_report(run: CaptureRun) -> None:
    """Fresh prepare state for a newly loaded page (a reload re-prepares on
    the same run: report that page only)."""
    report = run.report
    if _OVERLAY_LOG in run.scratch:
        report.warnings[:] = [
            warning
            for warning in report.warnings
            if warning.code not in _OWN_WARNING_CODES
        ]
    run.scratch[_OVERLAY_LOG] = _OverlayLog()
    report.set_meta("overlays", hidden=0, samples=[])


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


async def prepare_page(run: CaptureRun) -> PrepareResult:
    """Hide overlays, trigger lazy content and scroll the page through (no
    readiness verdict). Never raises PageStabilizationTimeoutError, so a
    readiness retry can always run on an already scrolled page."""
    page = run.page
    report = run.report
    _start_report(run)
    settle_timeout_ms = cfg.stabilization_timeout_ms(run.config, retry=run.attempt > 0)
    await hide_overlays(run)
    await _script(page, JS_PAUSE_VIDEOS)
    try:
        await readiness.wait_for_fonts(
            page, min(settle_timeout_ms, cfg.PREPARE_FONTS_WAIT_CAP_MS)
        )
    except PageStabilizationTimeoutError:
        # Fonts are awaited again by readiness.finalize, which owns the verdict.
        logger.debug("fonts not ready before scrolling — continuing")

    # Detect lazy markers BEFORE triggering, since JS_TRIGGER_LAZY consumes
    # them (swaps data-src→src, strips lazy classes). Pages with no lazy content
    # use the much faster scroll/settle timing profile.
    has_lazy_content = bool(await _script(page, JS_DETECT_LAZY_CONTENT))
    post_trigger_ms = (
        cfg.POST_TRIGGER_WAIT_LAZY_MS
        if has_lazy_content
        else cfg.POST_TRIGGER_WAIT_FAST_MS
    )
    await _script(page, JS_TRIGGER_LAZY)
    await normalize_render(run)
    await network.wait_for_quiet(
        page,
        run.tracker,
        min_ms=cfg.POST_TRIGGER_MIN_MS,
        quiet_ms=cfg.POST_TRIGGER_QUIET_MS,
        max_ms=post_trigger_ms,
    )

    setup = _as_dict(await _script(page, JS_SCROLL_SETUP))
    inner = bool(setup.get("inner"))
    with report.phase("scroll"):
        scroll = await scroll_to_trigger_lazy(
            run, has_lazy_content=has_lazy_content, inner=inner
        )
    if scroll.growth_stopped:
        report.warn(
            "infinite_scroll_stopped",
            INFINITE_SCROLL_MESSAGE,
            height=scroll.height,
            growth_events=scroll.growth_events,
        )
    carousels = await pause_carousels(run)

    document_height = scroll.height
    inner_scroller = False
    if inner:
        expanded = await expand_inner_scroller(run)
        if expanded is not None:
            inner_scroller = True
            document_height = _as_int(expanded.get("scrollHeight")) or document_height
            report.warn(
                "inner_scroll_expanded",
                INNER_SCROLL_MESSAGE,
                scroll_height=document_height,
                element=str(expanded.get("element") or setup.get("element") or ""),
            )
    await _script(page, JS_PAUSE_VIDEOS)

    report.set_meta(
        "page",
        inner_scroller=inner_scroller,
        scroll_steps=scroll.steps,
        pass2=scroll.pass2_ran,
        pass2_reasons=list(scroll.pass2_reasons),
    )
    return PrepareResult(
        has_lazy_content=has_lazy_content,
        scroll_steps=scroll.steps,
        pass2_ran=scroll.pass2_ran,
        document_height=document_height,
        inner_scroller=inner_scroller,
        growth_stopped=scroll.growth_stopped,
        overlays_hidden=_overlay_log(run).hidden,
        carousels_paused=carousels,
        pass2_reasons=scroll.pass2_reasons,
    )


async def reapply_transforms(run: CaptureRun) -> None:
    """Re-trigger lazy content and re-apply the page transforms right before
    a readiness verdict (content added since prepare gets them too)."""
    page = run.page
    await _script(page, JS_TRIGGER_LAZY)
    await normalize_render(run)
    await hide_overlays(run)
    await _reapply_expansion(run)
    await pause_carousels(run)


async def prepare_unstable(run: CaptureRun) -> None:
    """Best-effort cleanup before capturing a page whose DOM never settled."""
    await hide_overlays(run)
    await _reapply_expansion(run)
    await normalize_render(run)
    await next_frame(run.page)


async def final_touches(run: CaptureRun, *, unstable: bool) -> None:
    """Last transforms right before the screenshot: hide overlays again (popups
    on a timer), keep an inner scroller expanded and let the page render a
    frame (shorter cleanup for an *unstable* page), then pause videos and
    normalize the render once more."""
    page = run.page
    if unstable:
        await prepare_unstable(run)
    else:
        await hide_overlays(run)
        await _reapply_expansion(run)
        await next_frame(page)
    await _script(page, JS_PAUSE_VIDEOS)
    await normalize_render(run)
