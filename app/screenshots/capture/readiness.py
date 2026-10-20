"""Readiness stage: fonts, visible image/background decode, DOM stability and
the blank-page check, folded into one ReadinessResult verdict — plus the
asset classification and the in-place retry of broken assets.

Every script here carries a ``/*qa:<name>*/`` marker (see jsutil).
"""

import logging
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from playwright.async_api import Page

from app.screenshots.capture import clock, jsutil, network
from app.screenshots.capture import config as cfg
from app.screenshots.capture.errors import (
    EvaluateTimeout,
    PageAssetLoadError,
    PageStabilizationTimeoutError,
)
from app.screenshots.capture.network import InflightTracker, NetworkDiagnostics
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import (
    AssetCategory,
    AssetIssue,
    AssetKind,
    AssetReadinessResult,
    CaptureRun,
    ReadinessResult,
    ResourceRecord,
)

logger = logging.getLogger(__name__)

FONTS_TIMEOUT_MESSAGE = "Timed out waiting for fonts to be ready"
DECODE_TIMEOUT_MESSAGE = "Timed out waiting for visible image/background assets"
DOM_TIMEOUT_MESSAGE = "Timed out waiting for DOM/layout stability"
_ASSET_KINDS: dict[str, AssetKind] = {
    "img": "img",
    "background": "background",
    "stylesheet": "stylesheet",
    "font": "font",
}
_MISSING_STATUSES = frozenset({404, 410})
# Categories retry_in_place re-requests. "missing" (404/410) will not come
# back by asking again; "pending" is still loading and a re-request would
# restart it.
_RETRYABLE: frozenset[str] = frozenset({"blocked", "network", "unknown"})
_IMAGE_KINDS = frozenset({"img", "background"})

# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

# Init script of every capture context (top frame only, see
# profiles.capture_init_script). Records when the page last changed: a DOM
# mutation (nodes, text, or the src/srcset/class/style/hidden/open attributes)
# or a resize of the root element. Our own DOM-changing scripts end with
# ``window.__qaActivity.drain()`` so their changes do not count.
# The leading ";" keeps it a separate statement whatever precedes it in the
# combined init script, and it never throws (that would stop the scripts after
# it).
JS_ACTIVITY_TRACKER = """;(() => { /*qa:activity-tracker*/
    try {
        if (window.top !== window || window.__qaActivity) {
            return;
        }
        const now = () => performance.now();
        let lastActivity = now();
        let observer = null;
        let resizeObserver = null;
        let mutedBy = 0;
        let drains = 0;
        const touch = () => {
            lastActivity = now();
        };
        // documentElement does not exist yet when the init script runs.
        const watchResize = () => {
            if (resizeObserver || typeof ResizeObserver !== 'function') {
                return;
            }
            const root = document.documentElement;
            if (!root) {
                return;
            }
            try {
                resizeObserver = new ResizeObserver(() => {
                    if (!mutedBy) {
                        touch();
                    }
                });
                resizeObserver.observe(root);
            } catch (_) {
                resizeObserver = null;
            }
        };
        const api = {
            get lastActivity() {
                return lastActivity;
            },
            quietForMs() {
                return now() - lastActivity;
            },
            // Forget the mutations queued so far (the caller's own DOM
            // changes) and the root resize they cause in the next frame.
            // Leaves lastActivity alone.
            drain() {
                let taken = 0;
                try {
                    taken = observer ? observer.takeRecords().length : 0;
                } catch (_) {}
                const token = ++drains;
                mutedBy = token;
                const unmute = () => {
                    if (mutedBy === token) {
                        mutedBy = 0;
                    }
                };
                try {
                    requestAnimationFrame(() => requestAnimationFrame(unmute));
                } catch (_) {}
                setTimeout(unmute, 250);
                watchResize();
                return taken;
            },
        };
        Object.defineProperty(window, '__qaActivity', {
            value: api,
            configurable: true,
        });
        if (typeof MutationObserver === 'function') {
            observer = new MutationObserver(() => {
                touch();
                watchResize();
            });
            observer.observe(document, {
                subtree: true,
                childList: true,
                characterData: true,
                attributes: true,
                attributeFilter: ['src', 'srcset', 'class', 'style', 'hidden', 'open'],
            });
        }
        watchResize();
    } catch (_) {}
})();"""

# Bounded in the page itself (resolves on timeout) so the Python side never
# has to abandon a pending evaluate — see jsutil.evaluate_self_bounded.
# Returns true when the fonts are ready, else {ready: false, families}.
# Relies on document.fonts.ready, not on document.fonts.status: WebKit reports
# "loaded" while a font request is still pending (and after ~3 s marks that
# face "error" although ready still never settles).
JS_WAIT_FOR_FONTS = """async (timeoutMs) => { /*qa:wait-fonts*/
    const fonts = document.fonts;
    if (!fonts || !fonts.ready) {
        return true;
    }
    const ready = await Promise.race([
        fonts.ready.then(() => true, () => true),
        new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs)),
    ]);
    if (ready) {
        return true;
    }
    const familiesWith = (status) => {
        const names = [];
        try {
            fonts.forEach((face) => {
                if (face.status !== status || names.length >= 50) {
                    return;
                }
                const name = String(face.family || '').replace(/^["']|["']$/g, '');
                if (name && !names.includes(name)) {
                    names.push(name);
                }
            });
        } catch (_) {}
        return names;
    };
    const loading = familiesWith('loading');
    return {
        ready: false,
        families: loading.length ? loading : familiesWith('error'),
    };
}"""


# Waits for the visible images and CSS backgrounds (each at most 5 s, all at
# once). The overall deadline is enforced in the page: on timeout it resolves
# with what it knows so far plus timedOut/pendingCount/pendingSamples, so the
# Python side never has to abandon a pending evaluate. missingSamples lists the
# broken (or still loading after their own wait: pending) assets, images first.
JS_DECODE_VISIBLE_IMAGES = """async (timeoutMs) => { /*qa:decode-images*/
  const overallTimeout = timeoutMs || 5000;
  const perAssetTimeout = Math.max(500, Math.min(overallTimeout, 5000));
  const MAX_SAMPLES = 50;
  const images = [];
  const backgrounds = [];
  let hiddenPendingImageCount = 0;

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

  const clip = (url) => (url.length > 4096 ? url.slice(0, 4096) : url);

  const waitWithTimeout = (promise) => Promise.race([
      promise,
      new Promise((resolve) => setTimeout(() => resolve('timeout'), perAssetTimeout)),
  ]);

  const waitForElementImage = async (entry) => {
      const img = entry.el;
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
      entry.ok = img.complete && img.naturalWidth > 0;
      entry.pending = !img.complete;
      entry.done = true;
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

  const waitForBackgroundUrl = async (entry) => {
      const loaded = await waitWithTimeout(new Promise((resolve) => {
          const img = new Image();
          img.onload = () => resolve(true);
          img.onerror = () => resolve(false);
          img.src = entry.url;
          if (img.complete && img.naturalWidth > 0) {
              resolve(true);
          }
      }));
      entry.ok = loaded === true;
      entry.pending = loaded === 'timeout';
      entry.done = true;
  };

  const sample = (entry) => ({
      kind: entry.kind,
      url: clip(entry.url),
      pending: !entry.done || !!entry.pending,
  });

  const summary = (timedOut) => {
      const missingImages = images.filter((entry) => entry.done && !entry.ok);
      const missingBackgrounds = backgrounds.filter(
          (entry) => entry.done && !entry.ok
      );
      const result = {
          visibleImageCount: images.length,
          visibleBackgroundCount: backgrounds.length,
          missingVisibleImageCount: missingImages.length,
          missingBackgroundImageCount: missingBackgrounds.length,
          hiddenPendingImageCount,
          missingSamples: missingImages.concat(missingBackgrounds)
              .slice(0, MAX_SAMPLES)
              .map(sample),
      };
      if (timedOut) {
          const unresolved = images.concat(backgrounds)
              .filter((entry) => !entry.done);
          result.timedOut = true;
          result.pendingCount = unresolved.length;
          result.pendingSamples = unresolved.slice(0, MAX_SAMPLES).map(sample);
      }
      return result;
  };

  const run = async () => {
      const allImages = Array.from(document.images).filter((img) => {
          const src = img.currentSrc || img.src;
          return !!src;
      });
      allImages.forEach((img) => {
          if (visible(img)) {
              images.push({
                  kind: 'img', url: img.currentSrc || img.src, el: img,
                  done: false, ok: false, pending: false,
              });
          } else if (!img.complete || img.naturalWidth === 0) {
              hiddenPendingImageCount += 1;
          }
      });

      const backgroundUrls = new Set();
      Array.from(document.querySelectorAll('*')).forEach((el) => {
          if (!visible(el)) {
              return;
          }
          try {
              extractBackgroundUrls(window.getComputedStyle(el).backgroundImage)
                  .forEach((url) => backgroundUrls.add(url));
          } catch (_) {}
      });
      backgroundUrls.forEach((url) => {
          backgrounds.push({
              kind: 'background', url, done: false, ok: false, pending: false,
          });
      });

      await Promise.allSettled(
          images.map(waitForElementImage)
              .concat(backgrounds.map(waitForBackgroundUrl))
      );
      return summary(false);
  };
  let timer = null;
  const result = await Promise.race([
      run(),
      new Promise((resolve) => {
          timer = setTimeout(() => resolve(summary(true)), overallTimeout);
      }),
  ]);
  clearTimeout(timer);
  return result;
}"""


# pendingLazyRender only looks at the elements prepare forced visible
# (data-qa-cv-forced) and WP Rocket's lazy-render blocks — no style walk of
# the whole page every sample. quietForMs comes from JS_ACTIVITY_TRACKER
# (null when the tracker is not installed).
JS_COLLECT_STABILITY_STATE = """() => { /*qa:stability-state*/
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
        document.querySelectorAll('[data-qa-cv-forced],[data-wpr-lazyrender]')
    ).filter((el) => {
        try {
            return window.getComputedStyle(el).contentVisibility === 'auto';
        } catch (_) {
            return false;
        }
    }).length;

    let quietForMs = null;
    try {
        const activity = window.__qaActivity;
        if (activity && typeof activity.quietForMs === 'function') {
            quietForMs = activity.quietForMs();
        }
    } catch (_) {}

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
        quietForMs,
    };
}"""


# Blank check: visible graphics that are content without being text or an
# image/background — a page of only svg/canvas/video/iframe is not blank.
JS_VISIBLE_MEDIA = """() => { /*qa:visible-media*/
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
    let count = 0;
    document.querySelectorAll('svg, canvas, video, iframe').forEach((el) => {
        if (visible(el)) {
            count += 1;
        }
    });
    return count;
}"""


# Stylesheet links without a sheet. Only a supplementary signal: every engine
# keeps an (empty) sheet for a stylesheet it refused over its MIME type, so the
# network records stay the main one.
JS_BROKEN_STYLESHEETS = """() => { /*qa:broken-stylesheets*/
    const urls = [];
    document.querySelectorAll('link[rel~="stylesheet"]').forEach((link) => {
        try {
            const alternate = link.relList && link.relList.contains('alternate');
            if (link.disabled || alternate || !link.href) {
                return;
            }
            if (link.sheet === null && !urls.includes(link.href) && urls.length < 50) {
                urls.push(link.href);
            }
        } catch (_) {}
    });
    return urls;
}"""


# Same-URL re-request of broken images and stylesheets (no cache-busting:
# signed CDN URLs must keep working). Each attribute is removed and set back
# to the same value: WebKit ignores src set to its current value, and no
# engine reloads a stylesheet whose href is set to the same value. The link
# keeps its element, so the cascade order stays. Progress is kept in
# window.__qaRetry for JS_RETRY_STATUS.
JS_RETRY_ASSETS = """(targets) => { /*qa:retry-assets*/
    const imageUrls = new Set((targets && targets.images) || []);
    const sheetUrls = new Set((targets && targets.stylesheets) || []);
    const entries = [];
    const reassign = (el, name) => {
        if (!el.hasAttribute(name)) {
            return;
        }
        const value = el.getAttribute(name);
        el.removeAttribute(name);
        el.setAttribute(name, value);
    };
    const track = (el, kind) => {
        const entry = { el, kind, done: false };
        const finish = () => {
            entry.done = true;
        };
        el.addEventListener('load', finish, { once: true });
        el.addEventListener('error', finish, { once: true });
        entries.push(entry);
    };
    if (imageUrls.size) {
        Array.from(document.images).forEach((img) => {
            try {
                const url = img.currentSrc || img.src;
                if (!url || !imageUrls.has(url)) {
                    return;
                }
                if (img.complete && img.naturalWidth > 0) {
                    return;
                }
                if (img.loading === 'lazy') {
                    img.loading = 'eager';
                }
                track(img, 'img');
                const parent = img.parentElement;
                if (parent && parent.tagName === 'PICTURE') {
                    parent.querySelectorAll('source').forEach(
                        (source) => reassign(source, 'srcset')
                    );
                }
                reassign(img, 'srcset');
                reassign(img, 'src');
            } catch (_) {}
        });
    }
    if (sheetUrls.size) {
        document.querySelectorAll('link[rel~="stylesheet"]').forEach((link) => {
            try {
                if (!link.href || !sheetUrls.has(link.href)) {
                    return;
                }
                track(link, 'stylesheet');
                reassign(link, 'href');
            } catch (_) {}
        });
    }
    window.__qaRetry = entries;
    try {
        if (window.__qaActivity) {
            window.__qaActivity.drain();
        }
    } catch (_) {}
    return {
        images: entries.filter((entry) => entry.kind === 'img').length,
        stylesheets: entries.filter((entry) => entry.kind === 'stylesheet').length,
    };
}"""


# How many re-requested elements have neither loaded nor failed yet.
JS_RETRY_STATUS = """() => { /*qa:retry-status*/
    const entries = window.__qaRetry || [];
    let pending = 0;
    entries.forEach((entry) => {
        if (entry.done) {
            return;
        }
        if (entry.kind === 'img' && entry.el.complete) {
            return;
        }
        pending += 1;
    });
    return { pending, total: entries.length };
}"""


# Per stylesheet URL: true when a link with that href has a sheet with rules
# (or rules hidden from us: cross-origin), false when its links have none,
# null when no link has that href.
JS_STYLESHEET_STATE = """(urls) => { /*qa:stylesheet-state*/
    const state = {};
    (urls || []).forEach((url) => {
        state[url] = null;
    });
    document.querySelectorAll('link[rel~="stylesheet"]').forEach((link) => {
        const url = link.href;
        if (!url || !Object.prototype.hasOwnProperty.call(state, url)) {
            return;
        }
        let ok = false;
        try {
            const sheet = link.sheet;
            if (sheet) {
                try {
                    ok = sheet.cssRules.length > 0;
                } catch (_) {
                    ok = true;
                }
            }
        } catch (_) {}
        state[url] = state[url] === true || ok;
    });
    return state;
}"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


async def _evaluate(page: Page, script: str, arg: Any = None) -> Any:
    """A readiness script that does not bound itself; None ("no data") when
    it does not finish within cfg.READINESS_SCRIPT_TIMEOUT_MS."""
    try:
        return await jsutil.evaluate(
            page, script, arg, timeout_ms=cfg.READINESS_SCRIPT_TIMEOUT_MS
        )
    except EvaluateTimeout:
        logger.info("readiness script %s timed out", jsutil.script_marker(script))
        return None


def _unique(values: Iterable[str]) -> list[str]:
    return list(dict.fromkeys(value for value in values if value))


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


def _drop_warning(report: CaptureReport, code: str) -> None:
    """Remove this module's warning *code* so a newer verdict can replace it:
    the report describes the page as last checked, not every check."""
    report.warnings[:] = [
        warning for warning in report.warnings if warning.code != code
    ]


def _seconds(ms: int) -> str:
    return f"{ms / 1000:g} s"


# ---------------------------------------------------------------------------
# Fonts
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class FontsState:
    ready: bool = True
    # Font families still loading when the wait timed out (deduplicated).
    families: tuple[str, ...] = ()


def fonts_state_from_raw(raw: object) -> FontsState:
    """``true`` / no data → ready; ``false`` or ``{ready: false, families}`` →
    not ready, with at most cfg.FONTS_TIMEOUT_MAX_FAMILIES families."""
    if raw is False:
        return FontsState(ready=False)
    if not isinstance(raw, dict) or raw.get("ready") is not False:
        return FontsState()
    families = raw.get("families")
    names: list[str] = []
    if isinstance(families, list):
        names = _unique(str(family) for family in families if family)
    return FontsState(
        ready=False, families=tuple(names[: cfg.FONTS_TIMEOUT_MAX_FAMILIES])
    )


async def fonts_state(page: Page, timeout_ms: int) -> FontsState:
    """Wait up to *timeout_ms* for ``document.fonts.ready``."""
    try:
        raw = await jsutil.evaluate_self_bounded(page, JS_WAIT_FOR_FONTS, timeout_ms)
    except (TimeoutError, EvaluateTimeout):
        return FontsState(ready=False)
    return fonts_state_from_raw(raw)


async def wait_for_fonts(page: Page, timeout_ms: int) -> None:
    """Wait up to *timeout_ms* for the fonts; PageStabilizationTimeoutError
    when they are not ready (prepare ignores it, finalize only warns)."""
    if not (await fonts_state(page, timeout_ms)).ready:
        raise PageStabilizationTimeoutError(FONTS_TIMEOUT_MESSAGE)


def _note_fonts(report: CaptureReport, state: FontsState, timeout_ms: int) -> None:
    _drop_warning(report, "fonts_timeout")
    if state.ready:
        return
    message = f"Web fonts were still loading after {_seconds(timeout_ms)}"
    if state.families:
        message += ": " + ", ".join(state.families)
    report.warn(
        "fonts_timeout",
        message,
        families=list(state.families),
        timeout_ms=timeout_ms,
    )


# ---------------------------------------------------------------------------
# Visible images / backgrounds
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AssetSample:
    """A broken or still loading visible image / CSS background, as the
    decode script reported it (full URL)."""

    kind: AssetKind
    url: str
    pending: bool = False


def int_from_mapping(raw: dict[str, Any], key: str) -> int:
    try:
        return int(raw.get(key, 0) or 0)
    except (TypeError, ValueError):
        return 0


def asset_readiness_from_raw(raw: object) -> AssetReadinessResult:
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
            samples.append(f"{kind}: {network.shorten(url, 180)}")

    return AssetReadinessResult(
        visible_image_count=int_from_mapping(raw, "visibleImageCount"),
        visible_background_count=int_from_mapping(raw, "visibleBackgroundCount"),
        missing_visible_image_count=int_from_mapping(raw, "missingVisibleImageCount"),
        missing_background_image_count=int_from_mapping(
            raw,
            "missingBackgroundImageCount",
        ),
        hidden_pending_image_count=int_from_mapping(raw, "hiddenPendingImageCount"),
        missing_samples=tuple(samples),
    )


def asset_samples_from_raw(raw: object) -> list[AssetSample]:
    """The broken and still pending assets of a decode result (the page sends
    at most 50 of each), deduplicated."""
    if not isinstance(raw, dict):
        return []
    samples: dict[tuple[str, str], AssetSample] = {}
    for key in ("missingSamples", "pendingSamples"):
        entries = raw.get(key)
        if not isinstance(entries, list):
            continue
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            kind = _ASSET_KINDS.get(str(entry.get("kind") or ""), "img")
            if kind not in _IMAGE_KINDS:
                kind = "img"
            url = str(entry.get("url") or "")
            pending = key == "pendingSamples" or entry.get("pending") is True
            samples.setdefault((kind, url), AssetSample(kind, url, pending))
    return list(samples.values())


def format_asset_failure(result: AssetReadinessResult) -> str:
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


def is_blank_capture_state(
    state: dict[str, int | str],
    asset_state: AssetReadinessResult,
) -> bool:
    try:
        text_length = int(state.get("textLength", 0) or 0)
    except (TypeError, ValueError):
        text_length = 0
    return text_length == 0 and asset_state.visible_asset_count == 0


async def decode_visible_images(
    page: Page,
    timeout_ms: int,
    *,
    samples: list[AssetSample] | None = None,
) -> AssetReadinessResult:
    """Wait for visible images / CSS backgrounds to load and decode.

    Raises PageAssetLoadError (verdict in ``.assets``) when some did not, and
    one with DECODE_TIMEOUT_MESSAGE (``.assets``: the partial verdict, if the
    page sent one) when the wait timed out. When *samples* is given it
    receives every broken or still pending asset the page reported, with its
    full URL.
    """
    try:
        raw = await jsutil.evaluate_self_bounded(
            page, JS_DECODE_VISIBLE_IMAGES, timeout_ms
        )
    except (TimeoutError, EvaluateTimeout) as exc:
        raise PageAssetLoadError(DECODE_TIMEOUT_MESSAGE) from exc
    if samples is not None:
        samples.extend(asset_samples_from_raw(raw))
    if isinstance(raw, dict) and raw.get("timedOut"):
        partial = asset_readiness_from_raw(raw) if "visibleImageCount" in raw else None
        raise PageAssetLoadError(DECODE_TIMEOUT_MESSAGE, assets=partial)

    result = asset_readiness_from_raw(raw)
    if result.hidden_pending_image_count:
        logger.debug(
            "ignoring %d hidden pending image(s) during asset readiness",
            result.hidden_pending_image_count,
        )
    if result.missing_count:
        raise PageAssetLoadError(format_asset_failure(result), assets=result)
    return result


def asset_issues_from_error(error: PageAssetLoadError) -> tuple[AssetIssue, ...]:
    """One AssetIssue per missing sample of *error*; a single issue when it has
    no samples (category "pending" for a timed-out decode)."""
    reason = str(error)
    assets = getattr(error, "assets", None)
    samples = assets.missing_samples if assets is not None else ()
    if not samples:
        category = "pending" if reason.startswith(DECODE_TIMEOUT_MESSAGE) else "unknown"
        return (AssetIssue(kind="img", url="", category=category, reason=reason),)
    issues: list[AssetIssue] = []
    for sample in samples:
        kind, separator, url = sample.partition(": ")
        if not separator:
            kind, url = "img", sample
        issues.append(
            AssetIssue(
                kind=_ASSET_KINDS.get(kind, "img"),
                url=url,
                category="unknown",
                reason=reason,
            )
        )
    return tuple(issues)


# ---------------------------------------------------------------------------
# Classification
# ---------------------------------------------------------------------------


def _is_network_failure(failure: str) -> bool:
    text = failure.lower()
    return any(marker.lower() in text for marker in cfg.NETWORK_FAILURE_MARKERS)


def _describe(record: ResourceRecord) -> str:
    parts: list[str] = []
    if record.status is not None:
        parts.append(f"HTTP {record.status}")
    mime = (record.content_type or "").split(";", 1)[0].strip()
    if mime:
        parts.append(mime)
    if record.failure:
        parts.append(record.failure)
    if record.refused:
        parts.append("refused by the browser")
    if not record.finished and not record.failure:
        parts.append("not finished")
    return ", ".join(parts) or "no details"


def classify(
    record: ResourceRecord | None, *, pending: bool | None = None
) -> tuple[AssetCategory, str]:
    """``(category, reason)`` of a broken asset from what the network saw.

    404/410 → "missing"; blocked-looking (401/403/429/503, HTML where a
    stylesheet/image/font belongs, aborted/blocked, refused) → "blocked";
    a connection/timeout failure → "network"; still loading → "pending"
    (*pending* is the page's own view; without it an unfinished request);
    anything else → "unknown".
    """
    if record is not None:
        status = record.status
        if status in _MISSING_STATUSES:
            return "missing", f"HTTP {status}"
        if record.blocked_like():
            return "blocked", _describe(record)
        if record.failure and _is_network_failure(record.failure):
            return "network", record.failure
    if pending is None:
        pending = record is not None and not record.finished and not record.failure
    if pending:
        return "pending", "still loading"
    if record is None:
        return "unknown", "no network record"
    return "unknown", _describe(record)


def _healthy(record: ResourceRecord) -> bool:
    """The (latest) request for this URL loaded fine."""
    status_ok = record.status is None or record.status < 400
    return (
        record.finished
        and not record.failure
        and status_ok
        and not record.blocked_like()
    )


def classify_asset(
    net: NetworkDiagnostics,
    kind: AssetKind,
    url: str,
    *,
    pending: bool | None = None,
) -> AssetIssue:
    """The AssetIssue for a broken *kind* asset at *url*, classified by
    ``net.lookup(url)``."""
    record = net.lookup(url) if url else None
    category, reason = classify(record, pending=pending)
    return AssetIssue(
        kind=kind,
        url=url,
        category=category,
        status=record.status if record is not None else None,
        reason=reason,
    )


def _issues_from_samples(
    net: NetworkDiagnostics,
    samples: list[AssetSample],
    error: PageAssetLoadError | None,
) -> tuple[AssetIssue, ...]:
    if samples:
        return tuple(
            classify_asset(net, sample.kind, sample.url, pending=sample.pending)
            for sample in samples
        )
    if error is None:
        return ()
    # No per-asset detail from the page (it timed out before telling): what
    # the error carries.
    issues: list[AssetIssue] = []
    for issue in asset_issues_from_error(error):
        if issue.url:
            issue = classify_asset(net, issue.kind, issue.url)
        issues.append(issue)
    return tuple(issues)


def _resource_issue(
    net: NetworkDiagnostics, kind: AssetKind, url: str, *, refused: bool = False
) -> AssetIssue:
    record = net.lookup(url)
    if record is None and refused:
        return AssetIssue(
            kind=kind, url=url, category="blocked", reason="refused by the browser"
        )
    category, reason = classify(record)
    return AssetIssue(
        kind=kind,
        url=url,
        category=category,
        status=record.status if record is not None else None,
        reason=reason,
    )


async def stylesheet_issues(run: CaptureRun) -> tuple[AssetIssue, ...]:
    """Stylesheets the network saw fail (error status, failure, HTML instead
    of CSS), the ones the engine refused (console), and links whose request
    finished but that have no sheet — one issue per URL. A URL whose latest
    request loaded fine (a successful re-request) is not an issue."""
    net = run.net
    issues: dict[str, AssetIssue] = {}

    def add(url: str, *, refused: bool = False) -> None:
        if not url or url in issues:
            return
        record = net.lookup(url)
        if record is not None and _healthy(record):
            return
        issues[url] = _resource_issue(net, "stylesheet", url, refused=refused)

    for record in net.problem_resources(kinds=("stylesheet",)):
        add(record.url)
    for url in net.refused_stylesheets():
        add(url, refused=True)
    broken = await _evaluate(run.page, JS_BROKEN_STYLESHEETS)
    if isinstance(broken, list):
        for url in broken:
            url = str(url or "")
            if not url or url in issues:
                continue
            record = net.lookup(url)
            if record is None or not (record.finished or record.failure):
                continue  # still loading (or never requested): no verdict yet
            category, reason = classify(record)
            if category == "unknown":
                reason = f"stylesheet not applied ({reason})"
            issues[url] = AssetIssue(
                kind="stylesheet",
                url=url,
                category=category,
                status=record.status,
                reason=reason,
            )
    return tuple(issues.values())[: cfg.MAX_ASSET_ISSUES]


def font_issues(run: CaptureRun) -> tuple[AssetIssue, ...]:
    """Font files the network saw fail (informational: the text still renders
    in a fallback font)."""
    net = run.net
    issues: dict[str, AssetIssue] = {}
    for record in net.problem_resources(kinds=("font",)):
        url = record.url
        if not url or url in issues:
            continue
        latest = net.lookup(url)
        if latest is not None and _healthy(latest):
            continue
        issues[url] = _resource_issue(net, "font", url)
    return tuple(issues.values())[: cfg.MAX_ASSET_ISSUES]


def issue_sample(issue: AssetIssue) -> dict[str, Any]:
    """A warning sample for *issue* (the shape the UI reads)."""
    return {
        "kind": issue.kind,
        "url": issue.url,
        "status": issue.status,
        "category": issue.category,
        "reason": issue.reason,
    }


def _issue_text(issue: AssetIssue) -> str:
    text = f"{issue.kind}: {network.shorten(issue.url, 180) or '(no url)'}"
    if issue.reason:
        text += f" ({network.shorten(issue.reason, 80)})"
    return text


def issues_message(issues: tuple[AssetIssue, ...], *, base: str | None = None) -> str:
    """Error text for *issues*: *base* (the decode error's own text) plus the
    stylesheet / font issues; without *base* every issue is listed."""
    parts: list[str] = []
    listed = issues
    if base:
        parts.append(base)
        listed = tuple(issue for issue in issues if issue.kind not in _IMAGE_KINDS)
    if listed:
        counts: dict[str, int] = {}
        for issue in listed:
            counts[issue.kind] = counts.get(issue.kind, 0) + 1
        summary = ", ".join(f"{count} {kind}" for kind, count in counts.items())
        samples = "; ".join(_issue_text(issue) for issue in listed[:5])
        parts.append(
            f"Page assets did not load: {summary}; samples: {samples}"
            if not base
            else f"also did not load: {summary}; samples: {samples}"
        )
    return "; ".join(parts) or "Visible page assets did not load"


def _note_pending(
    report: CaptureReport, issues: tuple[AssetIssue, ...], timeout_ms: int
) -> None:
    _drop_warning(report, "assets_pending")
    pending = [
        issue
        for issue in issues
        if issue.category == "pending" and issue.kind in _IMAGE_KINDS
    ]
    if not pending:
        return
    warning = report.warn(
        "assets_pending",
        f"{len(pending)} visible image(s) still loading after {_seconds(timeout_ms)}",
        samples=[issue_sample(issue) for issue in pending if issue.url],
        timeout_ms=timeout_ms,
    )
    # This warning is replaced, never merged, so its count is free to say how
    # many assets are pending (what the UI shows).
    warning.details["count"] = len(pending)


# ---------------------------------------------------------------------------
# DOM stability
# ---------------------------------------------------------------------------


_UNKNOWN_STATE: dict[str, int | str] = {
    "readyState": "unknown",
    "scrollHeight": 0,
    "pendingImages": 0,
    "pendingVisibleImages": 0,
    "hiddenPendingImages": 0,
    "pendingLazyRender": 0,
    "textLength": 0,
}


async def collect_stability_state(page: Page) -> dict[str, Any]:
    state = await _evaluate(page, JS_COLLECT_STABILITY_STATE)
    if not isinstance(state, dict):
        return dict(_UNKNOWN_STATE)
    return state


def same_stability_state(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
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


def quiet_enough(state: dict[str, Any], tracker: InflightTracker | None) -> bool:
    """Fast-path condition: the activity tracker saw no DOM change for
    cfg.FAST_PATH_QUIET_MS and no tracked request is in flight. False when
    either is unknown (no tracker in the page, no InflightTracker)."""
    quiet_ms = _number(state.get("quietForMs"))
    if quiet_ms is None or tracker is None:
        return False
    return quiet_ms >= cfg.FAST_PATH_QUIET_MS and tracker.inflight() == 0


async def wait_for_dom_stability(
    page: Page,
    timeout_ms: int,
    *,
    allow_interactive_ready_state: bool = False,
    tracker: InflightTracker | None = None,
) -> bool:
    """Wait until the page is ready (readyState, no pending visible image or
    lazy-render block) and stable; True when the fast path ended the wait.

    Stable is STABILITY_REQUIRED_CYCLES further identical samples, one every
    STABILITY_SAMPLE_MS — the only criterion that can fail, with
    PageStabilizationTimeoutError after *timeout_ms*. The fast path only ends
    the wait earlier: a ready sample whose page has been quiet for
    FAST_PATH_QUIET_MS (in-page activity tracker) while *tracker* has nothing
    in flight counts as stable at once — checked on entry and every sample.
    """
    deadline = clock.now() + (timeout_ms / 1000)
    stable_cycles = 0
    previous_state: dict[str, Any] | None = None
    ready_states = {"complete"}
    if allow_interactive_ready_state:
        ready_states.add("interactive")

    while clock.now() < deadline:
        current_state = await collect_stability_state(page)
        is_ready = (
            current_state.get("readyState") in ready_states
            and current_state.get(
                "pendingVisibleImages",
                current_state.get("pendingImages"),
            )
            == 0
            and current_state.get("pendingLazyRender") == 0
        )
        if is_ready and quiet_enough(current_state, tracker):
            return True
        if is_ready and same_stability_state(previous_state, current_state):
            stable_cycles += 1
            if stable_cycles >= cfg.STABILITY_REQUIRED_CYCLES:
                return False
        else:
            stable_cycles = 0
        previous_state = current_state
        await page.wait_for_timeout(cfg.STABILITY_SAMPLE_MS)

    raise PageStabilizationTimeoutError(DOM_TIMEOUT_MESSAGE)


async def count_visible_media(page: Page) -> int | None:
    """Visible svg/canvas/video/iframe elements; None when the page did not
    answer in time."""
    try:
        count = await jsutil.evaluate(
            page, JS_VISIBLE_MEDIA, timeout_ms=cfg.READINESS_SCRIPT_TIMEOUT_MS
        )
    except EvaluateTimeout:
        return None
    number = _number(count)
    return int(number) if number is not None else 0


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


async def finalize(
    run: CaptureRun,
    *,
    window_ms: int,
    allow_interactive_ready_state: bool | None = None,
) -> ReadinessResult:
    """Readiness verdict within *window_ms*. Never stops early and never
    raises for what it finds:

    1. fonts, at most cfg.FONTS_WAIT_CAP_MS — a timeout is only the
       ``fonts_timeout`` warning (``fonts_ready=False``);
    2. visible images / CSS backgrounds, at most cfg.DECODE_WAIT_CAP_MS — the
       broken ones become ``issues`` classified by ``run.net``; still loading
       ones (or a timed-out decode) are category "pending" plus the
       ``assets_pending`` warning;
    3. DOM stability (activity fast path or three equal samples) — else
       ``stable=False``, ``unstable_reason="dom"``;
    4. a settled page with no visible text, image, background, svg, canvas,
       video or iframe → ``blank=True``;
    5. stylesheet issues (network, console refusals, links without a sheet)
       and failed font files (informational) join ``issues``.

    When there are issues ``run.scratch["asset_error"]`` holds their error
    text (the decode error's own text first). The ``readiness`` meta section
    gets stable / fast_path / fonts_ready; this module's warnings describe the
    latest verdict. *allow_interactive_ready_state* defaults to
    ``run.nav.allow_interactive``.
    """
    page = run.page
    report = run.report
    if allow_interactive_ready_state is None:
        allow_interactive_ready_state = (
            run.nav.allow_interactive if run.nav is not None else False
        )
    run.scratch.pop("asset_error", None)

    fonts_wait_ms = min(window_ms, cfg.FONTS_WAIT_CAP_MS)
    fonts = await fonts_state(page, fonts_wait_ms)
    _note_fonts(report, fonts, fonts_wait_ms)
    if not fonts.ready:
        logger.info(
            "fonts not ready after %d ms for %s: %s",
            fonts_wait_ms,
            run.url,
            ", ".join(fonts.families) or "unknown families",
        )

    decode_ms = min(window_ms, cfg.DECODE_WAIT_CAP_MS)
    samples: list[AssetSample] = []
    decode_error: PageAssetLoadError | None = None
    assets: AssetReadinessResult | None
    try:
        assets = await decode_visible_images(page, decode_ms, samples=samples)
    except PageAssetLoadError as exc:
        decode_error = exc
        assets = exc.assets
    asset_issues = _issues_from_samples(
        run.net, samples[: cfg.MAX_ASSET_ISSUES], decode_error
    )
    decode_timed_out = decode_error is not None and str(decode_error).startswith(
        DECODE_TIMEOUT_MESSAGE
    )

    stable = True
    fast_path = False
    try:
        fast_path = bool(
            await wait_for_dom_stability(
                page,
                window_ms,
                allow_interactive_ready_state=allow_interactive_ready_state,
                tracker=run.tracker,
            )
        )
    except PageStabilizationTimeoutError:
        stable = False

    blank = False
    if stable:
        final_state = await collect_stability_state(page)
        still_loading = decode_timed_out or any(
            issue.category == "pending" for issue in asset_issues
        )
        if not still_loading and is_blank_capture_state(
            final_state, assets or AssetReadinessResult()
        ):
            blank = await count_visible_media(page) == 0

    issues = asset_issues + await stylesheet_issues(run) + font_issues(run)
    if issues:
        base = str(decode_error) if decode_error is not None else None
        run.scratch["asset_error"] = issues_message(issues, base=base)
    _note_pending(report, issues, decode_ms)
    report.set_meta(
        "readiness", stable=stable, fast_path=fast_path, fonts_ready=fonts.ready
    )
    return ReadinessResult(
        assets=assets,
        issues=issues,
        fonts_ready=fonts.ready,
        stable=stable,
        unstable_reason=None if stable else "dom",
        blank=blank,
        fast_path=fast_path,
    )


# ---------------------------------------------------------------------------
# In-place retry
# ---------------------------------------------------------------------------


async def _wait_for_retried_assets(run: CaptureRun, *, requested: bool) -> None:
    """Until the re-requested elements (when *requested*) loaded or failed
    and no tracked request is in flight — at most cfg.RETRY_WAIT_MS, and only
    cfg.RETRY_IDLE_MS while elements wait with nothing on the wire (the engine
    did not fetch again)."""
    page = run.page
    tracker = run.tracker
    started = clock.now()
    waited = 0.0
    idle_since: float | None = None
    while True:
        elapsed = max(waited, (clock.now() - started) * 1000)
        pending = 0
        if requested:
            status = await _evaluate(page, JS_RETRY_STATUS)
            if isinstance(status, dict):
                pending = int_from_mapping(status, "pending")
        inflight = tracker.inflight() if tracker is not None else 0
        if pending == 0 and inflight == 0:
            return
        if inflight:
            idle_since = None
        elif idle_since is None:
            idle_since = elapsed
        elif elapsed - idle_since >= cfg.RETRY_IDLE_MS:
            return
        if elapsed >= cfg.RETRY_WAIT_MS:
            return
        step = min(cfg.RETRY_POLL_MS, cfg.RETRY_WAIT_MS - elapsed)
        await page.wait_for_timeout(step)
        waited += step


def _stylesheet_recovered(
    net: NetworkDiagnostics, url: str, dom_state: dict[str, Any]
) -> bool:
    record = net.lookup(url)
    if record is not None:
        return _healthy(record)
    return dom_state.get(url) is True


async def _recheck(
    run: CaptureRun, issues: tuple[AssetIssue, ...]
) -> tuple[AssetIssue, ...]:
    net = run.net
    image_issues = [
        issue
        for issue in issues
        if issue.kind in _IMAGE_KINDS and issue.category != "missing"
    ]
    broken: dict[tuple[str, str], AssetSample] = {}
    decode_blind = False  # the decode failed without telling which assets
    if image_issues:
        samples: list[AssetSample] = []
        try:
            await decode_visible_images(run.page, cfg.RETRY_DECODE_MS, samples=samples)
        except PageAssetLoadError:
            decode_blind = not samples
        broken = {(sample.kind, sample.url): sample for sample in samples}

    sheet_urls = _unique(
        issue.url
        for issue in issues
        if issue.kind == "stylesheet" and issue.category != "missing"
    )
    dom_state: dict[str, Any] = {}
    if sheet_urls:
        raw_state = await _evaluate(run.page, JS_STYLESHEET_STATE, sheet_urls)
        if isinstance(raw_state, dict):
            dom_state = raw_state

    still: list[AssetIssue] = []
    for issue in issues:
        if issue.kind == "font" or issue.category == "missing":
            still.append(issue)
        elif issue.kind in _IMAGE_KINDS:
            if not issue.url:
                if decode_blind or broken:
                    still.append(issue)
                continue
            sample = broken.get((issue.kind, issue.url))
            if sample is not None:
                still.append(
                    classify_asset(net, issue.kind, issue.url, pending=sample.pending)
                )
            elif decode_blind:
                still.append(issue)
        elif not issue.url:
            still.append(issue)
        elif not _stylesheet_recovered(net, issue.url, dom_state):
            if net.lookup(issue.url) is not None:
                issue = _resource_issue(net, "stylesheet", issue.url)
            still.append(issue)
    return tuple(still)


async def retry_in_place(
    run: CaptureRun, issues: tuple[AssetIssue, ...] | list[AssetIssue]
) -> tuple[AssetIssue, ...]:
    """Re-request the assets behind *issues* on the same page and return the
    ones still broken (re-classified), in their original order.

    Same-URL re-request, no cache-busting: broken ``<img>`` get their
    ``<picture><source>`` srcset, srcset and src set again, broken stylesheet
    links their href (same element, so the cascade order stays). Not
    re-requested: "missing" (404/410) issues — returned untouched without any
    wait — "pending" ones (still loading), CSS backgrounds and fonts. Then up
    to cfg.RETRY_WAIT_MS for the re-requested elements and the in-flight
    requests, and a re-check: a decode with cfg.RETRY_DECODE_MS for images and
    backgrounds, the latest network record (or the link's sheet) for
    stylesheets. Font issues are informational and returned as they are.
    Keeps ``assets_pending`` and ``run.scratch["asset_error"]`` in line.
    """
    issues = tuple(issues)
    if not any(
        issue.kind != "font" and issue.category != "missing" for issue in issues
    ):
        return issues

    images = _unique(
        issue.url
        for issue in issues
        if issue.kind == "img" and issue.category in _RETRYABLE
    )
    stylesheets = _unique(
        issue.url
        for issue in issues
        if issue.kind == "stylesheet" and issue.category in _RETRYABLE
    )
    requested = bool(images or stylesheets)
    if requested:
        answer = await _evaluate(
            run.page,
            JS_RETRY_ASSETS,
            {"images": images, "stylesheets": stylesheets},
        )
        logger.info(
            "retrying assets in place for %s: %s", run.url, answer or "no answer"
        )
    await _wait_for_retried_assets(run, requested=requested)
    still = await _recheck(run, issues)

    previous = run.report.warning("assets_pending")
    if previous is not None:
        timeout_ms = previous.details.get("timeout_ms")
        _note_pending(
            run.report,
            still,
            timeout_ms if isinstance(timeout_ms, int) else cfg.RETRY_DECODE_MS,
        )
    if still:
        run.scratch["asset_error"] = issues_message(still)
    else:
        run.scratch.pop("asset_error", None)
    return still
