"""Resolved capture parameters and every capture tuning constant.

Other capture modules import this module as ``cfg``
(``from app.screenshots.capture import config as cfg``; ``config`` is the name
of the EffectiveConfig parameter everywhere) and read the constants as
``cfg.NAME`` at call time, so a test can patch
``app.screenshots.capture.config.NAME``. Thresholds live here — never in env
or DB settings.
"""

from dataclasses import dataclass

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


# ---------------------------------------------------------------------------
# Attempts
# ---------------------------------------------------------------------------

RETRY_COUNT = 1
RETRY_DELAY_S = 2.0  # seconds between attempts

# ---------------------------------------------------------------------------
# Readiness
# ---------------------------------------------------------------------------

STABILITY_SAMPLE_MS = 500
STABILITY_REQUIRED_CYCLES = 3
MIN_SETTLE_TIMEOUT_MS = 8000
MAX_SETTLE_TIMEOUT_MS = 25000
RETRY_SETTLE_MULTIPLIER = 1.5
# The retry window has its own, higher cap: with the default 30 s project
# timeout the base window is already clamped to MAX_SETTLE_TIMEOUT_MS, so
# capping the retry at the same value made the multiplier a no-op.
MAX_RETRY_SETTLE_TIMEOUT_MS = 37500
# Caps of the self-bounded page waits within a readiness window.
FONTS_WAIT_CAP_MS = 5000
DECODE_WAIT_CAP_MS = 12000
# Extra time the outer asyncio safety net allows on top of a timeout the page
# script already enforces itself.
JS_TIMEOUT_GRACE_MS = 2000

# ---------------------------------------------------------------------------
# Scroll / settle
# ---------------------------------------------------------------------------

# The fixed pauses below only exist to *trigger* lazy loads as we scroll; the
# actual wait-for-readiness is handled adaptively by the readiness stage
# (decode_visible_images / wait_for_dom_stability). So pages with no lazy
# content can use the fast profile and finish in a fraction of the time, while
# lazy-heavy pages keep the generous "slow" pauses they need.
SCROLL_PAUSE_LAZY_PASS1_MS = 2000  # slow lazy page, first (trigger) pass
SCROLL_PAUSE_LAZY_PASS2_MS = 1000  # slow lazy page, second (render) pass
SCROLL_PAUSE_FAST_PASS1_MS = 500  # no lazy content, first pass
SCROLL_PAUSE_FAST_PASS2_MS = 300  # no lazy content, second pass
SETTLE_PROBE_LAZY_MS = 2000  # interlude/bottom settle when lazy content present
SETTLE_PROBE_FAST_MS = 500  # interlude/bottom settle when no lazy content
NETWORKIDLE_PROBE_LAZY_MS = 5000  # non-fatal networkidle probe, lazy pages
NETWORKIDLE_PROBE_FAST_MS = 1500  # non-fatal networkidle probe, no lazy content
POST_TRIGGER_WAIT_LAZY_MS = 500  # pause after firing lazy triggers, lazy pages
POST_TRIGGER_WAIT_FAST_MS = 150  # pause after firing lazy triggers, no lazy

# Adaptive waits. The constants above are the *caps* of each wait; within a
# cap the wait ends as soon as no tracked request has been in flight for the
# quiet period (see network.wait_for_quiet). So no wait is ever longer than
# before — it just stops idling once the page has actually finished loading.
MAX_SCROLL_STEPS = 200  # guard against infinite-scroll pages
QUIET_POLL_MS = 100
STEP_MIN_LAZY_PASS1_MS = 300  # covers throttled scroll-listener lazy loaders
STEP_MIN_FAST_PASS1_MS = 150
STEP_MIN_PASS2_MS = 100
STEP_QUIET_MS = 200
STEP_QUIET_PASS2_MS = 100
SETTLE_MIN_MS = 300
SETTLE_QUIET_MS = 500
POST_GOTO_QUIET_MS = 500
POST_GOTO_QUIET_CAP_MS = 5000

# ---------------------------------------------------------------------------
# Network tracking
# ---------------------------------------------------------------------------

# Request types that change what the screenshot shows. media is excluded on
# purpose: video/audio stream via range requests that never go quiet. xhr/fetch
# only count right after navigation — analytics and long-polling would
# otherwise pin every later wait at its cap.
TRACKED_VISUAL_TYPES = frozenset({"image", "font", "stylesheet", "script", "document"})
TRACKED_DATA_TYPES = frozenset({"xhr", "fetch"})
# Requests in flight longer than this are treated as background (long-poll,
# streaming, an occasional missing requestfinished) and stop blocking a wait.
INFLIGHT_STALE_MS = 10000
INFLIGHT_STALE_DATA_MS = 3000


def stabilization_timeout_ms(config: EffectiveConfig, retry: bool = False) -> int:
    """Readiness window for one attempt: the project timeout clamped to
    [MIN_SETTLE_TIMEOUT_MS, MAX_SETTLE_TIMEOUT_MS]; the retry / second-chance
    window is RETRY_SETTLE_MULTIPLIER times longer, up to its own cap."""
    base = max(MIN_SETTLE_TIMEOUT_MS, min(config.timeout, MAX_SETTLE_TIMEOUT_MS))
    if retry:
        return min(int(base * RETRY_SETTLE_MULTIPLIER), MAX_RETRY_SETTLE_TIMEOUT_MS)
    return base


# --- B1: device profiles ---
# Read by profiles.build_profile; see the user-agent table there.

# Locale of every capture context: navigator.language(s) and Accept-Language,
# the same on all engines (left alone, each engine sends its own header and
# Firefox reports navigator.languages = ["undefined"]).
PROFILE_LOCALE = "en-US"
# OS tokens of the emulated user agents (Chrome's reduced Android UA always
# says "Android 10; K", so it has no setting).
FIREFOX_ANDROID_VERSION = "14"  # "Android 14; Mobile|Tablet"
IOS_VERSION = "18_5"  # "CPU iPhone OS 18_5" / "CPU OS 18_5"
# What the profile init script makes navigator report on emulated Android
# devices, and the touch points of an emulated touch device whose engine
# reports none (Firefox and WebKit report 0 even with has_touch).
ANDROID_NAVIGATOR_PLATFORM = "Linux armv8l"
EMULATED_TOUCH_POINTS = 5


# --- B2: navigation & network ---

# Navigation is one goto that waits for DOMContentLoaded; the load event is
# then awaited separately and non-fatally, up to this cap (and the project
# timeout). A page whose load event never fires is captured anyway — readiness
# then accepts document.readyState "interactive".
LOAD_WAIT_CAP_MS = 30_000
# Post-goto quiet of wait_until="networkidle" projects, replacing Playwright's
# networkidle (which analytics beacons and long-polling keep from ever firing).
NETWORKIDLE_QUIET_MS = 1000
NETWORKIDLE_QUIET_CAP_MS = 15_000
# Bound of every navigation page script (jsutil.evaluate), and of the wait for
# a new document's DOMContentLoaded before a page check is retried after a
# navigation replaced the document under it.
NAVIGATION_SCRIPT_TIMEOUT_MS = 15_000
NAVIGATION_RECHECK_WAIT_MS = 5000
# Protection pages (WAF / bot challenge): how long a detected one may take to
# pass, and the polling interval while it does.
CHALLENGE_WAIT_MS = 15_000
CHALLENGE_POLL_MS = 500
# Page markers of a protection page only count on a page this small (element
# count): such pages are tiny, so a long article that merely mentions
# "Access Denied" or a vendor name is never mistaken for one.
CHALLENGE_PAGE_MAX_ELEMENTS = 400
# Bound of NetworkDiagnostics' per-URL resource registry.
MAX_RESOURCE_RECORDS = 2000


# --- B3: page preparation ---

# Bound of every prepare page script (jsutil.evaluate raises EvaluateTimeout).
PREPARE_SCRIPT_TIMEOUT_MS = 15_000
# Font wait before scrolling; readiness waits for fonts again with its own cap.
PREPARE_FONTS_WAIT_CAP_MS = 3000
# Adaptive pause after firing the lazy triggers (capped at
# POST_TRIGGER_WAIT_LAZY_MS / POST_TRIGGER_WAIT_FAST_MS).
POST_TRIGGER_MIN_MS = 100
POST_TRIGGER_QUIET_MS = 150
# Scroll steps: pass 1 = half the viewport height (at least SCROLL_STEP_MIN_PX),
# pass 2 = PASS2_STEP_RATIO * viewport height.
SCROLL_STEP_MIN_PX = 300
PASS2_STEP_RATIO = 0.9
# Pass 2 runs only when prepare.should_run_pass2 sees a reason; FORCE_PASS2
# always runs it (bench A/B gate: conditional vs forced second pass).
FORCE_PASS2 = False
PASS2_MIN_HEIGHT_GROWTH_PX = 50
# Infinite-scroll guard: stop scrolling after INFINITE_SCROLL_GROWTHS growths in
# a row of at least INFINITE_SCROLL_GROWTH_RATIO * viewport height while waiting
# at the bottom, or once the page is taller than INFINITE_SCROLL_MAX_HEIGHT_PX
# (3 * 32767).
INFINITE_SCROLL_GROWTHS = 3
INFINITE_SCROLL_GROWTH_RATIO = 0.5
INFINITE_SCROLL_MAX_HEIGHT_PX = 98_301
# Hidden-overlay records kept in capture_meta["overlays"]["samples"].
OVERLAY_SAMPLES = 10


# --- B4: readiness ---

# readiness.finalize waits for the fonts at most FONTS_WAIT_CAP_MS (above); a
# timeout is only the fonts_timeout warning, naming at most this many
# families.
FONTS_TIMEOUT_MAX_FAMILIES = 10
# Broken / still loading visible assets (img, CSS background) one verdict
# turns into AssetIssues; same cap for stylesheet and font issues.
MAX_ASSET_ISSUES = 20
# Bound of every readiness page script that does not bound itself.
READINESS_SCRIPT_TIMEOUT_MS = 15_000
# Fast path of wait_for_dom_stability: stable at once when the page is ready,
# the in-page activity tracker has seen no DOM mutation / root resize for this
# long and nothing tracked is in flight. It only ends the wait earlier — the
# three-equal-samples rule stays the only way to an "unstable" verdict.
FAST_PATH_QUIET_MS = 1500
# readiness.retry_in_place: wait for the re-requested assets and the in-flight
# requests at most RETRY_WAIT_MS (polling every RETRY_POLL_MS), but only
# RETRY_IDLE_MS while elements still wait with nothing on the wire (the engine
# did not fetch again); then re-check with a RETRY_DECODE_MS decode.
RETRY_WAIT_MS = 5000
RETRY_POLL_MS = 200
RETRY_IDLE_MS = 1000
RETRY_DECODE_MS = 2500
# requestfailed texts (Chromium, Firefox, WebKit) that make a broken asset a
# "network" issue; case-insensitive substrings.
NETWORK_FAILURE_MARKERS = (
    "ERR_CONNECTION_",
    "ERR_TIMED_OUT",
    "ERR_NETWORK_",
    "ERR_INTERNET_DISCONNECTED",
    "ERR_EMPTY_RESPONSE",
    "ERR_HTTP2_",
    "ERR_QUIC_",
    "NS_ERROR_NET_",
    "NS_ERROR_CONNECTION_REFUSED",
    "Could not connect",
    "Connection terminated",
    "network connection was lost",
    "timed out",
)


# --- B5: pipeline & snapshot ---

# Page scripts (jsutil.evaluate): the default bound of one script, and how long
# a script whose document was replaced under it (a late client-side redirect)
# waits for the new document's DOMContentLoaded before its one re-run.
EVALUATE_TIMEOUT_MS = 15_000
CONTEXT_DESTROYED_WAIT_MS = 5000

# Attempt budget: clamp(ATTEMPT_BUDGET_PER_TIMEOUT x project timeout +
# ATTEMPT_BUDGET_BASE_S, ATTEMPT_BUDGET_MIN_S, ATTEMPT_BUDGET_MAX_S) seconds,
# never past the caller's deadline_at. Past the budget an attempt fails with
# CaptureDeadlineExceeded: its page/context is closed under it (bounded by
# CLOSE_TIMEOUT_S) and the attempt gets as long again to unwind before it is
# cancelled; a cancelled capture gets CANCEL_SETTLE_S for that.
ATTEMPT_BUDGET_PER_TIMEOUT = 4
ATTEMPT_BUDGET_BASE_S = 90
ATTEMPT_BUDGET_MIN_S = 240
ATTEMPT_BUDGET_MAX_S = 480
CLOSE_TIMEOUT_S = 10
CANCEL_SETTLE_S = 2
# A retry attempt only starts with at least this much left of deadline_at.
RETRY_MIN_REMAINING_S = 60

# Optional steps run only when the attempt budget has room for them, else
# they are skipped with a recovery_skipped info warning:
# - the in-place second readiness chance needs its window + this spare;
SECOND_CHANCE_SPARE_S = 10
# - a recovery reload needs this x the time already spent on navigation +
#   prepare + readiness (and keeps RELOAD_RESERVE_S of the budget to fall
#   back to the snapshot taken before it);
RELOAD_COST_FACTOR = 1.5
RELOAD_RESERVE_S = 5
# - an in-place screenshot retry needs this much.
SCREENSHOT_RETRY_MIN_S = 30

# page.screenshot timeout: max(project timeout, SCREENSHOT_MIN_TIMEOUT_MS,
# SCREENSHOT_MS_PER_MEGAPIXEL x document megapixels), capped by the budget.
# After a timeout the screenshot is retried once after this pause.
SCREENSHOT_MIN_TIMEOUT_MS = 90_000
SCREENSHOT_MS_PER_MEGAPIXEL = 3_000
SCREENSHOT_RETRY_PAUSE_MS = 1000
# Content wider than the layout viewport by more than this is cropped to it.
OVERFLOW_TOLERANCE_PX = 1
