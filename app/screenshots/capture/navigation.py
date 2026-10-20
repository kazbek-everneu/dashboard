"""Navigation stage.

navigate() runs one ``page.goto`` that waits for DOMContentLoaded (every
engine), then a non-fatal wait for the load event, the post-navigation quiet
(network.wait_for_quiet) and the protection-page check: a WAF / bot challenge
page gets a bounded wait to pass, else PageBlockedError. reload() repeats the
same in place. report_status() turns the main document's outcome (HTTP error,
redirect) into capture warnings; the pipeline calls it once per successful
attempt, after any reload.

Playwright's own "load" / "networkidle" goto conditions are never used: a page
whose load event never fires is still captured (readiness then accepts
readyState "interactive"), and analytics or long-polling keep networkidle from
ever firing.
"""

import contextlib
import logging
import math
import re
from collections.abc import Mapping
from contextlib import AbstractContextManager
from typing import Any
from urllib.parse import unquote, urlsplit

from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import clock, jsutil, network
from app.screenshots.capture import config as cfg
from app.screenshots.capture.config import EffectiveConfig
from app.screenshots.capture.errors import (
    EvaluateTimeout,
    NavigationError,
    NavigationTimeoutError,
    PageBlockedError,
)
from app.screenshots.capture.types import CaptureRun, NavigationResult

logger = logging.getLogger(__name__)

NETWORKIDLE_STRATEGY = "networkidle→quiet"

# ---------------------------------------------------------------------------
# Navigation errors
# ---------------------------------------------------------------------------

# Failures no retry fixes, as Chromium net error names (NavigationError.net_error).
_FATAL_NET_ERRORS = frozenset(
    {"ERR_NAME_NOT_RESOLVED", "ERR_CONNECTION_REFUSED", "ERR_ADDRESS_UNREACHABLE"}
)
_FATAL_NET_ERROR_PREFIXES = ("ERR_CERT_", "ERR_SSL_")
_CHROMIUM_NET_ERROR = re.compile(r"\bnet::(ERR_[A-Z0-9_]+)")
# Firefox and WebKit word the same failures their own way.
_ENGINE_NET_ERRORS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\bNS_ERROR_UNKNOWN_HOST\b"), "ERR_NAME_NOT_RESOLVED"),
    (re.compile(r"\bNS_ERROR_CONNECTION_REFUSED\b"), "ERR_CONNECTION_REFUSED"),
    (
        re.compile(r"\b(?:SEC_ERROR_|MOZILLA_PKIX_ERROR_|SSL_ERROR_BAD_CERT)"),
        "ERR_CERT_INVALID",
    ),
    (re.compile(r"\bSSL_ERROR_[A-Z0-9_]+"), "ERR_SSL_PROTOCOL_ERROR"),
    (
        re.compile(
            r"Name or service not known|No address associated with hostname"
            r"|Could not resolve host"
        ),
        "ERR_NAME_NOT_RESOLVED",
    ),
    (re.compile(r"Connection refused"), "ERR_CONNECTION_REFUSED"),
    (re.compile(r"No route to host|Network is unreachable"), "ERR_ADDRESS_UNREACHABLE"),
    (re.compile(r"TLS certificate|SSL certificate"), "ERR_CERT_INVALID"),
    (re.compile(r"(?:TLS|SSL) handshake failed"), "ERR_SSL_PROTOCOL_ERROR"),
)
_CALL_PREFIX = re.compile(r"^\w+\.\w+:\s*")
# Errors that mean the page itself is gone: never swallowed while polling.
_CLOSED_MARKERS = (
    "has been closed",
    "target closed",
    "connection closed",
    "browser has disconnected",
)


def fatal_net_error(message: str) -> str | None:
    """The fatal network failure (a Chromium net error name) behind a
    navigation error *message* of any engine, or None when a retry might help
    (timeouts, resets, aborted requests) or the error is not a network one."""
    match = _CHROMIUM_NET_ERROR.search(message)
    if match:
        code = match.group(1)
        if code in _FATAL_NET_ERRORS or code.startswith(_FATAL_NET_ERROR_PREFIXES):
            return code
        return None
    for pattern, code in _ENGINE_NET_ERRORS:
        if pattern.search(message):
            return code
    return None


def _navigation_error(exc: PlaywrightError, url: str) -> NavigationError | None:
    message = str(getattr(exc, "message", "") or exc)
    code = fatal_net_error(message)
    if code is None:
        return None
    lines = message.strip().splitlines()
    detail = _CALL_PREFIX.sub("", lines[0]) if lines else ""
    text = f"Navigation failed: {code} at {url}"
    if detail and code not in detail:
        text += f" ({detail})"
    return NavigationError(net_error=code, message=text)


def _is_closed_error(exc: BaseException) -> bool:
    if type(exc).__name__ == "TargetClosedError":
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _CLOSED_MARKERS)


# ---------------------------------------------------------------------------
# Protection pages
# ---------------------------------------------------------------------------

# Signals of the current document for classify_challenge. Bounded: on a page
# with more than *maxElements* elements (never a protection page) only the
# title and the element count are read.
JS_DETECT_CHALLENGE = """(maxElements) => { /*qa:detect-challenge*/
    const signals = {
        title: '',
        text: '',
        elements: 0,
        challengeForm: false,
        challengeRunning: false,
        challengeOptions: false,
        challengeScript: false,
        incapsulaResource: false,
        sucuriScript: false,
    };
    try {
        const doc = document;
        signals.title = String(doc.title || '').slice(0, 200);
        signals.elements = doc.getElementsByTagName('*').length;
        if (signals.elements > maxElements) {
            return signals;
        }
        signals.challengeForm = !!doc.getElementById('challenge-form');
        signals.challengeRunning = !!doc.getElementById('cf-challenge-running');
        signals.challengeOptions = typeof window._cf_chl_opt !== 'undefined';
        for (const el of doc.querySelectorAll('script, iframe')) {
            const src = el.getAttribute('src') || '';
            if (src.includes('/cdn-cgi/challenge-platform/')) {
                signals.challengeScript = true;
            }
            if (src.includes('_Incapsula_Resource')) {
                signals.incapsulaResource = true;
            }
            if (!src && el.tagName === 'SCRIPT'
                    && (el.textContent || '').includes('sucuri_cloudproxy_js')) {
                signals.sucuriScript = true;
            }
        }
        const body = doc.body;
        signals.text = body
            ? String(body.innerText || body.textContent || '').slice(0, 5000)
            : '';
    } catch (_) {}
    return signals;
}"""


def _normalized(value: Any) -> str:
    return " ".join(str(value or "").split()).lower()


def classify_challenge(
    signals: Any, headers: Mapping[str, str] | None = None
) -> str | None:
    """The protection-page vendor that the main document's response *headers*
    and the page *signals* (JS_DETECT_CHALLENGE) point at, or None.

    The ``cf-mitigated: challenge`` header is conclusive on its own; page
    markers only count on a small page (config.CHALLENGE_PAGE_MAX_ELEMENTS),
    so an ordinary page that merely mentions "Access Denied" or a vendor is
    never taken for a protection page.
    """
    for name, value in (headers or {}).items():
        if str(name).lower() == "cf-mitigated" and _normalized(value) == "challenge":
            return "cloudflare"
    if not isinstance(signals, dict):
        return None
    elements = signals.get("elements") or 0
    if not isinstance(elements, int) or elements > cfg.CHALLENGE_PAGE_MAX_ELEMENTS:
        return None
    title = _normalized(signals.get("title"))
    text = _normalized(signals.get("text"))
    page = f"{title} {text}"
    if (
        title.startswith("just a moment")
        or "attention required! | cloudflare" in title
        or ("you have been blocked" in text and "cloudflare ray id" in text)
        or signals.get("challengeForm")
        or signals.get("challengeRunning")
        or signals.get("challengeOptions")
        or signals.get("challengeScript")
    ):
        return "cloudflare"
    if "sucuri website firewall" in page or signals.get("sucuriScript"):
        return "sucuri"
    if "your access to this site has been limited" in page:
        return "wordfence"
    if "incapsula incident id" in page or signals.get("incapsulaResource"):
        return "incapsula"
    if "access denied" in page and "reference #" in text:
        return "akamai"
    return None


# ---------------------------------------------------------------------------
# Stage
# ---------------------------------------------------------------------------


def wait_strategy(config: EffectiveConfig) -> str:
    """How navigation waits for the project's wait_until (recorded in meta)."""
    if config.wait_until == "networkidle":
        return NETWORKIDLE_STRATEGY
    if config.wait_until == "domcontentloaded":
        return "domcontentloaded"
    return "load"


def needs_reload(status: int | None) -> bool:
    """A main document answering 5xx or 429 is worth one reload in place."""
    return status is not None and (status == 429 or 500 <= status <= 599)


async def navigate(run: CaptureRun) -> NavigationResult:
    """Navigate ``run.page`` to ``run.url`` and wait for the page: one goto
    that waits for DOMContentLoaded (config.timeout), the load event (not for
    domcontentloaded projects; non-fatal, at most config.LOAD_WAIT_CAP_MS),
    the post-goto quiet (xhr/fetch included; longer for networkidle
    projects) and the protection-page check. Sets and returns ``run.nav``.

    Raises NavigationTimeoutError when the goto times out, NavigationError
    for a DNS / refused / certificate failure and PageBlockedError when a
    protection page does not pass in time; other Playwright errors propagate.
    """
    config = run.config
    run.report.set_meta(
        "navigation", requested_url=run.url, wait_strategy=wait_strategy(config)
    )
    with run.report.phase("goto"):
        try:
            response = await run.page.goto(
                run.url, wait_until="domcontentloaded", timeout=config.timeout
            )
        except PlaywrightTimeoutError as exc:
            raise NavigationTimeoutError(
                f"Navigation timed out after {config.timeout} ms: {run.url}"
            ) from exc
        except PlaywrightError as exc:
            error = _navigation_error(exc, run.url)
            if error is None:
                raise
            raise error from exc
    return await _settle(run, response, reloads=0, phases=True)


async def reload(run: CaptureRun, *, reason: str) -> NavigationResult:
    """Reload ``run.page`` in place — same context, so the cookies a
    protection page or the first load set are kept — and wait for it like
    navigate() does. ``reloads`` is the previous result's plus one; sets and
    returns ``run.nav``. Timed as the "reload" phase; raises like navigate()
    (NavigationTimeoutError when the reload times out)."""
    config = run.config
    previous = run.nav.reloads if run.nav is not None else 0
    logger.info("reloading %s (reason=%s)", run.url, reason)
    with run.report.phase("reload"):
        try:
            response = await run.page.reload(
                wait_until="domcontentloaded", timeout=config.timeout
            )
        except PlaywrightTimeoutError as exc:
            raise NavigationTimeoutError(
                f"Reload timed out after {config.timeout} ms: {run.url}"
            ) from exc
        except PlaywrightError as exc:
            error = _navigation_error(exc, run.url)
            if error is None:
                raise
            raise error from exc
        return await _settle(run, response, reloads=previous + 1, phases=False)


async def _settle(
    run: CaptureRun, response: Any, *, reloads: int, phases: bool
) -> NavigationResult:
    """Everything after the goto / reload: load event, quiet, protection page.
    With *phases* the waits are timed as "load_wait" / "quiet" (navigate);
    a reload is timed as a whole by its caller."""
    started = clock.now()
    # Main-document responses seen so far: a later one means the page moved
    # on to a new document (a JS redirect, a passed challenge).
    mark = run.net.document_responses
    goto_challenge = classify_challenge(None, _headers(response))
    load_fired = await _wait_for_load(run, phases=phases)
    await _quiet(run, phases=phases)

    passed: str | None = None
    vendor, _checked = await _detect_challenge(run, response, mark)
    polled_ms = 0.0
    if vendor is not None:
        limit_ms = _challenge_window_ms(run)
        logger.info(
            "protection page (%s) on %s, waiting up to %d ms for it to pass",
            vendor,
            run.url,
            limit_ms,
        )
        # Raised inside the phase: a failure's phase is the one the time went to.
        with _phase(run, "load_wait", phases):
            remaining, polled_ms = await _await_challenge(run, response, mark, limit_ms)
            if remaining is not None:
                status = _status(_document(run, response, mark))
                run.report.set_meta(
                    "navigation",
                    final_url=_current_url(run),
                    http_status=status,
                    challenge=remaining,
                    reloads=reloads,
                )
                logger.info(
                    "protection page (%s) did not pass on %s", remaining, run.url
                )
                raise PageBlockedError(vendor=remaining, status=status)
        passed = vendor
    elif goto_challenge is not None:
        # The protection page passed while the page loaded: the document
        # that answered the goto was one, the current one is not.
        passed = goto_challenge

    if passed is not None:
        waited_ms = round(max(polled_ms, (clock.now() - started) * 1000))
        logger.info(
            "protection page (%s) passed on %s after %d ms", passed, run.url, waited_ms
        )
        run.report.warn(
            "challenge_passed",
            f"A {passed} protection page passed after {waited_ms} ms",
            vendor=passed,
            waited_ms=waited_ms,
        )
        # The real page has just replaced the protection page: wait for it.
        load_fired = await _wait_for_load(run, phases=phases, new_document=True)
        await _quiet(run, phases=phases)

    status = _status(_document(run, response, mark))
    result = NavigationResult(
        requested_url=run.url,
        final_url=_current_url(run),
        status=status,
        load_event_fired=load_fired,
        wait_strategy=wait_strategy(run.config),
        challenge=passed,
        reloads=reloads,
        needs_reload=needs_reload(status),
    )
    run.nav = result
    run.report.set_meta(
        "navigation",
        requested_url=result.requested_url,
        final_url=result.final_url,
        http_status=result.status,
        wait_strategy=result.wait_strategy,
        load_event=result.load_event_fired,
        reloads=result.reloads,
        challenge=result.challenge,
    )
    return result


def _phase(run: CaptureRun, name: str, enabled: bool) -> AbstractContextManager[Any]:
    return run.report.phase(name) if enabled else contextlib.nullcontext()


def _bounded_ms(run: CaptureRun, limit_ms: float) -> int:
    """*limit_ms*, cut to what is left of the attempt's deadline."""
    remaining_s = run.deadline.remaining_s()
    if math.isfinite(remaining_s):
        limit_ms = min(limit_ms, remaining_s * 1000)
    return max(0, int(limit_ms))


def _load_wait_ms(run: CaptureRun) -> int:
    limit = cfg.LOAD_WAIT_CAP_MS
    if run.config.timeout > 0:
        limit = min(limit, run.config.timeout)
    return _bounded_ms(run, limit)


def _challenge_window_ms(run: CaptureRun) -> int:
    return _bounded_ms(run, cfg.CHALLENGE_WAIT_MS)


async def _wait_for_state(run: CaptureRun, state: str, timeout_ms: int) -> bool:
    """Non-fatal page.wait_for_load_state: False when it timed out (or no
    time is left — Playwright reads a 0 timeout as "no timeout")."""
    if timeout_ms <= 0:
        return False
    try:
        await run.page.wait_for_load_state(state, timeout=timeout_ms)
    except PlaywrightTimeoutError:
        return False
    return True


async def _wait_for_load(
    run: CaptureRun, *, phases: bool, new_document: bool = False
) -> bool:
    """Wait for the current document's load event; whether it fired.

    domcontentloaded projects never wait for it (False, so readiness accepts
    readyState "interactive") — only for DOMContentLoaded of a document that
    has just replaced a protection page (*new_document*).
    """
    timeout_ms = _load_wait_ms(run)
    with _phase(run, "load_wait", phases):
        if run.config.wait_until == "domcontentloaded":
            if new_document:
                await _wait_for_state(run, "domcontentloaded", timeout_ms)
            return False
        fired = await _wait_for_state(run, "load", timeout_ms)
    if not fired:
        logger.info(
            "load event did not fire within %d ms on %s, continuing",
            timeout_ms,
            run.url,
        )
    return fired


async def _quiet(run: CaptureRun, *, phases: bool) -> None:
    """Let the page's own post-load requests (xhr/fetch included) settle."""
    if run.config.wait_until == "networkidle":
        quiet_ms, cap_ms = cfg.NETWORKIDLE_QUIET_MS, cfg.NETWORKIDLE_QUIET_CAP_MS
    else:
        quiet_ms, cap_ms = cfg.POST_GOTO_QUIET_MS, cfg.POST_GOTO_QUIET_CAP_MS
    with _phase(run, "quiet", phases):
        await network.wait_for_quiet(
            run.page,
            run.tracker,
            min_ms=0,
            quiet_ms=quiet_ms,
            max_ms=cap_ms,
            include_data=True,
        )


async def _detect_challenge(
    run: CaptureRun, response: Any, mark: int
) -> tuple[str | None, bool]:
    """``(vendor, checked)`` for the current document. *checked* is False
    when the page script could not run — a navigation kept replacing the
    document (one re-check after its DOMContentLoaded) — and only the
    response headers were judged."""
    for attempt in range(2):
        try:
            signals = await jsutil.evaluate(
                run.page,
                JS_DETECT_CHALLENGE,
                cfg.CHALLENGE_PAGE_MAX_ELEMENTS,
                timeout_ms=cfg.NAVIGATION_SCRIPT_TIMEOUT_MS,
            )
        except EvaluateTimeout:
            break
        except Exception as exc:
            if _is_closed_error(exc):
                raise
            logger.debug("challenge check failed on %s: %s", run.url, exc)
            if attempt == 0:
                await _wait_for_state(
                    run,
                    "domcontentloaded",
                    _bounded_ms(run, cfg.NAVIGATION_RECHECK_WAIT_MS),
                )
            continue
        document = _document(run, response, mark)
        return classify_challenge(signals, _headers(document)), True
    return classify_challenge(None, _headers(_document(run, response, mark))), False


async def _await_challenge(
    run: CaptureRun, response: Any, mark: int, limit_ms: int
) -> tuple[str | None, float]:
    """Poll every config.CHALLENGE_POLL_MS, for at most *limit_ms*, until the
    protection page is gone. Returns ``(vendor still showing or None,
    waited ms)``; a navigation that keeps the page from being checked at the
    end of the window counts as passed."""
    started = clock.now()
    waited = 0.0
    vendor: str | None = None
    checked = True
    while True:
        elapsed = max(waited, (clock.now() - started) * 1000)
        if elapsed >= limit_ms:
            break
        step = min(cfg.CHALLENGE_POLL_MS, limit_ms - elapsed)
        await run.page.wait_for_timeout(step)
        waited += step
        vendor, checked = await _detect_challenge(run, response, mark)
        if vendor is None and checked:
            break
    elapsed = max(waited, (clock.now() - started) * 1000)
    if not checked:
        return None, elapsed
    return vendor, elapsed


def _document(run: CaptureRun, response: Any, mark: int) -> Any:
    """The main document's latest response: one the page navigated to after
    *mark*, else the goto / reload *response*."""
    if run.net.document_responses > mark and run.net.document_response is not None:
        return run.net.document_response
    return response


def _headers(response: Any) -> dict[str, str]:
    headers = getattr(response, "headers", None)
    if not isinstance(headers, Mapping):
        return {}
    return {str(name).lower(): str(value) for name, value in headers.items()}


def _status(response: Any) -> int | None:
    status = getattr(response, "status", None)
    if isinstance(status, bool) or not isinstance(status, int):
        return None
    return status


def _current_url(run: CaptureRun) -> str:
    url = getattr(run.page, "url", None)
    return url if isinstance(url, str) and url else run.url


# ---------------------------------------------------------------------------
# Status warnings
# ---------------------------------------------------------------------------

_DEFAULT_PORTS = {"http": 80, "https": 443}
_LOGIN_PATHS = ("/login", "/wp-login.php", "/signin", "/sign-in", "/account/login")


def _host(url: str) -> str:
    try:
        host = urlsplit(url.strip()).hostname or ""
    except ValueError:
        return ""
    try:
        return host.encode("idna").decode("ascii")
    except UnicodeError:
        return host


def _comparable(url: str) -> tuple[str, str, int | None, str, str]:
    parts = urlsplit(url.strip())
    scheme = parts.scheme.lower()
    try:
        port = parts.port
    except ValueError:
        port = None
    if port == _DEFAULT_PORTS.get(scheme):
        port = None
    path = unquote(parts.path).rstrip("/")
    return scheme, _host(url), port, path, unquote(parts.query)


def same_url(requested: str, final: str) -> bool:
    """Whether *final* is *requested*, ignoring the fragment, a trailing
    slash, letter case of scheme and host, a default port and
    percent-encoding."""
    try:
        return _comparable(requested) == _comparable(final)
    except ValueError:
        return requested == final


def same_site(requested: str, final: str) -> bool:
    """Same host, ``www.`` or not."""
    return _host(requested).removeprefix("www.") == _host(final).removeprefix("www.")


def is_login_url(url: str) -> bool:
    """A login page: /login, /wp-login.php, /signin, /account/login (also
    under a prefix such as /en/login) or a WordPress ``redirect_to=`` query."""
    try:
        parts = urlsplit(url.strip())
    except ValueError:
        return False
    path = unquote(parts.path).lower().rstrip("/")
    if any(path.endswith(login) for login in _LOGIN_PATHS):
        return True
    return "redirect_to=" in parts.query.lower()


def report_status(run: CaptureRun) -> None:
    """Warn about the main document of ``run.nav`` (the latest navigation):
    ``http_error`` for HTTP status >= 400, ``redirected`` when the final URL is
    not the requested one (same_url) — a warning when it left the site or
    landed on a login page, info otherwise. A protection page never gets
    here: it raised PageBlockedError. The pipeline calls this once per
    successful attempt, after any reload."""
    nav = run.nav
    if nav is None:
        return
    if nav.status is not None and nav.status >= 400:
        run.report.warn(
            "http_error",
            f"The page answered with HTTP {nav.status}",
            status=nav.status,
        )
    if same_url(nav.requested_url, nav.final_url):
        return
    if is_login_url(nav.final_url):
        level = "warning"
        message = f"Redirected to a login page: {nav.final_url}"
    elif not same_site(nav.requested_url, nav.final_url):
        level = "warning"
        message = f"Redirected to another site: {nav.final_url}"
    else:
        level = "info"
        message = f"Redirected to {nav.final_url}"
    run.report.warn("redirected", message, level=level, final_url=nav.final_url)
