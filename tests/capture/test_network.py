from types import SimpleNamespace
from unittest.mock import patch

import pytest
from playwright.async_api import Error as PlaywrightError

from app.screenshots.capture import network
from app.screenshots.capture.network import InflightTracker, NetworkDiagnostics
from app.screenshots.capture.types import ResourceRecord
from tests.capture.fakes import (
    FakeConsoleMessage,
    FakePage,
    FakeRequest,
    FakeResponse,
)


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


# ---------------------------------------------------------------------------
# Resource registry
# ---------------------------------------------------------------------------

CSS = "https://cdn.example.com/site.css"


def _response(
    url: str = CSS,
    *,
    status: int = 200,
    resource_type: str = "stylesheet",
    content_type: str | None = "text/css",
    frame: object = None,
    redirected_from: object = None,
) -> FakeResponse:
    headers = {"Content-Type": content_type} if content_type else {}
    response = FakeResponse(
        status=status, url=url, headers=headers, resource_type=resource_type
    )
    if frame is not None:
        response.request.frame = frame
    if redirected_from is not None:
        response.request.redirected_from = redirected_from
    return response


def _attached() -> tuple[FakePage, NetworkDiagnostics]:
    page = FakePage()
    diagnostics = NetworkDiagnostics()
    diagnostics.attach(page)
    return page, diagnostics


class _FramelessRequest(FakeRequest):
    @property
    def frame(self) -> object:
        raise PlaywrightError(
            "Service Worker requests do not have an associated frame."
        )


def test_attach_subscribes_the_page_events() -> None:
    page, diagnostics = _attached()

    assert page.handlers == {
        "requestfailed": [diagnostics.record_failed_request],
        "response": [diagnostics.record_response],
        "requestfinished": [diagnostics.record_request_finished],
        "console": [diagnostics.record_console],
    }


def test_registry_records_what_the_network_saw() -> None:
    page, diagnostics = _attached()
    response = _response()

    page.emit("response", response)
    assert diagnostics.lookup(CSS) == ResourceRecord(
        url=CSS, resource_type="stylesheet", status=200, content_type="text/css"
    )

    page.emit("requestfinished", response.request)
    record = diagnostics.lookup(CSS)
    assert record is not None and record.finished is True
    assert diagnostics.problem_resources() == []


def test_registry_drops_the_fragment() -> None:
    page, diagnostics = _attached()

    page.emit("response", _response(CSS + "#v2"))

    assert diagnostics.lookup(CSS) is diagnostics.lookup(CSS + "#other")
    assert diagnostics.lookup(CSS) is not None
    assert list(diagnostics.resources) == [CSS]


def test_registry_skips_inline_urls_and_unknown_lookups() -> None:
    page, diagnostics = _attached()

    page.emit("response", _response("data:text/css,body{}"))

    assert diagnostics.resources == {}
    assert diagnostics.lookup("https://cdn.example.com/other.css") is None
    assert diagnostics.lookup("") is None


def test_registry_keeps_main_frame_requests_only() -> None:
    page, diagnostics = _attached()
    main_frame = SimpleNamespace(parent_frame=None)
    child_frame = SimpleNamespace(parent_frame=main_frame)

    page.emit("response", _response("https://a.example/main.css", frame=main_frame))
    page.emit("response", _response("https://a.example/frame.css", frame=child_frame))
    frameless = _FramelessRequest(resource_type="image", url="https://a.example/sw.png")
    page.emit("requestfailed", frameless)

    assert list(diagnostics.resources) == ["https://a.example/main.css"]
    # The error-message diagnostics still see every asset.
    assert diagnostics.failed_requests == [
        "image net::ERR_FAILED https://a.example/sw.png"
    ]


def test_registry_records_failed_requests() -> None:
    page, diagnostics = _attached()
    request = FakeRequest(
        resource_type="font",
        url="https://cdn.example.com/a.woff2",
        failure="net::ERR_CONNECTION_RESET",
    )

    page.emit("requestfailed", request)

    assert diagnostics.lookup("https://cdn.example.com/a.woff2") == ResourceRecord(
        url="https://cdn.example.com/a.woff2",
        resource_type="font",
        failure="net::ERR_CONNECTION_RESET",
    )


def test_registry_keeps_the_latest_request_of_a_url() -> None:
    page, diagnostics = _attached()
    blocked = _response(status=403, content_type="text/html")
    page.emit("response", blocked)
    page.emit("requestfinished", blocked.request)
    page.emit(
        "console",
        FakeConsoleMessage(
            f"Did not parse stylesheet at '{CSS}' because non CSS MIME types are "
            "not allowed in strict mode."
        ),
    )
    assert diagnostics.refused_stylesheets() == [CSS]

    # Re-requested (retry in place / reload): the new outcome replaces the old.
    retried = _response()
    page.emit("response", retried)

    assert diagnostics.lookup(CSS) == ResourceRecord(
        url=CSS, resource_type="stylesheet", status=200, content_type="text/css"
    )
    assert diagnostics.refused_stylesheets() == []
    assert diagnostics.problem_resources() == []

    page.emit("requestfinished", retried.request)
    page.emit(
        "requestfailed",
        FakeRequest(resource_type="stylesheet", url=CSS, failure="net::ERR_FAILED"),
    )
    record = diagnostics.lookup(CSS)
    assert record is not None
    assert (record.status, record.failure, record.finished) == (
        None,
        "net::ERR_FAILED",
        False,
    )


def test_lookup_follows_redirects() -> None:
    page, diagnostics = _attached()
    first = _response("https://cdn.example.com/old.css", status=302, content_type=None)
    page.emit("response", first)
    page.emit("requestfinished", first.request)
    page.emit(
        "response",
        _response(
            "https://cdn.example.com/blocked",
            status=403,
            content_type="text/html",
            redirected_from=first.request,
        ),
    )

    record = diagnostics.lookup("https://cdn.example.com/old.css")

    assert record is not None
    assert (record.url, record.status) == ("https://cdn.example.com/blocked", 403)
    assert [r.url for r in diagnostics.problem_resources()] == [
        "https://cdn.example.com/blocked"
    ]


def test_problem_resources() -> None:
    page, diagnostics = _attached()
    for response in (
        _response("https://a.example/ok.css"),
        _response("https://a.example/waf.css", content_type="text/html"),
        _response(
            "https://a.example/gone.png",
            status=404,
            resource_type="image",
            content_type="text/html",
        ),
        _response(
            "https://a.example/ok.png", resource_type="image", content_type="image/png"
        ),
        _response(
            "https://a.example/app.js",
            status=500,
            resource_type="script",
            content_type="text/html",
        ),
        _response(
            "https://a.example/rate.woff2",
            status=429,
            resource_type="font",
            content_type="text/plain",
        ),
    ):
        page.emit("response", response)
        page.emit("requestfinished", response.request)
    page.emit(
        "requestfailed",
        FakeRequest(
            resource_type="image",
            url="https://a.example/cut.jpg",
            failure="net::ERR_FAILED",
        ),
    )

    assert [record.url for record in diagnostics.problem_resources()] == [
        "https://a.example/waf.css",
        "https://a.example/gone.png",
        "https://a.example/rate.woff2",
        "https://a.example/cut.jpg",
    ]
    assert [
        record.url for record in diagnostics.problem_resources(kinds=("script",))
    ] == ["https://a.example/app.js"]


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        (
            "error",
            f"Refused to apply style from '{CSS}' because its MIME type ('text/html') "
            "is not a supported stylesheet MIME type, and strict MIME checking is "
            "enabled.",
        ),
        (
            "error",
            f'[JavaScript Error: "The stylesheet {CSS} was not loaded because its MIME '
            'type, “text/html”, is not “text/css”." {file: "https://example.com/" '
            "line: 0}]",
        ),
        (
            "error",
            f"Did not parse stylesheet at '{CSS}' because non CSS MIME types are not "
            "allowed in strict mode.",
        ),
        (
            "warning",
            f"The stylesheet {CSS} was not loaded because its MIME type, “text/html”, "
            "is not “text/css”.",
        ),
    ],
)
def test_console_reports_refused_stylesheets(kind: str, text: str) -> None:
    page, diagnostics = _attached()

    page.emit("console", FakeConsoleMessage(text, kind=kind))

    assert diagnostics.refused_stylesheets() == [CSS]
    record = diagnostics.lookup(CSS)
    assert record == ResourceRecord(url=CSS, resource_type="stylesheet", refused=True)
    assert record.blocked_like()
    assert diagnostics.problem_resources() == [record]


def test_console_refusal_marks_the_recorded_stylesheet() -> None:
    page, diagnostics = _attached()
    response = _response(content_type="text/html")
    page.emit("response", response)
    page.emit("requestfinished", response.request)

    page.emit(
        "console",
        FakeConsoleMessage(
            f"Did not parse stylesheet at '{CSS}' because non CSS MIME types are "
            "not allowed in strict mode."
        ),
    )

    assert diagnostics.lookup(CSS) == ResourceRecord(
        url=CSS,
        resource_type="stylesheet",
        status=200,
        content_type="text/html",
        refused=True,
        finished=True,
    )


@pytest.mark.parametrize(
    ("kind", "text"),
    [
        # Not an error / warning.
        (
            "log",
            f"Did not parse stylesheet at '{CSS}' because non CSS MIME types are not "
            "allowed in strict mode.",
        ),
        # Firefox in quirks mode applies the sheet anyway.
        (
            "warning",
            f'[JavaScript Warning: "The stylesheet {CSS} was loaded as CSS even though '
            'its MIME type, “text/html”, is not “text/css”."]',
        ),
        ("error", "Failed to load resource: the server responded with a status of 404"),
        ("error", "Uncaught TypeError: x is not a function"),
    ],
)
def test_console_ignores_other_messages(kind: str, text: str) -> None:
    page, diagnostics = _attached()

    page.emit("console", FakeConsoleMessage(text, kind=kind))

    assert diagnostics.resources == {}
    assert diagnostics.refused_stylesheets() == []


def test_console_opaque_response_blocking_marks_a_recorded_resource() -> None:
    page, diagnostics = _attached()
    page.emit("response", _response(content_type="text/html"))
    text = (
        '[JavaScript Warning: "A resource is blocked by OpaqueResponseBlocking, '
        'please check browser console for details." {file: "' + CSS + '" line: 0}]'
    )

    page.emit("console", FakeConsoleMessage(text, kind="warning", url=CSS))
    page.emit(
        "console",
        FakeConsoleMessage(text, kind="warning", url="https://a.example/unknown.css"),
    )

    assert diagnostics.refused_stylesheets() == [CSS]
    assert list(diagnostics.resources) == [CSS]


def test_registry_is_bounded() -> None:
    page, diagnostics = _attached()
    with patch("app.screenshots.capture.config.MAX_RESOURCE_RECORDS", 3):
        for index in range(5):
            response = _response(f"https://a.example/{index}.css")
            page.emit("response", response)
            page.emit("requestfinished", response.request)
        assert list(diagnostics.resources) == [
            "https://a.example/0.css",
            "https://a.example/1.css",
            "https://a.example/2.css",
        ]

        # A problem still gets in, in place of the oldest healthy record.
        page.emit("response", _response("https://a.example/waf.css", status=403))

    assert list(diagnostics.resources) == [
        "https://a.example/1.css",
        "https://a.example/2.css",
        "https://a.example/waf.css",
    ]


def test_diagnostics_track_the_main_document() -> None:
    page, diagnostics = _attached()
    child_frame = SimpleNamespace(parent_frame=SimpleNamespace(parent_frame=None))
    hop = _response("https://example.com/", status=301, resource_type="document")
    final = _response(
        "https://example.com/en/", resource_type="document", content_type="text/html"
    )

    page.emit("response", hop)
    assert (diagnostics.document_response, diagnostics.document_responses) == (None, 0)

    page.emit("response", final)
    page.emit(
        "response",
        _response(
            "https://ads.example/frame.html",
            resource_type="document",
            content_type="text/html",
            frame=child_frame,
        ),
    )
    page.emit("response", _response())

    assert diagnostics.document_response is final
    assert diagnostics.document_responses == 1
