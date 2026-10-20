"""Helpers for the real-browser tests: a local HTTP site, the capture call and a
pixel sampler that decodes screenshots in the browser (no image library needed).
"""

import asyncio
import base64
import collections
import os
import socket
import threading
import time
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from playwright.async_api import Browser

from app.screenshots.capture import pipeline, profiles
from app.screenshots.capture.config import EffectiveConfig
from app.screenshots.capture.types import CaptureResult
from app.screenshots.image_dimensions import read_image_dimensions_from_bytes

ENGINES = ("chrome", "firefox", "webkit")


@dataclass
class Response:
    body: bytes = b""
    status: int = 200
    content_type: str = "text/html; charset=utf-8"
    headers: dict[str, str] = field(default_factory=dict)


@dataclass
class Route:
    responses: list[Response]
    delay_s: float = 0.0
    hang: bool = False


class LocalSite:
    """Synthetic pages served over real HTTP from a thread of this process.

    The browser reaches it at ``site.url(path)`` (this container's address on the
    compose network; override with QA_TEST_SITE_HOST). Per path:
    ``add(path, body, status=…, content_type=…, headers=…)``; ``delay_s`` delays
    the reply, ``hang=True`` never replies (until the site closes), and
    ``responses=[Response(...), ...]`` answers in sequence (the last one repeats).
    ``hits[path]`` counts requests. Query strings are ignored for routing.
    """

    def __init__(self, host: str | None = None) -> None:
        self.routes: dict[str, Route] = {}
        self.hits: collections.Counter[str] = collections.Counter()
        self._closed = threading.Event()
        self._lock = threading.Lock()
        self.server = ThreadingHTTPServer(("0.0.0.0", 0), _handler_for(self))
        self.server.daemon_threads = True
        self.port = self.server.server_address[1]
        self.host = (
            host
            or os.environ.get("QA_TEST_SITE_HOST")
            or socket.gethostbyname(socket.gethostname())
        )
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def url(self, path: str = "/") -> str:
        return f"http://{self.host}:{self.port}{path}"

    def add(
        self,
        path: str,
        body: str | bytes = b"",
        *,
        status: int = 200,
        content_type: str = "text/html; charset=utf-8",
        headers: dict[str, str] | None = None,
        delay_s: float = 0.0,
        hang: bool = False,
        responses: list[Response] | None = None,
    ) -> str:
        if responses is None:
            data = body.encode() if isinstance(body, str) else body
            responses = [Response(data, status, content_type, dict(headers or {}))]
        self.routes[path] = Route(responses=responses, delay_s=delay_s, hang=hang)
        return self.url(path)

    def close(self) -> None:
        self._closed.set()
        self.server.shutdown()
        self.server.server_close()

    def _next_response(self, path: str) -> tuple[Route | None, Response | None]:
        with self._lock:
            index = self.hits[path]
            self.hits[path] += 1
        route = self.routes.get(path)
        if route is None:
            return None, None
        return route, route.responses[min(index, len(route.responses) - 1)]


def _handler_for(site: LocalSite) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            route, response = site._next_response(path)
            if route is None or response is None:
                self.send_error(404)
                return
            if route.hang:
                site._closed.wait(timeout=300)
                return
            if route.delay_s:
                time.sleep(route.delay_s)
            self.send_response(response.status)
            self.send_header("Content-Type", response.content_type)
            self.send_header("Content-Length", str(len(response.body)))
            if "Cache-Control" not in response.headers:
                self.send_header("Cache-Control", "no-store")
            for name, value in response.headers.items():
                self.send_header(name, value)
            self.end_headers()
            self.wfile.write(response.body)

        def log_message(self, format: str, *args: object) -> None:
            return

    return Handler


def make_config(browser_name: str, **overrides: object) -> EffectiveConfig:
    values: dict[str, object] = {
        "browser": browser_name,
        "fmt": "png",
        "quality": 85,
        "wait_until": "load",
        "timeout": 30000,
    }
    values.update(overrides)
    return EffectiveConfig(**values)  # type: ignore[arg-type]


async def capture(
    browser: Browser,
    url: str,
    *,
    browser_name: str = "chrome",
    viewport: tuple[int, int] = (1280, 800),
    device_type: str | None = None,
    **config_overrides: object,
) -> CaptureResult:
    """Run the real capture pipeline (with its retry policy) on *url*."""
    vp = {"width": viewport[0], "height": viewport[1]}
    profile = profiles.build_profile(
        browser_name,
        profiles.resolve_device_type(vp, device_type),
        browser_version=browser.version,
    )
    return await pipeline.capture_with_retries(
        browser=browser,
        url=url,
        viewport=vp,
        config=make_config(browser_name, **config_overrides),
        profile=profile,
    )


def image_size(data: bytes) -> tuple[int, int]:
    size = read_image_dimensions_from_bytes(data)
    assert size is not None, "screenshot bytes are not a PNG/JPEG image"
    return size


_SAMPLE_PIXELS = """async ({src, points}) => {
    const img = new Image();
    img.src = src;
    await img.decode();
    const canvas = document.createElement('canvas');
    canvas.width = img.naturalWidth;
    canvas.height = img.naturalHeight;
    const ctx = canvas.getContext('2d', {willReadFrequently: true});
    ctx.drawImage(img, 0, 0);
    return points.map(([x, y]) => Array.from(ctx.getImageData(x, y, 1, 1).data));
}"""


async def pixels_at(
    browser: Browser, image: bytes, points: list[tuple[int, int]]
) -> list[tuple[int, int, int]]:
    """RGB at each (x, y) of *image* (PNG/JPEG bytes), decoded in *browser*."""
    mime = "image/png" if image[:8] == b"\x89PNG\r\n\x1a\n" else "image/jpeg"
    src = f"data:{mime};base64,{base64.b64encode(image).decode()}"
    context = await browser.new_context()
    try:
        page = await context.new_page()
        await page.set_content("<!doctype html><title>sampler</title>")
        raw = await page.evaluate(
            _SAMPLE_PIXELS, {"src": src, "points": [list(p) for p in points]}
        )
    finally:
        await context.close()
    return [(int(r), int(g), int(b)) for r, g, b, _a in raw]


def near(
    rgb: tuple[int, int, int], expected: tuple[int, int, int], tol: int = 24
) -> bool:
    return all(abs(a - b) <= tol for a, b in zip(rgb, expected, strict=True))


async def eventually(check, *, timeout_s: float = 10.0, interval_s: float = 0.2):
    """Poll an async *check* until it returns a truthy value or time runs out."""
    deadline = time.monotonic() + timeout_s
    while True:
        value = await check()
        if value or time.monotonic() >= deadline:
            return value
        await asyncio.sleep(interval_s)
