"""Screenshot capture pipeline.

Stages receive a CaptureRun and return a dataclass result:
navigation.navigate → prepare.prepare_page → prepare.reapply_transforms +
readiness.finalize → prepare.final_touches → snapshot.take_snapshot, strung
together by pipeline.run_attempt / pipeline.capture_with_retries. Persistence
lives in app.screenshots.service.

Patch a function in tests through its defining module (e.g.
``app.screenshots.capture.readiness.finalize``), never through the re-exports
below: production code calls across modules via the module object.
"""

import os

from app.screenshots.capture.config import EffectiveConfig, stabilization_timeout_ms
from app.screenshots.capture.errors import (
    AttemptFailed,
    BlankPageError,
    BrowserUnavailableError,
    CaptureDeadlineExceeded,
    EvaluateTimeout,
    NavigationError,
    NavigationTimeoutError,
    PageAssetLoadError,
    PageBlockedError,
    PageStabilizationTimeoutError,
    ScreenshotTimeoutError,
)
from app.screenshots.capture.pipeline import capture_with_retries, run_attempt
from app.screenshots.capture.profiles import (
    DeviceProfile,
    DeviceType,
    build_profile,
    open_capture_context,
    resolve_device_type,
)
from app.screenshots.capture.report import WARNING_CODES, CaptureReport, CaptureWarning
from app.screenshots.capture.types import (
    AssetIssue,
    AssetReadinessResult,
    CaptureOutcome,
    CaptureResult,
    CaptureRun,
    Deadline,
    NavigationResult,
    PrepareResult,
    ReadinessResult,
    ResourceRecord,
    SnapshotResult,
    Viewport,
)

# Playwright waits for document.fonts.ready inside page.screenshot(). That wait
# is unbounded except by the screenshot action timeout and is flaky in WebKit on
# pages with problematic web fonts. We already do a bounded font readiness probe
# before taking the screenshot, so do not let the final screenshot block on it.
# Set at import time — before a locally launched Playwright driver starts and
# inherits the environment (a remote browser server sets it itself).
os.environ.setdefault("PW_TEST_SCREENSHOT_NO_FONTS_READY", "1")

__all__ = [
    "WARNING_CODES",
    "AssetIssue",
    "AssetReadinessResult",
    "AttemptFailed",
    "BlankPageError",
    "BrowserUnavailableError",
    "CaptureDeadlineExceeded",
    "CaptureOutcome",
    "CaptureReport",
    "CaptureResult",
    "CaptureRun",
    "CaptureWarning",
    "Deadline",
    "DeviceProfile",
    "DeviceType",
    "EffectiveConfig",
    "EvaluateTimeout",
    "NavigationError",
    "NavigationResult",
    "NavigationTimeoutError",
    "PageAssetLoadError",
    "PageBlockedError",
    "PageStabilizationTimeoutError",
    "PrepareResult",
    "ReadinessResult",
    "ResourceRecord",
    "ScreenshotTimeoutError",
    "SnapshotResult",
    "Viewport",
    "build_profile",
    "capture_with_retries",
    "open_capture_context",
    "resolve_device_type",
    "run_attempt",
    "stabilization_timeout_ms",
]
