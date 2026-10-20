import math
from unittest.mock import patch

import pytest

from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import (
    AssetReadinessResult,
    CaptureOutcome,
    CaptureResult,
    Deadline,
    NavigationResult,
    ResourceRecord,
)


def test_infinite_deadline_never_expires_and_never_reads_the_clock() -> None:
    with patch(
        "app.screenshots.capture.clock.now",
        side_effect=AssertionError("read the clock"),
    ):
        deadline = Deadline(budget_s=math.inf, started_at=0.0)

        assert deadline.remaining_s() == math.inf
        assert deadline.expired() is False


def test_finite_deadline_counts_down_on_the_capture_clock() -> None:
    now = [100.0]
    with patch("app.screenshots.capture.clock.now", side_effect=lambda: now[0]):
        deadline = Deadline.start(30.0)
        now[0] += 10

        assert deadline.started_at == 100.0
        assert deadline.remaining_s() == 20.0
        assert deadline.expired() is False

        now[0] += 25
        assert deadline.expired() is True


@pytest.mark.parametrize(
    ("record", "blocked"),
    [
        (ResourceRecord("https://a/x.jpg", "image", status=403), True),
        (ResourceRecord("https://a/x.jpg", "image", status=401), True),
        (ResourceRecord("https://a/x.css", "stylesheet", status=429), True),
        (ResourceRecord("https://a/x.css", "stylesheet", status=503), True),
        (ResourceRecord("https://a/x.jpg", "image", status=404), False),
        (ResourceRecord("https://a/x.jpg", "image", status=410), False),
        (ResourceRecord("https://a/x.jpg", "image", status=500), False),
        (ResourceRecord("https://a/x.jpg", "image", status=200), False),
        # A WAF answering with an HTML page instead of the real file.
        (
            ResourceRecord(
                "https://a/x.css",
                "stylesheet",
                status=200,
                content_type="text/html; charset=UTF-8",
            ),
            True,
        ),
        (ResourceRecord("https://a/x.png", "image", content_type="text/html"), True),
        (ResourceRecord("https://a/x.woff2", "font", content_type="text/html"), True),
        (ResourceRecord("https://a/x.js", "script", content_type="text/html"), False),
        (ResourceRecord("https://a/", "document", content_type="text/html"), False),
        (
            ResourceRecord("https://a/x.css", "stylesheet", content_type="text/css"),
            False,
        ),
        (ResourceRecord("https://a/x.jpg", "image", failure="net::ERR_ABORTED"), True),
        (
            ResourceRecord(
                "https://a/x.jpg", "image", failure="net::ERR_BLOCKED_BY_CLIENT"
            ),
            True,
        ),
        (
            ResourceRecord(
                "https://a/x.jpg", "image", failure="net::ERR_CONNECTION_RESET"
            ),
            False,
        ),
        (
            ResourceRecord("https://a/x.jpg", "image", failure="net::ERR_TIMED_OUT"),
            False,
        ),
        (ResourceRecord("https://a/x.css", "stylesheet", refused=True), True),
        (ResourceRecord("https://a/x.jpg"), False),
    ],
)
def test_resource_record_blocked_like(record: ResourceRecord, blocked: bool) -> None:
    assert record.blocked_like() is blocked


def test_navigation_result_allows_interactive_only_without_load_event() -> None:
    loaded = NavigationResult("https://a/", "https://a/", 200, True, "load")
    dom_only = NavigationResult(
        "https://a/", "https://a/", 200, False, "domcontentloaded"
    )

    assert loaded.allow_interactive is False
    assert dom_only.allow_interactive is True
    assert (dom_only.challenge, dom_only.reloads) == (None, 0)


def test_asset_readiness_result_totals() -> None:
    result = AssetReadinessResult(
        visible_image_count=3,
        visible_background_count=2,
        missing_visible_image_count=1,
        missing_background_image_count=1,
    )

    assert result.missing_count == 2
    assert result.visible_asset_count == 5


def test_capture_outcome_gets_its_own_report() -> None:
    first = CaptureOutcome(data=b"a", duration=1.0)
    second = CaptureOutcome(data=b"b", duration=1.0)

    assert isinstance(first.report, CaptureReport)
    assert first.report is not second.report
    assert first.captured_with_unstable_dom is False
    assert first.warning_message is None


def test_capture_result_ok() -> None:
    outcome = CaptureOutcome(data=b"a", duration=1.0)

    assert CaptureResult(outcome, None, outcome.report, 1, 1.0).ok is True
    assert CaptureResult(None, ValueError("x"), CaptureReport(), 2, 3.0).ok is False
