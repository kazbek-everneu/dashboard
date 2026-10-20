"""Readiness stage: fonts, visible image/background decode, DOM stability and
the blank-page check, folded into one ReadinessResult verdict.

Every script here carries a ``/*qa:<name>*/`` marker (see jsutil).
"""

import logging
from typing import Any

from playwright.async_api import Page

from app.screenshots.capture import clock, jsutil, network
from app.screenshots.capture import config as cfg
from app.screenshots.capture.errors import (
    PageAssetLoadError,
    PageStabilizationTimeoutError,
)
from app.screenshots.capture.types import (
    AssetIssue,
    AssetKind,
    AssetReadinessResult,
    CaptureRun,
    ReadinessResult,
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

# ---------------------------------------------------------------------------
# JavaScript snippets
# ---------------------------------------------------------------------------

# Init-script activity tracker (added to every capture context, see profiles).
# Empty until the readiness fast path lands.
JS_ACTIVITY_TRACKER = ""

# Bounded in the page itself (resolves false on timeout) so the Python side
# never has to abandon a pending evaluate — see jsutil.evaluate_self_bounded.
JS_WAIT_FOR_FONTS = """async (timeoutMs) => { /*qa:wait-fonts*/
    if (!document.fonts || !document.fonts.ready) {
        return true;
    }
    return await Promise.race([
        document.fonts.ready.then(() => true),
        new Promise((resolve) => setTimeout(() => resolve(false), timeoutMs)),
    ]);
}"""


JS_DECODE_VISIBLE_IMAGES = """async (timeoutMs) => { /*qa:decode-images*/
  // Overall deadline enforced in the page (returns {timedOut: true}) so the
  // Python side never has to abandon a pending evaluate.
  const overallTimeout = timeoutMs || 5000;
  const run = async () => {
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
  };
  return await Promise.race([
    run(),
    new Promise((resolve) => setTimeout(
        () => resolve({ timedOut: true }), overallTimeout
    )),
  ]);
}"""


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


# ---------------------------------------------------------------------------
# Fonts / assets
# ---------------------------------------------------------------------------


async def wait_for_fonts(page: Page, timeout_ms: int) -> None:
    try:
        ready = await jsutil.evaluate_self_bounded(page, JS_WAIT_FOR_FONTS, timeout_ms)
    except TimeoutError as exc:
        raise PageStabilizationTimeoutError(FONTS_TIMEOUT_MESSAGE) from exc
    if ready is False:
        raise PageStabilizationTimeoutError(FONTS_TIMEOUT_MESSAGE)


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


async def decode_visible_images(page: Page, timeout_ms: int) -> AssetReadinessResult:
    """Wait for visible images / CSS backgrounds to load and decode. Raises
    PageAssetLoadError (with the verdict in ``.assets``) when some did not."""
    try:
        raw = await jsutil.evaluate_self_bounded(
            page, JS_DECODE_VISIBLE_IMAGES, timeout_ms
        )
    except TimeoutError as exc:
        raise PageAssetLoadError(DECODE_TIMEOUT_MESSAGE) from exc
    if isinstance(raw, dict) and raw.get("timedOut"):
        raise PageAssetLoadError(DECODE_TIMEOUT_MESSAGE)

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
# DOM stability
# ---------------------------------------------------------------------------


async def collect_stability_state(page: Page) -> dict[str, int | str]:
    state = await jsutil.evaluate(page, JS_COLLECT_STABILITY_STATE)
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


def same_stability_state(
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


async def wait_for_dom_stability(
    page: Page,
    timeout_ms: int,
    *,
    allow_interactive_ready_state: bool = False,
) -> None:
    deadline = clock.now() + (timeout_ms / 1000)
    stable_cycles = 0
    previous_state: dict[str, int | str] | None = None
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
        if is_ready and same_stability_state(previous_state, current_state):
            stable_cycles += 1
            if stable_cycles >= cfg.STABILITY_REQUIRED_CYCLES:
                return
        else:
            stable_cycles = 0
        previous_state = current_state
        await page.wait_for_timeout(cfg.STABILITY_SAMPLE_MS)

    raise PageStabilizationTimeoutError(DOM_TIMEOUT_MESSAGE)


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


async def finalize(
    run: CaptureRun,
    *,
    window_ms: int,
    allow_interactive_ready_state: bool | None = None,
) -> ReadinessResult:
    """Readiness verdict within *window_ms*: fonts, visible images, a stable
    DOM, then the blank check. Stops at the first problem:

    - fonts or DOM not settled → ``stable=False`` (``unstable_reason`` "fonts"
      / "dom");
    - visible assets missing → ``issues``; the exact error text is kept in
      ``run.scratch["asset_error"]``;
    - nothing visible at all → ``blank=True``.

    *allow_interactive_ready_state* defaults to ``run.nav.allow_interactive``.
    The page transforms that used to open this step are
    prepare.reapply_transforms, which the pipeline runs first.
    """
    page = run.page
    if allow_interactive_ready_state is None:
        allow_interactive_ready_state = (
            run.nav.allow_interactive if run.nav is not None else False
        )
    try:
        await wait_for_fonts(page, min(window_ms, cfg.FONTS_WAIT_CAP_MS))
    except PageStabilizationTimeoutError:
        return ReadinessResult(fonts_ready=False, stable=False, unstable_reason="fonts")
    try:
        asset_state = await decode_visible_images(
            page, min(window_ms, cfg.DECODE_WAIT_CAP_MS)
        )
    except PageAssetLoadError as exc:
        run.scratch["asset_error"] = str(exc)
        return ReadinessResult(
            assets=getattr(exc, "assets", None),
            issues=asset_issues_from_error(exc),
        )
    try:
        await wait_for_dom_stability(
            page,
            window_ms,
            allow_interactive_ready_state=allow_interactive_ready_state,
        )
    except PageStabilizationTimeoutError:
        return ReadinessResult(assets=asset_state, stable=False, unstable_reason="dom")
    final_state = await collect_stability_state(page)
    if is_blank_capture_state(final_state, asset_state):
        return ReadinessResult(assets=asset_state, blank=True)
    return ReadinessResult(assets=asset_state)


async def retry_in_place(
    run: CaptureRun, issues: tuple[AssetIssue, ...] | list[AssetIssue]
) -> tuple[AssetIssue, ...]:
    """Re-request the assets behind *issues* on the same page and return what
    is still broken. Not implemented yet: everything stays broken."""
    return tuple(issues)
