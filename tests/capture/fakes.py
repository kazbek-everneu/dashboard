"""Shared fakes for the capture tests.

FakePage answers page scripts by their ``/*qa:<name>*/`` marker: pass
``script_results={marker: value}`` where *value* is returned as-is (deep
copied), or a callable taking the evaluate argument, or an exception instance
to raise. Unmarked scripts fail loudly. Defaults describe an idle, settled page
with some text and no images, so the readiness loop ends after four samples.
"""

import copy
import math
import re
from collections.abc import Callable, Iterable
from typing import Any

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture.config import EffectiveConfig
from app.screenshots.capture.network import InflightTracker, NetworkDiagnostics
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureRun, Deadline
from app.screenshots.models import Screenshot

MARKER_RE = re.compile(r"/\*qa:([a-z0-9-]+)\*/")

READY_STABILITY_STATE: dict[str, Any] = {
    "readyState": "complete",
    "scrollHeight": 0,
    "pendingImages": 0,
    "pendingVisibleImages": 0,
    "hiddenPendingImages": 0,
    "pendingLazyRender": 0,
    "textLength": 42,
}

EMPTY_ASSET_STATE: dict[str, Any] = {
    "visibleImageCount": 0,
    "visibleBackgroundCount": 0,
    "missingVisibleImageCount": 0,
    "missingBackgroundImageCount": 0,
    "hiddenPendingImageCount": 0,
    "missingSamples": [],
}

DEFAULT_SCRIPT_RESULTS: dict[str, Any] = {
    "wait-fonts": True,
    "decode-images": EMPTY_ASSET_STATE,
    "stability-state": READY_STABILITY_STATE,
    "scroll-height": 0,
    "detect-lazy": False,
}


def script_marker(script: object) -> str:
    match = MARKER_RE.search(str(script))
    if match is None:
        raise AssertionError(
            f"page script without a /*qa:...*/ marker: {str(script)[:80]!r}"
        )
    return match.group(1)


def results_sequence(*values: Any) -> Callable[[Any], Any]:
    """A script result that returns *values* in order, then repeats the last."""
    remaining = list(values)

    def next_value(_arg: Any) -> Any:
        return remaining.pop(0) if len(remaining) > 1 else remaining[0]

    return next_value


def png_bytes(width: int, height: int) -> bytes:
    """Smallest PNG header read_image_dimensions_from_bytes understands."""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR"
        + width.to_bytes(4, "big")
        + height.to_bytes(4, "big")
        + b"\x08\x02\x00\x00\x00"
    )


def make_config(**overrides: Any) -> EffectiveConfig:
    values: dict[str, Any] = {
        "browser": "chromium",
        "fmt": "jpeg",
        "quality": 85,
        "wait_until": "load",
        "timeout": 1000,
    }
    values.update(overrides)
    return EffectiveConfig(**values)


class FakeRequest:
    def __init__(
        self,
        *,
        resource_type: str = "image",
        url: str = "https://cdn.example.com/missing.jpg",
        failure: str | None = "net::ERR_FAILED",
    ) -> None:
        self.resource_type = resource_type
        self.url = url
        self.failure = failure


class FakeResponse:
    def __init__(
        self,
        *,
        status: int = 200,
        url: str = "https://example.com/",
        headers: dict[str, str] | None = None,
        resource_type: str = "document",
    ) -> None:
        self.status = status
        self.url = url
        self.headers = {key.lower(): value for key, value in (headers or {}).items()}
        self.request = FakeRequest(resource_type=resource_type, url=url, failure=None)

    @property
    def ok(self) -> bool:
        return 200 <= self.status <= 299

    async def all_headers(self) -> dict[str, str]:
        return dict(self.headers)

    async def header_value(self, name: str) -> str | None:
        return self.headers.get(name.lower())


class FakeConsoleMessage:
    def __init__(self, text: str, *, kind: str = "error", url: str = "") -> None:
        self.text = text
        self.type = kind
        self.location = {"url": url, "lineNumber": 0, "columnNumber": 0}


class FakePage:
    def __init__(
        self,
        *,
        script_results: dict[str, Any] | None = None,
        failed_requests: Iterable[FakeRequest] = (),
        bad_responses: Iterable[FakeResponse] = (),
        goto_timeout_levels: Iterable[str] = (),
        goto_error: BaseException | None = None,
        response: FakeResponse | None = None,
        url: str = "https://example.com/",
        screenshot_data: bytes = b"img",
        screenshot_error: BaseException | None = None,
        load_state_error: BaseException | None = None,
    ) -> None:
        self.script_results: dict[str, Any] = copy.deepcopy(DEFAULT_SCRIPT_RESULTS)
        self.script_results.update(script_results or {})
        self.failed_requests = list(failed_requests)
        self.bad_responses = list(bad_responses)
        self.goto_timeout_levels = set(goto_timeout_levels)
        self.goto_error = goto_error
        self.response = response or FakeResponse(url=url)
        self.url = url
        self.screenshot_data = screenshot_data
        self.screenshot_error = screenshot_error
        self.load_state_error = load_state_error

        self.evaluated: list[str] = []  # markers, in order
        self.evaluate_calls: list[tuple[str, Any]] = []  # (marker, arg)
        # ("goto", wait_until) / ("evaluate", marker) / ("wait", ms) /
        # ("screenshot", kwargs) / ("set_viewport_size", size) / ("close", None)
        self.events: list[tuple[str, Any]] = []
        self.handlers: dict[str, list[Callable[[Any], Any]]] = {}
        self.goto_calls: list[tuple[str, dict[str, Any]]] = []
        self.reload_calls: list[dict[str, Any]] = []
        self.load_state_calls: list[tuple[str, dict[str, Any]]] = []
        self.waits: list[float] = []
        self.on_wait: Callable[[int], object] | None = None
        self.screenshot_calls: list[dict[str, Any]] = []
        self.closed = False

    @property
    def screenshot_kwargs(self) -> dict[str, Any] | None:
        return self.screenshot_calls[-1] if self.screenshot_calls else None

    def on(self, event: str, handler: Callable[[Any], Any]) -> None:
        self.handlers.setdefault(event, []).append(handler)

    def remove_listener(self, event: str, handler: Callable[[Any], Any]) -> None:
        self.handlers.get(event, []).remove(handler)

    def emit(self, event: str, payload: Any) -> None:
        for handler in list(self.handlers.get(event, [])):
            handler(payload)

    def _fire_network_events(self) -> None:
        for request in self.failed_requests:
            self.emit("requestfailed", request)
        for response in self.bad_responses:
            self.emit("response", response)

    async def goto(self, url: str, **kwargs: Any) -> FakeResponse:
        self.goto_calls.append((url, kwargs))
        self.events.append(("goto", kwargs.get("wait_until")))
        if self.goto_error is not None:
            raise self.goto_error
        if kwargs.get("wait_until") in self.goto_timeout_levels:
            raise PlaywrightTimeoutError(f"Timeout {kwargs.get('timeout')}ms exceeded")
        self._fire_network_events()
        return self.response

    async def reload(self, **kwargs: Any) -> FakeResponse:
        self.reload_calls.append(kwargs)
        self.events.append(("reload", kwargs.get("wait_until")))
        self._fire_network_events()
        return self.response

    async def wait_for_load_state(self, state: str = "load", **kwargs: Any) -> None:
        self.load_state_calls.append((state, kwargs))
        self.events.append(("load_state", state))
        if self.load_state_error is not None:
            raise self.load_state_error

    async def evaluate(self, script: str, *args: Any) -> Any:
        marker = script_marker(script)
        arg = args[0] if args else None
        self.evaluated.append(marker)
        self.evaluate_calls.append((marker, arg))
        self.events.append(("evaluate", marker))
        result = self.script_results.get(marker)
        if callable(result):
            result = result(arg)
        if isinstance(result, BaseException):
            raise result
        return copy.deepcopy(result)

    async def wait_for_timeout(self, timeout: float) -> None:
        self.waits.append(timeout)
        self.events.append(("wait", timeout))
        if self.on_wait is not None:
            self.on_wait(len(self.waits))

    async def screenshot(self, **kwargs: Any) -> bytes:
        self.screenshot_calls.append(kwargs)
        self.events.append(("screenshot", kwargs))
        if self.screenshot_error is not None:
            raise self.screenshot_error
        return self.screenshot_data

    async def set_viewport_size(self, size: dict[str, int]) -> None:
        self.events.append(("set_viewport_size", size))

    async def close(self) -> None:
        self.closed = True
        self.events.append(("close", None))


class FakeContext:
    def __init__(
        self,
        page: FakePage | None = None,
        *,
        page_factory: Callable[[], FakePage] | None = None,
        init_script_error: BaseException | None = None,
    ) -> None:
        self.page = page if page is not None else FakePage()
        self.page_factory = page_factory
        self.init_script_error = init_script_error
        self.init_scripts: list[str] = []
        self.pages: list[FakePage] = []
        self.closed = False

    async def add_init_script(self, script: str, /) -> None:
        # Positional-only on purpose: production code passes the script
        # positionally (the worker test fakes accept nothing else).
        if self.init_script_error is not None:
            raise self.init_script_error
        self.init_scripts.append(script)

    async def new_page(self) -> FakePage:
        page = self.page_factory() if self.page_factory is not None else self.page
        self.pages.append(page)
        return page

    async def close(self) -> None:
        self.closed = True


class FakeBrowser:
    def __init__(
        self,
        page: FakePage | None = None,
        *,
        version: str = "149.0.7800.0",
        connected: bool = True,
        page_factory: Callable[[], FakePage] | None = None,
        init_script_error: BaseException | None = None,
    ) -> None:
        self.page = page if page is not None else FakePage()
        self.version = version
        self.connected = connected
        self.page_factory = page_factory
        self.init_script_error = init_script_error
        self.context_kwargs: list[dict[str, Any]] = []
        self.contexts: list[FakeContext] = []
        self.closed = False

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(self, **kwargs: Any) -> FakeContext:
        self.context_kwargs.append(kwargs)
        context = FakeContext(
            self.page,
            page_factory=self.page_factory,
            init_script_error=self.init_script_error,
        )
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class FakeSession:
    def __init__(self) -> None:
        self.record: Screenshot | None = None
        self.added: list[Screenshot] = []
        self.commits = 0
        self.refreshed: list[Screenshot] = []

    def add(self, record: Screenshot) -> None:
        self.record = record
        self.added.append(record)

    async def commit(self) -> None:
        self.commits += 1

    async def refresh(self, record: Screenshot) -> None:
        self.refreshed.append(record)


def make_run(*, attach: bool = True, **overrides: Any) -> CaptureRun:
    """A CaptureRun on a FakePage (1280x720, make_config()). With *attach* the
    run's NetworkDiagnostics and InflightTracker listen to the page like in
    pipeline.run_attempt, so ``page.emit("request", ...)`` reaches them."""
    values: dict[str, Any] = {
        "url": "https://example.com/",
        "viewport": {"width": 1280, "height": 720},
        "config": make_config(),
        "profile": DeviceProfile(),
        "page": FakePage(),
        "tracker": InflightTracker(),
        "net": NetworkDiagnostics(),
        "report": CaptureReport(),
        "deadline": Deadline(budget_s=math.inf, started_at=0.0),
    }
    values.update(overrides)
    run = CaptureRun(**values)
    if attach:
        run.page.on("requestfailed", run.net.record_failed_request)
        run.page.on("response", run.net.record_response)
        if run.tracker is not None:
            run.tracker.attach(run.page)
    return run
