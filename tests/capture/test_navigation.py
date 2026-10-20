from collections.abc import Callable
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import navigation
from app.screenshots.capture.errors import (
    NavigationError,
    NavigationTimeoutError,
    PageBlockedError,
)
from app.screenshots.capture.types import CaptureRun, Deadline, NavigationResult
from tests.capture.fakes import (
    FakePage,
    FakeRequest,
    FakeResponse,
    make_config,
    make_run,
    results_sequence,
)

URL = "https://example.com/"
CHALLENGE = {
    "title": "Just a moment...",
    "text": "Verifying you are human. This may take a few seconds.",
    "elements": 24,
}
ORDINARY = {"title": "Example", "text": "Welcome to Example", "elements": 180}
NAVIGATED = PlaywrightError(
    "Page.evaluate: Execution context was destroyed, most likely because of a "
    "navigation"
)


class ReloadTimeoutPage(FakePage):
    async def reload(self, **kwargs: Any) -> FakeResponse:
        self.reload_calls.append(kwargs)
        raise PlaywrightTimeoutError(f"Timeout {kwargs.get('timeout')}ms exceeded")


def on_challenge_poll(page: FakePage, poll: int, action: Callable[[], object]) -> None:
    """Run *action* at the *poll*-th challenge poll (a 500 ms wait)."""
    polls = [0]

    def on_wait(_count: int) -> None:
        if page.waits[-1] == 500:
            polls[0] += 1
            if polls[0] == poll:
                action()

    page.on_wait = on_wait


def challenge_polls(page: FakePage) -> list[float]:
    return [wait for wait in page.waits if wait == 500]


def document(status: int = 200, url: str = URL, **headers: str) -> FakeResponse:
    return FakeResponse(status=status, url=url, headers=headers)


# ---------------------------------------------------------------------------
# navigate: goto, load wait, quiet
# ---------------------------------------------------------------------------


async def test_navigate_runs_one_goto_that_waits_for_domcontentloaded() -> None:
    page = FakePage(response=document(200))
    run = make_run(page=page)

    result = await navigation.navigate(run)

    assert page.goto_calls == [
        (URL, {"wait_until": "domcontentloaded", "timeout": 1000})
    ]
    assert page.load_state_calls == [("load", {"timeout": 1000})]
    assert result == NavigationResult(
        requested_url=URL,
        final_url=URL,
        status=200,
        load_event_fired=True,
        wait_strategy="load",
        challenge=None,
        reloads=0,
        needs_reload=False,
    )
    assert result.allow_interactive is False
    assert run.nav is result
    assert page.evaluated == ["detect-challenge"]
    assert run.report.warnings == []
    assert {"goto", "load_wait", "quiet"} <= set(run.report.timings_ms)
    assert run.report.meta["navigation"] == {
        "requested_url": URL,
        "final_url": URL,
        "http_status": 200,
        "wait_strategy": "load",
        "load_event": True,
        "reloads": 0,
        "challenge": None,
    }


@pytest.mark.parametrize("browser", ["chromium", "firefox", "webkit", "safari"])
async def test_navigate_treats_every_engine_alike(browser: str) -> None:
    page = FakePage()

    result = await navigation.navigate(
        make_run(page=page, config=make_config(browser=browser))
    )

    assert [kwargs["wait_until"] for _, kwargs in page.goto_calls] == [
        "domcontentloaded"
    ]
    assert page.load_state_calls == [("load", {"timeout": 1000})]
    assert result.load_event_fired is True


async def test_navigate_caps_the_load_wait() -> None:
    page = FakePage()

    await navigation.navigate(make_run(page=page, config=make_config(timeout=90_000)))

    assert page.goto_calls[0][1]["timeout"] == 90_000
    assert page.load_state_calls == [("load", {"timeout": 30_000})]


async def test_navigate_load_wait_timeout_is_not_fatal() -> None:
    page = FakePage(load_state_error=PlaywrightTimeoutError("Timeout 1000ms exceeded"))
    run = make_run(page=page)

    result = await navigation.navigate(run)

    assert len(page.goto_calls) == 1
    assert result.load_event_fired is False
    assert result.allow_interactive is True
    assert result.wait_strategy == "load"
    assert run.report.meta["navigation"]["load_event"] is False


async def test_navigate_skips_the_load_wait_for_domcontentloaded_projects() -> None:
    page = FakePage()

    result = await navigation.navigate(
        make_run(page=page, config=make_config(wait_until="domcontentloaded"))
    )

    assert page.load_state_calls == []
    assert result.load_event_fired is False
    assert result.allow_interactive is True
    assert result.wait_strategy == "domcontentloaded"


async def test_navigate_load_wait_is_bounded_by_the_deadline() -> None:
    page = FakePage()
    run = make_run(page=page, deadline=Deadline(budget_s=0.4, started_at=100.0))

    with patch("app.screenshots.capture.clock.now", return_value=100.0):
        await navigation.navigate(run)

    assert page.load_state_calls == [("load", {"timeout": 400})]


@pytest.mark.parametrize(
    ("wait_until", "quiet_ms", "max_ms", "strategy"),
    [
        ("load", 500, 5000, "load"),
        ("domcontentloaded", 500, 5000, "domcontentloaded"),
        ("networkidle", 1000, 15_000, "networkidle→quiet"),
    ],
)
async def test_navigate_post_goto_quiet(
    wait_until: str, quiet_ms: int, max_ms: int, strategy: str
) -> None:
    page = FakePage()
    quiet = AsyncMock()

    with patch("app.screenshots.capture.network.wait_for_quiet", quiet):
        result = await navigation.navigate(
            make_run(page=page, config=make_config(wait_until=wait_until))
        )

    quiet.assert_awaited_once()
    assert quiet.await_args.kwargs == {
        "min_ms": 0,
        "quiet_ms": quiet_ms,
        "max_ms": max_ms,
        "include_data": True,
    }
    assert result.wait_strategy == strategy
    # Playwright's own networkidle is never used.
    assert [kwargs["wait_until"] for _, kwargs in page.goto_calls] == [
        "domcontentloaded"
    ]
    assert all(state != "networkidle" for state, _ in page.load_state_calls)


async def test_navigate_waits_for_post_load_data_requests() -> None:
    page = FakePage()
    run = make_run(page=page)
    fetch = FakeRequest(resource_type="fetch")
    run.tracker.on_request(fetch)
    page.on_wait = lambda polls: polls == 3 and run.tracker.on_request_done(fetch)

    await navigation.navigate(run)

    # xhr/fetch count right after goto: busy for 300 ms, then quiet 500 ms.
    assert sum(page.waits) == 800


async def test_navigate_reports_the_final_url_and_status() -> None:
    page = FakePage(
        url="https://example.com/en/",
        response=document(404, "https://example.com/en/"),
    )

    result = await navigation.navigate(make_run(page=page))

    assert result.final_url == "https://example.com/en/"
    assert result.status == 404
    assert result.needs_reload is False


async def test_navigate_tolerates_a_missing_response() -> None:
    page = FakePage(url="")
    page.response = None  # e.g. same-document navigation

    result = await navigation.navigate(make_run(page=page))

    assert result.status is None
    assert result.final_url == URL
    assert result.needs_reload is False


@pytest.mark.parametrize(
    ("status", "expected"),
    [(500, True), (502, True), (503, True), (429, True), (404, False), (200, False)],
)
async def test_navigate_asks_for_a_reload_on_5xx_and_429(
    status: int, expected: bool
) -> None:
    page = FakePage(response=document(status))

    result = await navigation.navigate(make_run(page=page))

    assert result.status == status
    assert result.needs_reload is expected


async def test_navigate_takes_the_status_of_the_document_the_page_moved_to() -> None:
    page = FakePage(response=document(200))
    run = make_run(page=page)

    def js_redirect(polls: int) -> None:
        if polls == 1:
            page.url = "https://example.com/gone"
            page.emit("response", document(404, "https://example.com/gone"))

    page.on_wait = js_redirect

    result = await navigation.navigate(run)

    assert result.final_url == "https://example.com/gone"
    assert result.status == 404


# ---------------------------------------------------------------------------
# navigate: failures
# ---------------------------------------------------------------------------


async def test_navigate_goto_timeout_raises_navigation_timeout_error() -> None:
    page = FakePage(goto_timeout_levels={"domcontentloaded"})
    run = make_run(page=page)

    with pytest.raises(NavigationTimeoutError) as exc_info:
        await navigation.navigate(run)

    assert len(page.goto_calls) == 1
    assert str(exc_info.value) == (
        "Navigation timed out after 1000 ms: https://example.com/"
    )
    assert not isinstance(exc_info.value, TimeoutError)
    assert isinstance(exc_info.value.__cause__, PlaywrightTimeoutError)
    assert page.load_state_calls == []
    assert run.report.current_phase == "goto"
    assert run.report.meta["navigation"] == {
        "requested_url": URL,
        "wait_strategy": "load",
    }


@pytest.mark.parametrize(
    ("message", "net_error"),
    [
        (
            "Page.goto: net::ERR_NAME_NOT_RESOLVED at https://example.com/",
            "ERR_NAME_NOT_RESOLVED",
        ),
        (
            "Page.goto: net::ERR_CONNECTION_REFUSED at https://example.com/",
            "ERR_CONNECTION_REFUSED",
        ),
        (
            "Page.goto: net::ERR_ADDRESS_UNREACHABLE at https://example.com/",
            "ERR_ADDRESS_UNREACHABLE",
        ),
        (
            "Page.goto: net::ERR_CERT_AUTHORITY_INVALID at https://example.com/",
            "ERR_CERT_AUTHORITY_INVALID",
        ),
        (
            "Page.goto: net::ERR_SSL_PROTOCOL_ERROR at https://example.com/",
            "ERR_SSL_PROTOCOL_ERROR",
        ),
        # Firefox
        ("Page.goto: NS_ERROR_UNKNOWN_HOST", "ERR_NAME_NOT_RESOLVED"),
        ("Page.goto: NS_ERROR_CONNECTION_REFUSED", "ERR_CONNECTION_REFUSED"),
        ("Page.goto: SEC_ERROR_UNKNOWN_ISSUER", "ERR_CERT_INVALID"),
        ("Page.goto: SSL_ERROR_UNKNOWN", "ERR_SSL_PROTOCOL_ERROR"),
        # WebKit
        (
            "Page.goto: Error resolving “example.com”: Name or service not known",
            "ERR_NAME_NOT_RESOLVED",
        ),
        (
            "Page.goto: Could not connect to 10.0.0.1: Connection refused",
            "ERR_CONNECTION_REFUSED",
        ),
        ("Page.goto: Unacceptable TLS certificate", "ERR_CERT_INVALID"),
    ],
)
async def test_navigate_maps_fatal_network_errors(message: str, net_error: str) -> None:
    page = FakePage(goto_error=PlaywrightError(message))

    with pytest.raises(NavigationError) as exc_info:
        await navigation.navigate(make_run(page=page))

    assert exc_info.value.net_error == net_error
    assert str(exc_info.value).startswith(f"Navigation failed: {net_error} at {URL}")
    assert len(page.goto_calls) == 1


async def test_navigation_error_message_keeps_the_engine_detail() -> None:
    page = FakePage(goto_error=PlaywrightError("Page.goto: NS_ERROR_UNKNOWN_HOST"))

    with pytest.raises(NavigationError) as exc_info:
        await navigation.navigate(make_run(page=page))

    assert str(exc_info.value) == (
        "Navigation failed: ERR_NAME_NOT_RESOLVED at https://example.com/ "
        "(NS_ERROR_UNKNOWN_HOST)"
    )


@pytest.mark.parametrize(
    "message",
    [
        "Page.goto: net::ERR_CONNECTION_RESET at https://example.com/",
        "Page.goto: net::ERR_ABORTED at https://example.com/",
        "Page.goto: NS_ERROR_NET_INTERRUPT",
        "Target page, context or browser has been closed",
    ],
)
async def test_navigate_lets_retryable_errors_through(message: str) -> None:
    error = PlaywrightError(message)
    page = FakePage(goto_error=error)

    with pytest.raises(PlaywrightError) as exc_info:
        await navigation.navigate(make_run(page=page))

    assert exc_info.value is error


# ---------------------------------------------------------------------------
# Protection pages
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("signals", "headers", "vendor"),
    [
        (None, {"cf-mitigated": "challenge"}, "cloudflare"),
        (None, {"CF-Mitigated": "Challenge"}, "cloudflare"),
        ({"elements": 9000}, {"cf-mitigated": "challenge"}, "cloudflare"),
        (CHALLENGE, None, "cloudflare"),
        ({"title": "Just a moment…", "elements": 20}, None, "cloudflare"),
        ({"title": "", "elements": 20, "challengeForm": True}, None, "cloudflare"),
        ({"title": "", "elements": 20, "challengeRunning": True}, None, "cloudflare"),
        ({"title": "", "elements": 20, "challengeScript": True}, None, "cloudflare"),
        ({"title": "", "elements": 20, "challengeOptions": True}, None, "cloudflare"),
        (
            {"title": "Attention Required! | Cloudflare", "elements": 60},
            None,
            "cloudflare",
        ),
        (
            {"title": "Sucuri WebSite Firewall - Access Denied", "elements": 90},
            None,
            "sucuri",
        ),
        ({"title": "", "elements": 12, "sucuriScript": True}, None, "sucuri"),
        (
            {
                "title": "",
                "text": "Your access to this site has been limited by the site owner",
                "elements": 70,
            },
            None,
            "wordfence",
        ),
        (
            {
                "title": "",
                "text": "Request unsuccessful. Incapsula incident ID: 1-2",
                "elements": 8,
            },
            None,
            "incapsula",
        ),
        ({"title": "", "elements": 8, "incapsulaResource": True}, None, "incapsula"),
        (
            {
                "title": "Access Denied",
                "text": "Access Denied\nYou don't have permission to access "
                '"http://example.com/" on this server.\nReference #18.4a2d',
                "elements": 6,
            },
            None,
            "akamai",
        ),
    ],
)
def test_classify_challenge_recognizes_protection_pages(
    signals: Any, headers: dict[str, str] | None, vendor: str
) -> None:
    assert navigation.classify_challenge(signals, headers) == vendor


@pytest.mark.parametrize(
    "signals",
    [
        None,
        "not a dict",
        ORDINARY,
        {"title": "Access Denied", "text": "Please sign in", "elements": 40},
        # Markers only count on a small page: a long article is never one.
        {**CHALLENGE, "elements": 2500},
        {
            "title": "How the Sucuri WebSite Firewall works",
            "text": "Reference #1",
            "elements": 1800,
        },
    ],
)
def test_classify_challenge_leaves_ordinary_pages_alone(signals: Any) -> None:
    assert navigation.classify_challenge(signals, {"server": "cloudflare"}) is None


async def test_navigate_waits_for_a_challenge_to_pass() -> None:
    page = FakePage(response=document(403, **{"cf-mitigated": "challenge"}))
    page.script_results["detect-challenge"] = CHALLENGE
    run = make_run(page=page)

    def passed() -> None:
        # The challenge reloads the page, which is the real one now.
        page.script_results["detect-challenge"] = ORDINARY
        page.emit("response", document(200))

    on_challenge_poll(page, 3, passed)

    result = await navigation.navigate(run)

    assert challenge_polls(page) == [500, 500, 500]
    assert result.challenge == "cloudflare"
    assert result.status == 200
    assert result.needs_reload is False
    warning = run.report.warning("challenge_passed")
    assert warning is not None
    assert warning.level == "info"
    assert warning.details["vendor"] == "cloudflare"
    assert warning.details["waited_ms"] >= 1500
    assert run.report.meta["navigation"]["challenge"] == "cloudflare"
    # The real page gets its own load wait.
    assert page.load_state_calls == [("load", {"timeout": 1000})] * 2
    assert len(page.goto_calls) == 1


async def test_navigate_challenge_by_header_passes_with_the_next_document() -> None:
    # No page marker at all (e.g. a localized title): the header decides.
    page = FakePage(response=document(403, **{"cf-mitigated": "challenge"}))
    page.script_results["detect-challenge"] = ORDINARY
    on_challenge_poll(page, 2, lambda: page.emit("response", document(200)))
    run = make_run(page=page)

    result = await navigation.navigate(run)

    assert challenge_polls(page) == [500, 500]
    assert (result.challenge, result.status) == ("cloudflare", 200)


async def test_navigate_notices_a_challenge_that_passed_during_the_quiet() -> None:
    page = FakePage(response=document(403, **{"cf-mitigated": "challenge"}))
    page.script_results["detect-challenge"] = ORDINARY
    page.on_wait = lambda polls: polls == 2 and page.emit("response", document(200))
    run = make_run(page=page)

    result = await navigation.navigate(run)

    assert challenge_polls(page) == []
    assert (result.challenge, result.status) == ("cloudflare", 200)
    assert run.report.warning("challenge_passed") is not None


async def test_navigate_raises_page_blocked_when_the_challenge_stays() -> None:
    page = FakePage(response=document(403, **{"cf-mitigated": "challenge"}))
    page.script_results["detect-challenge"] = CHALLENGE
    run = make_run(page=page)

    with pytest.raises(PageBlockedError) as exc_info:
        await navigation.navigate(run)

    assert (exc_info.value.vendor, exc_info.value.status) == ("cloudflare", 403)
    assert sum(challenge_polls(page)) == 15_000
    assert run.report.warnings == []
    assert run.nav is None
    assert run.report.current_phase == "load_wait"
    meta = run.report.meta["navigation"]
    assert (meta["challenge"], meta["http_status"]) == ("cloudflare", 403)


async def test_challenge_wait_is_bounded_by_the_deadline() -> None:
    page = FakePage(response=document(503))
    page.script_results["detect-challenge"] = CHALLENGE
    run = make_run(page=page, deadline=Deadline(budget_s=2.0, started_at=100.0))

    with (
        patch("app.screenshots.capture.clock.now", return_value=100.0),
        pytest.raises(PageBlockedError),
    ):
        await navigation.navigate(run)

    assert challenge_polls(page) == [500, 500, 500, 500]


async def test_a_navigation_during_the_challenge_check_is_rechecked() -> None:
    page = FakePage(response=document(503))
    page.script_results["detect-challenge"] = results_sequence(
        CHALLENGE, NAVIGATED, ORDINARY
    )
    run = make_run(page=page)

    result = await navigation.navigate(run)

    # Re-checked right after the new document's DOMContentLoaded.
    assert challenge_polls(page) == [500]
    assert ("domcontentloaded", {"timeout": 5000}) in page.load_state_calls
    assert result.challenge == "cloudflare"
    assert run.report.warning("challenge_passed") is not None


async def test_a_challenge_left_mid_navigation_counts_as_passed() -> None:
    page = FakePage(response=document(503))
    page.script_results["detect-challenge"] = results_sequence(CHALLENGE, NAVIGATED)
    run = make_run(page=page)

    result = await navigation.navigate(run)

    assert sum(challenge_polls(page)) == 15_000
    assert result.challenge == "cloudflare"


async def test_a_closed_page_during_the_challenge_check_propagates() -> None:
    closed = PlaywrightError("Target page, context or browser has been closed")
    page = FakePage(response=document(503))
    page.script_results["detect-challenge"] = results_sequence(CHALLENGE, closed)

    with pytest.raises(PlaywrightError) as exc_info:
        await navigation.navigate(make_run(page=page))

    assert exc_info.value is closed


async def test_navigate_ignores_a_challenge_check_that_cannot_run() -> None:
    page = FakePage()
    page.script_results["detect-challenge"] = NAVIGATED

    result = await navigation.navigate(make_run(page=page))

    assert result.challenge is None
    assert page.evaluated == ["detect-challenge", "detect-challenge"]


# ---------------------------------------------------------------------------
# reload
# ---------------------------------------------------------------------------


async def test_reload_reloads_in_place() -> None:
    page = FakePage(response=document(503))
    run = make_run(page=page)
    first = await navigation.navigate(run)
    page.response = document(200)

    result = await navigation.reload(run, reason="http_status")

    assert first.needs_reload is True
    assert page.reload_calls == [{"wait_until": "domcontentloaded", "timeout": 1000}]
    assert len(page.goto_calls) == 1
    assert (result.status, result.needs_reload, result.reloads) == (200, False, 1)
    assert run.nav is result
    assert "reload" in run.report.timings_ms
    assert page.load_state_calls[-1] == ("load", {"timeout": 1000})
    assert run.report.meta["navigation"]["reloads"] == 1
    assert run.report.meta["navigation"]["http_status"] == 200

    again = await navigation.reload(run, reason="styles_missing")

    assert again.reloads == 2


async def test_reload_is_timed_as_one_phase() -> None:
    run = make_run()

    result = await navigation.reload(run, reason="styles_missing")

    assert result.reloads == 1
    assert set(run.report.timings_ms) == {"reload"}


async def test_reload_timeout_raises_navigation_timeout_error() -> None:
    page = ReloadTimeoutPage()
    run = make_run(page=page)

    with pytest.raises(NavigationTimeoutError) as exc_info:
        await navigation.reload(run, reason="assets_missing")

    assert str(exc_info.value) == "Reload timed out after 1000 ms: https://example.com/"
    assert len(page.reload_calls) == 1
    assert run.report.current_phase == "reload"


async def test_reload_handles_a_protection_page_too() -> None:
    page = FakePage(response=document(403, **{"cf-mitigated": "challenge"}))
    page.script_results["detect-challenge"] = CHALLENGE

    with pytest.raises(PageBlockedError):
        await navigation.reload(make_run(page=page), reason="blocked")


# ---------------------------------------------------------------------------
# report_status
# ---------------------------------------------------------------------------


def _status_run(
    status: int | None = 200, requested: str = URL, final: str | None = None
) -> CaptureRun:
    run = make_run()
    run.nav = NavigationResult(
        requested_url=requested, final_url=final or requested, status=status
    )
    return run


def test_report_status_is_quiet_for_a_plain_ok_page() -> None:
    run = _status_run(200)

    navigation.report_status(run)

    assert run.report.warnings == []


def test_report_status_without_navigation_does_nothing() -> None:
    run = make_run()

    navigation.report_status(run)

    assert run.report.warnings == []


@pytest.mark.parametrize("status", [400, 401, 403, 404, 410, 429, 500, 503])
def test_report_status_warns_about_http_errors(status: int) -> None:
    run = _status_run(status)

    navigation.report_status(run)

    warning = run.report.warning("http_error")
    assert warning is not None
    assert warning.level == "warning"
    assert warning.details["status"] == status
    assert str(status) in warning.message
    assert run.report.warning_codes() == ["http_error"]


@pytest.mark.parametrize("status", [None, 200, 204, 304])
def test_report_status_ignores_non_error_statuses(status: int | None) -> None:
    run = _status_run(status)

    navigation.report_status(run)

    assert run.report.warnings == []


@pytest.mark.parametrize(
    ("requested", "final"),
    [
        ("https://example.com", "https://example.com/"),
        ("https://example.com/a/", "https://example.com/a"),
        ("https://example.com/a", "https://example.com/a#top"),
        ("https://Example.COM/a", "https://example.com/a"),
        ("https://example.com:443/a", "https://example.com/a"),
        ("https://example.com/über", "https://example.com/%C3%BCber"),
        ("https://bücher.example/", "https://xn--bcher-kva.example/"),
    ],
)
def test_report_status_ignores_cosmetic_url_differences(
    requested: str, final: str
) -> None:
    run = _status_run(200, requested, final)

    navigation.report_status(run)

    assert run.report.warnings == []


@pytest.mark.parametrize(
    ("requested", "final", "level"),
    [
        ("https://example.com/a", "https://example.com/b", "info"),
        ("https://example.com/a?x=1", "https://example.com/a?x=2", "info"),
        ("http://example.com/", "https://example.com/", "info"),
        ("https://example.com/", "https://www.example.com/", "info"),
        ("https://www.example.com/", "https://example.com/en/", "info"),
        ("https://example.com/", "https://example.org/", "warning"),
        ("https://example.com/", "https://shop.example.com/", "warning"),
        ("https://example.com/shop", "https://example.com/login", "warning"),
        ("https://example.com/", "https://example.com/wp-login.php", "warning"),
        ("https://example.com/", "https://example.com/en/signin/", "warning"),
        ("https://example.com/", "https://example.com/account/login?next=/", "warning"),
        (
            "https://example.com/members",
            "https://example.com/?redirect_to=%2Fmembers",
            "warning",
        ),
    ],
)
def test_report_status_warns_about_redirects(
    requested: str, final: str, level: str
) -> None:
    run = _status_run(200, requested, final)

    navigation.report_status(run)

    warning = run.report.warning("redirected")
    assert warning is not None
    assert warning.level == level
    assert warning.details["final_url"] == final
    assert final in warning.message
    assert run.report.warning_codes() == ["redirected"]


def test_report_status_reports_an_error_after_a_redirect() -> None:
    run = _status_run(404, URL, "https://example.com/missing")

    navigation.report_status(run)

    assert run.report.warning_codes() == ["http_error", "redirected"]


def test_report_status_uses_the_latest_navigation() -> None:
    run = _status_run(503)
    run.nav = NavigationResult(requested_url=URL, final_url=URL, status=200, reloads=1)

    navigation.report_status(run)

    assert run.report.warnings == []
