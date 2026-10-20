"""Identity probe: what a site sees of each browser x device capture profile.

For every engine (chrome, msedge, firefox, webkit) x device (desktop 1920x1080,
tablet 834x1112, mobile 390x844) the probe connects to the remote browser
server, opens a context, serves a synthetic page through ``page.route`` (with
and without a meta viewport) and prints one JSON line: the main request's
headers (user-agent, sec-ch-ua*, accept-language) and what the page reads from
``navigator`` / ``matchMedia``.

Modes (``--modes``, comma-separated, default ``profile``):

- ``plain``: today's capture context (viewport + reduced motion only);
- ``profile``: the planned device profile (UA table below, ``is_mobile`` except
  on Firefox, touch, DPR 1);
- ``cdp``: ``profile`` plus a CDP ``Emulation.setUserAgentOverride`` that sets
  the client-hint brands and the ``mobile`` hint explicitly (chrome/msedge
  only) — the prototype for ``profiles.prepare_page_identity``.

``--locale en-US`` adds a context locale to the profile/cdp modes.

The headers are read where ``page.route`` intercepts the request. Chromium and
WebKit add their default Accept-Language later, in the network stack, so without
a locale it shows as null here although one is sent on the wire.

Run it in a throwaway worker-image container on the compose network::

    docker run --rm -i --network everneu-dashboard_default \\
      -e DATABASE_URL=postgresql+asyncpg://qa:qa_secret@db:5432/qa_screenshots \\
      -e REDIS_URL=redis://redis:6379 -e SECRET_KEY=<32+ chars> \\
      -v "$PWD":/app -w /app everneu-dashboard-worker \\
      python -m scripts.identity_probe --endpoint ws://browser --modes plain,profile
"""

import argparse
import asyncio
import json
import sys
from typing import Any

from playwright.async_api import Browser, Route, async_playwright

from app.core.config import settings
from app.tasks.worker import _BROWSER_WS_PORTS, _resolve_browser_type

BROWSERS = ("chrome", "msedge", "firefox", "webkit")
DEVICES: dict[str, dict[str, int]] = {
    "desktop": {"width": 1920, "height": 1080},
    "tablet": {"width": 834, "height": 1112},
    "mobile": {"width": 390, "height": 844},
}
MODES = ("plain", "profile", "cdp")
CHROMIUM_BROWSERS = ("chrome", "msedge")
ORIGIN = "https://qa.test"
PAGE_TIMEOUT_MS = 30_000

_PAGE_HTML = """<!doctype html>
<html><head><meta charset="utf-8">{meta}<title>identity probe</title></head>
<body><p>identity probe</p></body></html>"""
_META_VIEWPORT = '<meta name="viewport" content="width=device-width,initial-scale=1">'

_JS_IDENTITY = """async () => {
  const uad = navigator.userAgentData;
  let hev = null;
  if (uad && uad.getHighEntropyValues) {
    try {
      hev = await uad.getHighEntropyValues(
        ["architecture", "bitness", "model", "platformVersion", "fullVersionList"]);
    } catch (e) {
      hev = null;
    }
  }
  const list = (items) => items ? items.map((b) => b.brand + "/" + b.version) : null;
  return {
    user_agent: navigator.userAgent,
    brands: uad ? list(uad.brands) : null,
    uad_mobile: uad ? uad.mobile : null,
    uad_platform: uad ? uad.platform : null,
    full_version_list: hev ? list(hev.fullVersionList) : null,
    platform_version: hev ? hev.platformVersion : null,
    model: hev ? hev.model : null,
    architecture: hev ? hev.architecture : null,
    webdriver: navigator.webdriver,
    platform: navigator.platform,
    max_touch_points: navigator.maxTouchPoints,
    pointer_coarse: matchMedia("(pointer: coarse)").matches,
    hover_none: matchMedia("(hover: none)").matches,
    inner_width: window.innerWidth,
    languages: navigator.languages,
    device_pixel_ratio: window.devicePixelRatio,
    touch_events: "ontouchstart" in window,
  };
}"""

_HEADER_NAMES = (
    "user-agent",
    "sec-ch-ua",
    "sec-ch-ua-mobile",
    "sec-ch-ua-platform",
    "accept-language",
)


def _major(version: str) -> str:
    return version.split(".", 1)[0]


def profile_user_agent(browser_name: str, device: str, version: str) -> str | None:
    """The planned capture UA for *browser_name* on *device* (plan item 10).

    ``None`` means "keep the browser's own UA" (desktop Firefox and WebKit).
    *version* is ``browser.version``; Chromium UAs carry only the major.
    """
    major = _major(version)
    if browser_name in CHROMIUM_BROWSERS:
        if device == "desktop":
            ua = (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{major}.0.0.0 Safari/537.36"
            )
            suffix = f" Edg/{major}.0.0.0"
        else:
            safari = "Mobile Safari/537.36" if device == "mobile" else "Safari/537.36"
            ua = (
                "Mozilla/5.0 (Linux; Android 10; K) AppleWebKit/537.36 "
                f"(KHTML, like Gecko) Chrome/{major}.0.0.0 {safari}"
            )
            suffix = f" EdgA/{major}.0.0.0"
        return ua + suffix if browser_name == "msedge" else ua
    if device == "desktop":
        return None
    if browser_name == "firefox":
        form = "Mobile" if device == "mobile" else "Tablet"
        return (
            f"Mozilla/5.0 (Android 14; {form}; rv:{major}.0) "
            f"Gecko/{major}.0 Firefox/{major}.0"
        )
    if browser_name == "webkit":
        os_part = (
            "iPhone; CPU iPhone OS 18_5 like Mac OS X"
            if device == "mobile"
            else "iPad; CPU OS 18_5 like Mac OS X"
        )
        return (
            f"Mozilla/5.0 ({os_part}) AppleWebKit/605.1.15 "
            f"(KHTML, like Gecko) Version/{version} Mobile/15E148 Safari/604.1"
        )
    return None


def context_options(
    browser_name: str,
    device: str,
    version: str,
    mode: str,
    *,
    locale: str | None = None,
) -> dict[str, Any]:
    """``new_context`` kwargs for *mode* (``plain`` = today's capture context)."""
    options: dict[str, Any] = {
        "viewport": dict(DEVICES[device]),
        "reduced_motion": "reduce",
    }
    if mode == "plain":
        return options
    options["device_scale_factor"] = 1
    if locale:
        options["locale"] = locale
    user_agent = profile_user_agent(browser_name, device, version)
    if user_agent:
        options["user_agent"] = user_agent
    if device != "desktop":
        options["has_touch"] = True
        # Playwright rejects is_mobile on Firefox: touch + UA only there.
        if browser_name != "firefox":
            options["is_mobile"] = True
    return options


# Chromium's own brand list (GenerateBrandVersionList in
# components/embedder_support/user_agent_utils.cc): a GREASE brand, "Chromium"
# and the product brand, in a permutation seeded by the major version.
_GREASE_CHARS = (" ", "(", ":", "-", ".", "/", ")", ";", "=", "?", "_")
_GREASE_VERSIONS = ("8", "99", "24")
_BRAND_ORDERS = ((0, 1, 2), (0, 2, 1), (1, 0, 2), (1, 2, 0), (2, 0, 1), (2, 1, 0))


def chromium_brands(major: int, brand: str, version: str) -> list[dict[str, str]]:
    """Brand list as Chromium builds it, with *brand* as the product brand.

    *version* is the major for ``brands`` or a full version for
    ``fullVersionList`` (the GREASE entry then gets ``.0.0.0``).
    """
    grease_version = _GREASE_VERSIONS[major % 3]
    if "." in version:
        grease_version += ".0.0.0"
    first, second = _GREASE_CHARS[major % 11], _GREASE_CHARS[(major + 1) % 11]
    grease = {"brand": f"Not{first}A{second}Brand", "version": grease_version}
    order = _BRAND_ORDERS[major % 6]
    out: list[dict[str, str]] = [{}, {}, {}]
    out[order[0]] = grease
    out[order[1]] = {"brand": "Chromium", "version": version}
    out[order[2]] = {"brand": brand, "version": version}
    return out


def cdp_user_agent_override(
    browser_name: str,
    device: str,
    version: str,
    user_agent: str,
    *,
    locale: str | None = None,
) -> dict[str, Any]:
    """``Emulation.setUserAgentOverride`` params with explicit client hints.

    Playwright sends the same command for a ``user_agent`` option, with
    metadata derived from the UA but without brands (Chromium then keeps its
    own list) and with ``mobile`` = ``is_mobile``. This override sets the
    brands explicitly and reports ``mobile`` only for the phone profile, like
    a real Android tablet. It replaces Playwright's override for the page, so
    it must repeat ``acceptLanguage`` when a locale is set. The full version
    list is left to Chromium (Edge's Chromium build number is not in
    ``browser.version``).
    """
    major = int(_major(version))
    brand = "Microsoft Edge" if browser_name == "msedge" else "Google Chrome"
    android = device != "desktop"
    params: dict[str, Any] = {
        "userAgent": user_agent,
        "userAgentMetadata": {
            "brands": chromium_brands(major, brand, str(major)),
            "platform": "Android" if android else "Linux",
            "platformVersion": "10" if android else "",
            "architecture": "arm" if android else "x86",
            "model": "",
            "mobile": device == "mobile",
        },
    }
    if locale:
        params["acceptLanguage"] = locale
    return params


async def _connect(pw: Any, base: str, browser_name: str) -> Browser:
    browser_type, _channel, _chromium_args = _resolve_browser_type(pw, browser_name)
    port = _BROWSER_WS_PORTS[browser_name]
    ws_path = settings.BROWSER_WS_PATH.strip("/")
    endpoint = f"{base.rstrip('/')}:{port}/{ws_path}"
    return await browser_type.connect(
        endpoint, timeout=settings.BROWSER_CONNECT_TIMEOUT_MS
    )


async def probe_one(
    browser: Browser,
    browser_name: str,
    device: str,
    mode: str,
    *,
    locale: str | None = None,
) -> dict[str, Any]:
    """Load the synthetic page (with, then without a meta viewport) in a fresh
    context for one combination and return the observations."""
    version = browser.version
    options = context_options(browser_name, device, version, mode, locale=locale)
    record: dict[str, Any] = {
        "browser": browser_name,
        "version": version,
        "device": device,
        "viewport": "{width}x{height}".format(**DEVICES[device]),
        "mode": mode,
        "context": {k: v for k, v in options.items() if k != "viewport"},
    }
    headers: dict[str, dict[str, str]] = {}

    async def serve(route: Route) -> None:
        request = route.request
        path = request.url.removeprefix(ORIGIN)
        if request.resource_type != "document":
            await route.fulfill(status=404, body="")
            return
        headers[path] = await request.all_headers()
        meta = _META_VIEWPORT if path == "/meta" else ""
        await route.fulfill(
            status=200,
            content_type="text/html; charset=utf-8",
            body=_PAGE_HTML.format(meta=meta),
        )

    context = await browser.new_context(**options)
    try:
        page = await context.new_page()
        page.set_default_timeout(PAGE_TIMEOUT_MS)
        if mode == "cdp":
            params = cdp_user_agent_override(
                browser_name, device, version, options["user_agent"], locale=locale
            )
            session = await context.new_cdp_session(page)
            await session.send("Emulation.setUserAgentOverride", params)
            record["cdp_brands"] = [
                f"{b['brand']}/{b['version']}"
                for b in params["userAgentMetadata"]["brands"]
            ]
        await page.route(f"{ORIGIN}/**", serve)
        await page.goto(f"{ORIGIN}/meta", wait_until="load")
        record["page"] = await page.evaluate(_JS_IDENTITY)
        await page.goto(f"{ORIGIN}/nometa", wait_until="load")
        record["inner_width_no_meta"] = await page.evaluate("() => window.innerWidth")
    finally:
        await context.close()
    sent = headers.get("/meta", {})
    record["headers"] = {name: sent.get(name) for name in _HEADER_NAMES}
    if headers.get("/nometa", {}).get("user-agent") != sent.get("user-agent"):
        record["headers_differ_without_meta"] = True
    return record


async def run(
    base: str,
    browsers: list[str],
    devices: list[str],
    modes: list[str],
    *,
    locale: str | None = None,
) -> int:
    failures = 0
    async with async_playwright() as pw:
        for browser_name in browsers:
            try:
                browser = await _connect(pw, base, browser_name)
            except Exception as exc:
                failures += 1
                _emit({"endpoint": base, "browser": browser_name, "error": str(exc)})
                continue
            try:
                for mode in modes:
                    if mode == "cdp" and browser_name not in CHROMIUM_BROWSERS:
                        continue
                    for device in devices:
                        try:
                            record = await probe_one(
                                browser, browser_name, device, mode, locale=locale
                            )
                        except Exception as exc:
                            failures += 1
                            record = {
                                "browser": browser_name,
                                "device": device,
                                "mode": mode,
                                "error": str(exc).splitlines()[0],
                            }
                        _emit({"endpoint": base, **record})
            finally:
                await browser.close()
    return failures


def _emit(record: dict[str, Any]) -> None:
    print(json.dumps(record, ensure_ascii=False), flush=True)


def _csv(value: str, allowed: tuple[str, ...]) -> list[str]:
    items = [v.strip() for v in value.split(",") if v.strip()]
    unknown = [v for v in items if v not in allowed]
    if unknown:
        raise argparse.ArgumentTypeError(
            f"unknown: {', '.join(unknown)} (allowed: {', '.join(allowed)})"
        )
    return items


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "--endpoint",
        default=settings.BROWSER_WS_ENDPOINT,
        help="browser server base host without port, e.g. ws://browser "
        "(default: BROWSER_WS_ENDPOINT)",
    )
    parser.add_argument(
        "--browsers",
        type=lambda v: _csv(v, BROWSERS),
        default=list(BROWSERS),
    )
    parser.add_argument(
        "--devices",
        type=lambda v: _csv(v, tuple(DEVICES)),
        default=list(DEVICES),
    )
    parser.add_argument(
        "--modes",
        type=lambda v: _csv(v, MODES),
        default=["profile"],
        help="plain, profile and/or cdp (default: profile)",
    )
    parser.add_argument(
        "--locale",
        default=None,
        help="context locale for the profile/cdp modes, e.g. en-US (default: none)",
    )
    args = parser.parse_args()
    if not args.endpoint:
        parser.error("--endpoint is required when BROWSER_WS_ENDPOINT is empty")
    failures = asyncio.run(
        run(args.endpoint, args.browsers, args.devices, args.modes, locale=args.locale)
    )
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
