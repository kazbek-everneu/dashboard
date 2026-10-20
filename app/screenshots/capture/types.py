"""Stage contracts: the run state every stage receives and the dataclass
results the stages return.

Workstreams only add optional fields to the result class they own; existing
fields and their meaning are frozen.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal, TypedDict

from app.screenshots.capture import clock
from app.screenshots.capture.report import CaptureReport

if TYPE_CHECKING:
    from playwright.async_api import Page

    from app.screenshots.capture.config import EffectiveConfig
    from app.screenshots.capture.network import InflightTracker, NetworkDiagnostics
    from app.screenshots.capture.profiles import DeviceProfile


class Viewport(TypedDict):
    width: int
    height: int


@dataclass
class Deadline:
    """Time budget of one capture attempt, measured on ``clock.now()``.

    An infinite budget never expires and never reads the clock.
    """

    budget_s: float = math.inf
    started_at: float = 0.0

    @classmethod
    def start(cls, budget_s: float = math.inf) -> Deadline:
        return cls(budget_s=budget_s, started_at=clock.now())

    def remaining_s(self) -> float:
        if math.isinf(self.budget_s):
            return self.budget_s
        return self.budget_s - (clock.now() - self.started_at)

    def expired(self) -> bool:
        return self.remaining_s() <= 0


# ---------------------------------------------------------------------------
# Network
# ---------------------------------------------------------------------------

_BLOCKED_STATUSES = frozenset({401, 403, 429, 503})
# Resource types a WAF answers with an HTML page instead of the real file.
_HTML_SENSITIVE_TYPES = frozenset({"stylesheet", "image", "font"})
_BLOCKED_FAILURES = ("ERR_ABORTED", "ERR_BLOCKED_")


@dataclass
class ResourceRecord:
    """What the network told us about one subresource URL."""

    url: str
    resource_type: str = ""
    status: int | None = None
    content_type: str | None = None
    failure: str | None = None  # requestfailed text, e.g. "net::ERR_ABORTED"
    refused: bool = False  # the console reported the engine refused it
    finished: bool = False

    def blocked_like(self) -> bool:
        """True when the response looks like a block rather than a plain miss:
        401/403/429/503, an HTML body where a stylesheet/image/font belongs,
        an aborted/blocked request, or a resource the engine refused."""
        if self.refused:
            return True
        if self.status in _BLOCKED_STATUSES:
            return True
        failure = (self.failure or "").upper()
        if any(marker in failure for marker in _BLOCKED_FAILURES):
            return True
        mime = (self.content_type or "").split(";", 1)[0].strip().lower()
        return self.resource_type in _HTML_SENSITIVE_TYPES and mime == "text/html"


# ---------------------------------------------------------------------------
# Stage results
# ---------------------------------------------------------------------------


@dataclass
class NavigationResult:
    requested_url: str
    final_url: str
    status: int | None = None  # main document HTTP status, None when unknown
    load_event_fired: bool = True
    wait_strategy: str = "load"  # the wait_until level the goto succeeded with
    challenge: str | None = None  # protection-page vendor that was passed
    reloads: int = 0

    @property
    def allow_interactive(self) -> bool:
        """Readiness may accept document.readyState == "interactive" when the
        load event was not awaited."""
        return not self.load_event_fired


@dataclass
class PrepareResult:
    has_lazy_content: bool = False
    scroll_steps: int = 0
    pass2_ran: bool = False
    document_height: int = 0
    inner_scroller: bool = False
    growth_stopped: bool = False
    overlays_hidden: int = 0
    carousels_paused: int = 0


AssetKind = Literal["img", "background", "stylesheet", "font"]
AssetCategory = Literal["blocked", "missing", "network", "pending", "unknown"]


@dataclass(frozen=True)
class AssetIssue:
    kind: AssetKind
    url: str
    category: AssetCategory = "unknown"
    status: int | None = None
    reason: str | None = None


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
class ReadinessResult:
    assets: AssetReadinessResult | None = None
    issues: tuple[AssetIssue, ...] = ()
    fonts_ready: bool = True
    stable: bool = True
    unstable_reason: str | None = None  # "fonts" | "dom" when not stable
    blank: bool = False
    fast_path: bool = False


@dataclass
class SnapshotResult:
    data: bytes
    document_width: int = 0
    document_height: int = 0
    clip_width: int | None = None
    retried: bool = False


@dataclass
class CaptureOutcome:
    """A successful attempt. *duration* is page.goto → page.screenshot."""

    data: bytes
    duration: float
    report: CaptureReport = field(default_factory=CaptureReport)
    captured_with_unstable_dom: bool = False
    # Kept for compatibility: the text ok rows store in error_message.
    warning_message: str | None = None


@dataclass
class CaptureResult:
    """Result of all attempts for one capture.

    *duration* is what the row stores: the successful attempt's goto →
    screenshot time, or for a failure the wall time of all attempts (retry
    sleep excluded). *report* is the last attempt's report.
    """

    outcome: CaptureOutcome | None
    error: BaseException | None
    report: CaptureReport
    attempts: int
    duration: float

    @property
    def ok(self) -> bool:
        return self.error is None and self.outcome is not None


# ---------------------------------------------------------------------------
# Run state
# ---------------------------------------------------------------------------


@dataclass
class CaptureRun:
    """State of one capture attempt on one page, shared by all stages."""

    url: str
    viewport: Viewport
    config: EffectiveConfig
    profile: DeviceProfile
    page: Page
    tracker: InflightTracker | None
    net: NetworkDiagnostics
    report: CaptureReport
    deadline: Deadline
    attempt: int = 0
    nav: NavigationResult | None = None
    prep: PrepareResult | None = None
    scratch: dict[str, Any] = field(default_factory=dict)
