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
# Fixed pauses right before the screenshot (stable / unstable capture).
FINAL_SETTLE_MS = 500
UNSTABLE_SETTLE_MS = 300
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
