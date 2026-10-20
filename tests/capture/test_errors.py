import pytest

from app.screenshots.capture import errors
from app.screenshots.capture.report import CaptureReport

ALL_ERRORS = [
    errors.PageStabilizationTimeoutError,
    errors.PageAssetLoadError,
    errors.BlankPageError,
    errors.EvaluateTimeout,
    errors.CaptureDeadlineExceeded,
    errors.NavigationTimeoutError,
    errors.NavigationError,
    errors.PageBlockedError,
    errors.ScreenshotTimeoutError,
    errors.BrowserUnavailableError,
    errors.AttemptFailed,
]


@pytest.mark.parametrize("error_type", ALL_ERRORS)
def test_capture_errors_are_never_builtin_timeouts(error_type: type) -> None:
    # The re-capture job catches the builtin TimeoutError of its own wait_for.
    assert issubclass(error_type, Exception)
    assert not issubclass(error_type, TimeoutError)


def test_blank_page_error_keeps_the_old_message_and_asset_semantics() -> None:
    error = errors.BlankPageError()

    assert isinstance(error, errors.PageAssetLoadError)
    assert str(error) == (
        "Page rendered blank after stabilization: no visible text or "
        "image/background assets"
    )


def test_page_asset_load_error_keeps_the_plain_exception_shape() -> None:
    error = errors.PageAssetLoadError("Visible page assets did not load: 1 visible img")

    assert str(error) == "Visible page assets did not load: 1 visible img"
    assert error.assets is None


def test_typed_errors_carry_their_details() -> None:
    deadline = errors.CaptureDeadlineExceeded("readiness")
    navigation = errors.NavigationError("net::ERR_NAME_NOT_RESOLVED")
    blocked = errors.PageBlockedError("cloudflare", 403)

    assert deadline.phase == "readiness" and "readiness" in str(deadline)
    assert navigation.net_error == "net::ERR_NAME_NOT_RESOLVED"
    assert "net::ERR_NAME_NOT_RESOLVED" in str(navigation)
    assert (blocked.vendor, blocked.status) == ("cloudflare", 403)
    assert "cloudflare" in str(blocked) and "HTTP 403" in str(blocked)


def test_attempt_failed_wraps_the_error_and_the_report() -> None:
    report = CaptureReport()
    original = ValueError("boom")

    failed = errors.AttemptFailed(original, report)

    assert failed.error is original
    assert failed.report is report
    assert str(failed) == "boom"
