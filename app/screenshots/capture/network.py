"""Network observation of one capture page: failure diagnostics for error
messages, and the in-flight request tracker behind the adaptive waits."""

from collections.abc import Collection
from dataclasses import dataclass, field

from playwright.async_api import Page, Request, Response

from app.screenshots.capture import clock
from app.screenshots.capture import config as cfg
from app.screenshots.capture.types import ResourceRecord

_MAX_NETWORK_DIAGNOSTICS = 8
_NETWORK_DIAGNOSTIC_TYPES = {"image", "media", "font", "stylesheet"}


def shorten(value: str, max_length: int) -> str:
    if len(value) <= max_length:
        return value
    return value[: max_length - 3] + "..."


@dataclass
class NetworkDiagnostics:
    failed_requests: list[str] = field(default_factory=list)
    bad_responses: list[str] = field(default_factory=list)

    def record_failed_request(self, request: Request) -> None:
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

    def lookup(self, url: str) -> ResourceRecord | None:
        """What the network saw for *url* (the latest request), if anything.
        Not recorded yet: always None."""
        return None

    def problem_resources(
        self, *, kinds: Collection[str] = ("stylesheet", "image", "font")
    ) -> list[ResourceRecord]:
        """Resources of *kinds* that failed, returned an error status or were
        refused (e.g. a stylesheet served as text/html). Not recorded yet:
        always empty."""
        return []

    def refused_stylesheets(self) -> list[str]:
        """URLs of stylesheets the browser refused to apply (console reports).
        Not recorded yet: always empty."""
        return []


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
