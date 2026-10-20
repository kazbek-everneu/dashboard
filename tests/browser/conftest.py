"""Real-browser tests: the capture code against the remote browser server.

Every test under tests/browser is marked ``browser`` and skipped unless
BROWSER_WS_ENDPOINT points at a reachable browser server (CI has none). The
synthetic pages are served over real HTTP by ``LocalSite`` inside the test
process, so the run must happen where the browser can reach this process — a
container on the dev compose network:

    docker build -t qa-worker-test - <<'EOF'
    FROM everneu-dashboard-worker
    RUN pip install --no-cache-dir "pytest>=8.2" "pytest-asyncio>=0.23"
    EOF
    docker run --rm --network everneu-dashboard_default \
      -e BROWSER_WS_ENDPOINT=ws://browser -e PYTHONDONTWRITEBYTECODE=1 \
      --user "$(id -u):$(id -g)" -e HOME=/tmp \
      -v "$PWD":/app -w /app qa-worker-test \
      python -m pytest -m browser tests/browser -q -p no:cacheprovider
"""

import contextlib
import os
import socket
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from pathlib import Path
from urllib.parse import urlsplit

import pytest
import pytest_asyncio
from playwright.async_api import Browser, async_playwright

from tests.browser.helpers import LocalSite

_HERE = Path(__file__).parent
_PROBE_PORT = 3001  # chrome's launchServer; see app.tasks.worker._BROWSER_WS_PORTS


def _browser_server_reachable() -> bool:
    endpoint = os.environ.get("BROWSER_WS_ENDPOINT", "")
    host = urlsplit(endpoint).hostname
    if not host:
        return False
    try:
        with socket.create_connection((host, _PROBE_PORT), timeout=1):
            return True
    except OSError:
        return False


def pytest_collection_modifyitems(config: pytest.Config, items: list) -> None:
    browser_items = [item for item in items if _HERE in Path(item.path).parents]
    if not browser_items:
        return
    reachable = _browser_server_reachable()
    skip = pytest.mark.skip(
        reason="no reachable browser server (set BROWSER_WS_ENDPOINT, see "
        "tests/browser/conftest.py)"
    )
    for item in browser_items:
        item.add_marker(pytest.mark.browser)
        if not reachable:
            item.add_marker(skip)


@pytest.fixture
def site() -> Iterator[LocalSite]:
    """A fresh HTTP site for one test; ``site.add(path, body, ...)``."""
    local_site = LocalSite()
    try:
        yield local_site
    finally:
        local_site.close()


@pytest_asyncio.fixture
async def browsers() -> AsyncIterator[Callable[[str], Awaitable[Browser]]]:
    """``await browsers("chrome")`` — a connected browser, closed after the test."""
    from app.tasks.worker import _launch_browser

    async with async_playwright() as pw:
        opened: dict[str, Browser] = {}

        async def get(name: str = "chrome") -> Browser:
            if name not in opened:
                opened[name] = await _launch_browser(pw, name)
            return opened[name]

        try:
            yield get
        finally:
            for browser in opened.values():
                with contextlib.suppress(Exception):
                    await browser.close()
