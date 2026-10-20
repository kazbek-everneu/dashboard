"""Capture errors.

Every class derives from ``Exception`` and none from the builtin
``TimeoutError``: the re-capture job catches ``except TimeoutError`` around its
own ``asyncio.wait_for`` and must never see a capture-internal timeout there.
"""

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.screenshots.capture.report import CaptureReport
    from app.screenshots.capture.types import AssetReadinessResult

BLANK_PAGE_MESSAGE = (
    "Page rendered blank after stabilization: no visible text or "
    "image/background assets"
)


class PageStabilizationTimeoutError(Exception):
    """Raised when page readiness heuristics fail to settle before screenshot."""


class PageAssetLoadError(Exception):
    """Raised when visible page assets are still missing after readiness checks.

    *assets* is the asset readiness verdict behind the error, when one exists
    (it does not for a timed-out decode).
    """

    def __init__(
        self, *args: object, assets: "AssetReadinessResult | None" = None
    ) -> None:
        super().__init__(*args)
        self.assets = assets


class BlankPageError(PageAssetLoadError):
    """The page settled with no visible text and no visible image/background
    assets. A PageAssetLoadError, so it is retried and reported like one."""

    def __init__(self, message: str = BLANK_PAGE_MESSAGE) -> None:
        super().__init__(message)


class EvaluateTimeout(Exception):  # noqa: N818 — frozen contract name
    """A page script did not settle within its bound (see jsutil.evaluate)."""


class CaptureDeadlineExceeded(Exception):  # noqa: N818 — frozen contract name
    """A capture attempt ran out of its time budget; *phase* is the report
    phase that was running when it did."""

    def __init__(self, phase: str | None = None, message: str | None = None) -> None:
        self.phase = phase
        super().__init__(
            message or f"Capture attempt ran out of time (phase: {phase or 'unknown'})"
        )


class NavigationTimeoutError(Exception):
    """page.goto timed out on the lightest wait condition."""


class NavigationError(Exception):
    """Navigation failed for a network reason (DNS, refused, certificate)."""

    def __init__(
        self, net_error: str | None = None, message: str | None = None
    ) -> None:
        self.net_error = net_error
        super().__init__(
            message or f"Navigation failed: {net_error or 'unknown network error'}"
        )


class PageBlockedError(Exception):
    """A protection page (WAF / bot challenge) kept the real page from loading."""

    def __init__(
        self,
        vendor: str | None = None,
        status: int | None = None,
        message: str | None = None,
    ) -> None:
        self.vendor = vendor
        self.status = status
        if message is None:
            message = f"Page blocked by {vendor or 'a protection page'}"
            if status:
                message += f" (HTTP {status})"
        super().__init__(message)


class ScreenshotTimeoutError(Exception):
    """page.screenshot timed out."""


class BrowserUnavailableError(Exception):
    """The browser connection is gone and could not be re-established."""


class AttemptFailed(Exception):  # noqa: N818 — frozen contract name
    """One capture attempt failed.

    Carries the original *error* and the attempt's (partial) *report*, so the
    retry loop keeps the timings / warnings collected up to the failure.
    """

    def __init__(self, error: BaseException, report: "CaptureReport") -> None:
        super().__init__(str(error))
        self.error = error
        self.report = report
