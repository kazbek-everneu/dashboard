"""Network observation of one capture page: failure diagnostics for error
messages, a per-URL registry of what the network and the console reported for
each main-frame resource, and the in-flight request tracker behind the
adaptive waits."""

import re
from collections.abc import Collection
from dataclasses import dataclass, field
from typing import Any

from playwright.async_api import ConsoleMessage, Page, Request, Response

from app.screenshots.capture import clock
from app.screenshots.capture import config as cfg
from app.screenshots.capture.types import ResourceRecord

_MAX_NETWORK_DIAGNOSTICS = 8
_NETWORK_DIAGNOSTIC_TYPES = {"image", "media", "font", "stylesheet"}

# Console reports of a stylesheet the engine refused because of its MIME type
# (typically a WAF answering with an HTML page), one pattern per engine.
# Chromium 149 refuses such a sheet silently; the registry still flags it
# through its text/html content type (ResourceRecord.blocked_like).
_REFUSED_STYLESHEET_PATTERNS = (
    # Chromium: Refused to apply style from '<URL>' because its MIME type
    # ('text/html') is not a supported stylesheet MIME type, and strict MIME
    # checking is enabled.
    re.compile(r"Refused to apply style from '([^']+)' because its MIME type"),
    # Firefox (wrapped in [JavaScript Error: "…" {file: … line: 0}]): The
    # stylesheet <URL> was not loaded because its MIME type, “text/html”, is
    # not “text/css”.
    re.compile(r"The stylesheet (\S+) was not loaded because its MIME type"),
    # WebKit: Did not parse stylesheet at '<URL>' because non CSS MIME types
    # are not allowed in strict mode.
    re.compile(r"Did not parse stylesheet at '([^']+)' because non CSS MIME"),
)
# Firefox blocks a cross-origin response of the wrong type (HTML for a
# stylesheet or image) with this warning; the URL is the message location.
_OPAQUE_RESPONSE_BLOCKED = "blocked by OpaqueResponseBlocking"
_CONSOLE_REFUSAL_TYPES = frozenset({"error", "warning"})
_MAX_REDIRECT_HOPS = 10


def shorten(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


def resource_key(url: object) -> str:
    """Registry key of *url*: the URL without its fragment; "" for URLs the
    network never serves (empty, data:, blob:)."""
    text = str(url or "")
    if not text or text.startswith(("data:", "blob:")):
        return ""
    return text.split("#", 1)[0]


def refused_stylesheet_url(text: str) -> str | None:
    """The stylesheet URL of an engine's "refused because of its MIME type"
    console message, if *text* is one."""
    for pattern in _REFUSED_STYLESHEET_PATTERNS:
        match = pattern.search(text)
        if match:
            return match.group(1)
    return None


def is_main_frame_request(request: Any) -> bool:
    """True for a request of the page's main frame. A request without a frame
    (a service worker's own fetch) is not one; test fakes without frame
    attributes count as main-frame requests."""
    try:
        frame = request.frame
    except AttributeError:
        return True
    except Exception:  # Playwright raises when the request has no frame
        return False
    return getattr(frame, "parent_frame", None) is None


def _attr_text(obj: Any, name: str) -> str:
    value = getattr(obj, name, "")
    if callable(value):
        value = value()
    return str(value or "")


def _status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _content_type(response: Any) -> str | None:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, dict):
        return None
    for name, value in headers.items():
        if str(name).lower() == "content-type":
            return str(value)
    return None


def _is_problem(record: ResourceRecord) -> bool:
    return bool(
        record.refused
        or record.failure
        or (record.status or 0) >= 400
        or record.blocked_like()
    )


@dataclass
class NetworkDiagnostics:
    """What the network told us about one capture page.

    ``failed_requests`` / ``bad_responses`` are the short asset diagnostics
    appended to error messages (summary()). ``resources`` maps each
    main-frame resource URL (fragment dropped, bounded by
    config.MAX_RESOURCE_RECORDS) to a ResourceRecord of its latest request:
    status, content type, requestfailed text, whether it finished, and whether
    the console reported the engine refused it. ``document_response`` is the
    latest main-frame document response (redirect hops excluded) and
    ``document_responses`` counts them, so navigation can tell that the page
    moved on to a new document.
    """

    failed_requests: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)
    resources: dict[str, ResourceRecord] = field(default_factory=dict)
    document_response: Any = None
    document_responses: int = 0
    # resource key -> key it redirected to
    _redirects: dict[str, str] = field(default_factory=dict, init=False, repr=False)

    def record_failed_request(self, request: Request) -> None:
        self._note_failure(request)
        if len(self.failed_requests) >= _MAX_NETWORK_DIAGNOSTICS:
            return
        resource_type = str(getattr(request, "resource_type", ""))
        if resource_type not in _NETWORK_DIAGNOSTIC_TYPES:
            return
        failure = getattr(request, "failure", "")
        if callable(failure):
            failure = failure()
        self.failed_requests.append(
            f"{resource_type} {shorten(str(failure or 'failed'), 80)} "
            f"{shorten(str(getattr(request, 'url', '')), 180)}"
        )

    def record_response(self, response: Response) -> None:
        self._note_response(response)
        if len(self.bad_responses) >= _MAX_NETWORK_DIAGNOSTICS:
            return
        status = int(getattr(response, "status", 0) or 0)
        if status < 400:
            return
        request = getattr(response, "request", None)
        resource_type = str(getattr(request, "resource_type", ""))
        if resource_type not in _NETWORK_DIAGNOSTIC_TYPES:
            return
        self.bad_responses.append(
            f"{resource_type} HTTP {status} "
            f"{shorten(str(getattr(response, 'url', '')), 180)}"
        )

    def record_request_finished(self, request: Request) -> None:
        if not is_main_frame_request(request):
            return
        record = self.resources.get(resource_key(getattr(request, "url", "")))
        if record is not None:
            record.finished = True

    def record_console(self, message: ConsoleMessage) -> None:
        """Mark the resources the console says the engine refused."""
        if _attr_text(message, "type") not in _CONSOLE_REFUSAL_TYPES:
            return
        text = _attr_text(message, "text")
        key = resource_key(refused_stylesheet_url(text))
        if key:
            record = self.resources.get(key)
            if record is None:
                self._store(
                    ResourceRecord(url=key, resource_type="stylesheet", refused=True)
                )
            else:
                record.refused = True
                record.resource_type = record.resource_type or "stylesheet"
            return
        if _OPAQUE_RESPONSE_BLOCKED in text:
            location = getattr(message, "location", None)
            if isinstance(location, dict):
                record = self.resources.get(resource_key(location.get("url")))
                if record is not None:
                    record.refused = True

    def summary(self) -> str | None:
        parts: list[str] = []
        if self.failed_requests:
            parts.append("failed requests: " + "; ".join(self.failed_requests))
        if self.bad_responses:
            parts.append("bad responses: " + "; ".join(self.bad_responses))
        if not parts:
            return None
        return "Network diagnostics: " + " | ".join(parts)

    def attach(self, page: Page) -> None:
        """Subscribe to *page*'s events before navigation."""
        page.on("requestfailed", self.record_failed_request)
        page.on("response", self.record_response)
        page.on("requestfinished", self.record_request_finished)
        page.on("console", self.record_console)

    def lookup(self, url: str) -> ResourceRecord | None:
        """What the network saw for *url* (its latest request), if anything.
        The fragment is ignored and a redirect is followed to the record of
        the URL it led to."""
        record = self.resources.get(resource_key(url))
        for _ in range(_MAX_REDIRECT_HOPS):
            if record is None or record.status is None:
                break
            if not 300 <= record.status < 400:
                break
            target = self._redirects.get(record.url)
            if target is None or target not in self.resources:
                break
            record = self.resources[target]
        return record

    def problem_resources(
        self, *, kinds: Collection[str] = ("stylesheet", "image", "font")
    ) -> list[ResourceRecord]:
        """Resources of *kinds* that failed, returned an error status, look
        blocked (ResourceRecord.blocked_like, e.g. a stylesheet served as
        text/html) or were refused, in the order they were first seen."""
        wanted = set(kinds)
        return [
            record
            for record in self.resources.values()
            if record.resource_type in wanted and _is_problem(record)
        ]

    def refused_stylesheets(self) -> list[str]:
        """URLs of stylesheets the console reported refused (Firefox and
        WebKit report a non-CSS MIME type; Chromium 149 stays silent)."""
        return [
            record.url
            for record in self.resources.values()
            if record.refused and record.resource_type == "stylesheet"
        ]

    # -- registry -----------------------------------------------------------

    def _store(self, record: ResourceRecord) -> bool:
        """Keep the new *record*; once the registry is full, only a problem
        record gets in, replacing the oldest finished healthy one."""
        if len(self.resources) >= cfg.MAX_RESOURCE_RECORDS:
            if not _is_problem(record):
                return False
            victim = next(
                (
                    key
                    for key, kept in self.resources.items()
                    if kept.finished and not _is_problem(kept)
                ),
                None,
            )
            if victim is None:
                return False
            del self.resources[victim]
        self.resources[record.url] = record
        return True

    def _note_response(self, response: Response) -> None:
        request = getattr(response, "request", None)
        if request is None or not is_main_frame_request(request):
            return
        url = getattr(response, "url", "") or getattr(request, "url", "")
        key = resource_key(url)
        resource_type = str(getattr(request, "resource_type", "") or "")
        status = _status(response)
        redirect = status is not None and 300 <= status < 400
        if resource_type == "document" and not redirect:
            self.document_response = response
            self.document_responses += 1
        if not key:
            return
        previous = getattr(request, "redirected_from", None)
        if previous is not None:
            source = resource_key(getattr(previous, "url", ""))
            if source and source != key:
                self._redirects[source] = key
                if len(self._redirects) > cfg.MAX_RESOURCE_RECORDS:
                    self._redirects.pop(next(iter(self._redirects)))
        record = self.resources.get(key)
        new = record is None
        if record is None:
            record = ResourceRecord(url=key, resource_type=resource_type)
        elif record.finished or record.failure:
            # A new request for a URL seen before (a retry, a reload): its
            # outcome replaces the earlier one's.
            record.failure = None
            record.refused = False
            record.finished = False
        if resource_type:
            record.resource_type = resource_type
        record.status = status
        record.content_type = _content_type(response)
        if new:
            self._store(record)

    def _note_failure(self, request: Request) -> None:
        if not is_main_frame_request(request):
            return
        key = resource_key(getattr(request, "url", ""))
        if not key:
            return
        failure = getattr(request, "failure", None)
        if callable(failure):
            failure = failure()
        text = str(failure or "failed")
        record = self.resources.get(key)
        if record is None:
            record = ResourceRecord(
                url=key,
                resource_type=str(getattr(request, "resource_type", "") or ""),
                failure=text,
            )
            self._store(record)
            return
        if record.finished:
            # An earlier request of this URL finished; this one failed.
            record.status = None
            record.content_type = None
            record.refused = False
        record.failure = text
        record.finished = False


def append_network_diagnostics(message: str, diagnostics: NetworkDiagnostics) -> str:
    summary = diagnostics.summary()
    if not summary:
        return message
    return f"{message}; {summary}"


class InflightTracker:
    """Tracks in-flight page requests that affect what the screenshot shows.

    Fed by the page's request / requestfinished / requestfailed events, attached
    before navigation. Requests are keyed by the Request object itself, so a
    finish for a request we never saw is a no-op and the count can't go
    negative; each redirect hop is its own Request and finishes on its own.
    ``started_count`` counts every tracked request ever started.
    """

    def __init__(self) -> None:
        # request -> (started_at, is_data)
        self._inflight: dict[object, tuple[float, bool]] = {}
        self.started_count = 0

    def on_request(self, request: Request) -> None:
        resource_type = str(getattr(request, "resource_type", ""))
        if resource_type in cfg.TRACKED_VISUAL_TYPES:
            self._inflight[request] = (clock.now(), False)
        elif resource_type in cfg.TRACKED_DATA_TYPES:
            self._inflight[request] = (clock.now(), True)
        else:
            return
        self.started_count += 1

    def on_request_done(self, request: Request) -> None:
        self._inflight.pop(request, None)

    def inflight(self, *, include_data: bool = False) -> int:
        now = clock.now()
        count = 0
        for started_at, is_data in self._inflight.values():
            if is_data and not include_data:
                continue
            stale_ms = cfg.INFLIGHT_STALE_DATA_MS if is_data else cfg.INFLIGHT_STALE_MS
            if (now - started_at) * 1000 < stale_ms:
                count += 1
        return count

    def attach(self, page: Page) -> None:
        page.on("request", self.on_request)
        page.on("requestfinished", self.on_request_done)
        page.on("requestfailed", self.on_request_done)


async def wait_for_quiet(
    page: Page,
    tracker: InflightTracker | None,
    *,
    min_ms: float,
    quiet_ms: float,
    max_ms: float,
    include_data: bool = False,
) -> None:
    """Wait until no tracked request has been in flight for *quiet_ms*.

    Never shorter than *min_ms* (gives throttled scroll listeners time to start
    their requests) and never longer than *max_ms* — the fixed pause this
    replaces — so a page is only ever waited on less than before, not more.
    Without a tracker it degrades to the old fixed pause of *max_ms*.

    Polls via page.wait_for_timeout: its reply arrives after every request event
    the browser emitted before it, so the in-flight count is current even when
    events are delayed on a busy browser host. Elapsed time is the larger of the
    summed poll intervals and the real clock, which keeps the cap honest on a
    CPU-starved host and the loop finite under test fakes.
    """
    if tracker is None:
        await page.wait_for_timeout(max_ms)
        return
    started = clock.now()
    waited = 0.0
    # Quiet is measured from the first poll that saw nothing in flight — the
    # conservative end of the interval in which the last request finished.
    quiet_since: float | None = 0.0
    while True:
        elapsed = max(waited, (clock.now() - started) * 1000)
        if tracker.inflight(include_data=include_data):
            quiet_since = None
        elif quiet_since is None:
            quiet_since = elapsed
        if elapsed >= max_ms or (
            elapsed >= min_ms
            and quiet_since is not None
            and elapsed - quiet_since >= quiet_ms
        ):
            return
        step = min(cfg.QUIET_POLL_MS, max_ms - elapsed)
        await page.wait_for_timeout(step)
        waited += step
