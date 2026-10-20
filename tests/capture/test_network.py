from unittest.mock import patch

from app.screenshots.capture import network
from app.screenshots.capture.network import InflightTracker, NetworkDiagnostics
from tests.capture.fakes import FakePage, FakeRequest, FakeResponse


def test_inflight_tracker_counts_only_tracked_request_types() -> None:
    tracker = InflightTracker()
    image = FakeRequest(resource_type="image")
    xhr = FakeRequest(resource_type="xhr")
    video = FakeRequest(resource_type="media")
    for request in (image, xhr, video):
        tracker.on_request(request)

    assert tracker.inflight() == 1
    assert tracker.inflight(include_data=True) == 2

    tracker.on_request_done(FakeRequest())  # never seen: no-op
    assert tracker.inflight(include_data=True) == 2

    tracker.on_request_done(image)
    tracker.on_request_done(xhr)
    assert tracker.inflight(include_data=True) == 0


def test_inflight_tracker_counts_started_requests() -> None:
    tracker = InflightTracker()
    image = FakeRequest(resource_type="image")
    tracker.on_request(image)
    tracker.on_request(FakeRequest(resource_type="fetch"))
    tracker.on_request(FakeRequest(resource_type="media"))  # not tracked
    tracker.on_request_done(image)

    assert tracker.started_count == 2


def test_inflight_tracker_ignores_stale_requests() -> None:
    now = [100.0]
    with patch("app.screenshots.capture.clock.now", side_effect=lambda: now[0]):
        tracker = InflightTracker()
        tracker.on_request(FakeRequest(resource_type="image"))
        tracker.on_request(FakeRequest(resource_type="fetch"))
        assert tracker.inflight(include_data=True) == 2

        now[0] += 5  # past the 3 s data limit, within the 10 s visual one
        assert tracker.inflight(include_data=True) == 1

        now[0] += 6
        assert tracker.inflight(include_data=True) == 0


def test_inflight_tracker_listens_to_page_events() -> None:
    page = FakePage()
    tracker = InflightTracker()
    tracker.attach(page)
    request = FakeRequest(resource_type="stylesheet")

    page.emit("request", request)
    assert tracker.inflight() == 1
    page.emit("requestfinished", request)
    assert tracker.inflight() == 0

    page.emit("request", request)
    page.emit("requestfailed", request)
    assert tracker.inflight() == 0


async def test_wait_for_quiet_ends_once_requests_finish() -> None:
    page = FakePage()
    tracker = InflightTracker()
    request = FakeRequest(resource_type="image")
    tracker.on_request(request)
    page.on_wait = lambda polls: polls == 5 and tracker.on_request_done(request)

    await network.wait_for_quiet(page, tracker, min_ms=300, quiet_ms=200, max_ms=2000)

    # Busy for 5 polls (500 ms), then quiet for 200 ms — well under the cap.
    assert sum(page.waits) == 700
    assert all(wait <= 100 for wait in page.waits)


async def test_wait_for_quiet_stops_at_cap_when_request_never_finishes() -> None:
    page = FakePage()
    tracker = InflightTracker()
    tracker.on_request(FakeRequest(resource_type="font"))

    await network.wait_for_quiet(page, tracker, min_ms=300, quiet_ms=200, max_ms=1000)

    assert sum(page.waits) == 1000


async def test_wait_for_quiet_honours_min_wait_when_idle() -> None:
    page = FakePage()

    await network.wait_for_quiet(
        page, InflightTracker(), min_ms=300, quiet_ms=200, max_ms=2000
    )

    assert sum(page.waits) == 300


async def test_wait_for_quiet_without_tracker_keeps_fixed_pause() -> None:
    page = FakePage()

    await network.wait_for_quiet(page, None, min_ms=300, quiet_ms=200, max_ms=2000)

    assert page.waits == [2000]


async def test_wait_for_quiet_counts_data_requests_only_when_asked() -> None:
    tracker = InflightTracker()
    tracker.on_request(FakeRequest(resource_type="fetch"))

    ignoring = FakePage()
    await network.wait_for_quiet(ignoring, tracker, min_ms=0, quiet_ms=200, max_ms=1000)
    including = FakePage()
    await network.wait_for_quiet(
        including, tracker, min_ms=0, quiet_ms=200, max_ms=1000, include_data=True
    )

    assert sum(ignoring.waits) == 200
    assert sum(including.waits) == 1000


def test_network_diagnostics_records_asset_failures_only() -> None:
    diagnostics = NetworkDiagnostics()

    diagnostics.record_failed_request(
        FakeRequest(
            resource_type="image",
            url="https://cdn.example.com/missing.jpg",
            failure="net::ERR_CONNECTION_RESET",
        )
    )
    diagnostics.record_failed_request(FakeRequest(resource_type="script"))
    diagnostics.record_response(
        FakeResponse(
            status=403,
            url="https://cdn.example.com/blocked.jpg",
            resource_type="image",
        )
    )
    diagnostics.record_response(FakeResponse(status=404, resource_type="document"))
    diagnostics.record_response(FakeResponse(status=200, resource_type="image"))

    assert diagnostics.summary() == (
        "Network diagnostics: failed requests: image net::ERR_CONNECTION_RESET "
        "https://cdn.example.com/missing.jpg | bad responses: image HTTP 403 "
        "https://cdn.example.com/blocked.jpg"
    )


def test_network_diagnostics_caps_entries() -> None:
    diagnostics = NetworkDiagnostics()
    for index in range(20):
        diagnostics.record_failed_request(
            FakeRequest(url=f"https://cdn.example.com/{index}.jpg")
        )

    assert len(diagnostics.failed_requests) == 8


def test_append_network_diagnostics() -> None:
    empty = NetworkDiagnostics()
    diagnostics = NetworkDiagnostics(failed_requests=["image net::ERR_FAILED x"])

    assert network.append_network_diagnostics("boom", empty) == "boom"
    assert network.append_network_diagnostics("boom", diagnostics) == (
        "boom; Network diagnostics: failed requests: image net::ERR_FAILED x"
    )
