import logging

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from app.screenshots.capture import navigation
from tests.capture.fakes import (
    FakePage,
    FakeRequest,
    FakeResponse,
    make_config,
    make_run,
)


def test_navigation_wait_until_chain_cascades_to_domcontentloaded() -> None:
    def chain(browser: str, wait_until: str) -> list[str]:
        return navigation.navigation_wait_until_chain(
            make_config(browser=browser, wait_until=wait_until)
        )

    assert chain("chromium", "networkidle") == [
        "networkidle",
        "load",
        "domcontentloaded",
    ]
    assert chain("chromium", "load") == ["load", "domcontentloaded"]
    assert chain("chromium", "domcontentloaded") == ["domcontentloaded"]
    # WebKit downgrades `load` to `domcontentloaded`, so the chain has no
    # redundant `load` step.
    assert chain("webkit", "load") == ["domcontentloaded"]
    assert chain("webkit", "networkidle") == ["networkidle", "domcontentloaded"]


def test_navigation_wait_until_downgrades_load_for_webkit_only() -> None:
    assert navigation.navigation_wait_until(make_config(browser="webkit")) == (
        "domcontentloaded"
    )
    assert navigation.navigation_wait_until(make_config(browser="Safari")) == (
        "domcontentloaded"
    )
    assert navigation.navigation_wait_until(make_config(browser="chrome")) == "load"


async def test_navigate_uses_the_configured_wait_until_and_timeout() -> None:
    page = FakePage(response=FakeResponse(status=200, url="https://example.com/"))
    run = make_run(page=page)

    result = await navigation.navigate(run)

    assert page.goto_calls == [
        ("https://example.com/", {"wait_until": "load", "timeout": 1000})
    ]
    assert result.requested_url == "https://example.com/"
    assert result.final_url == "https://example.com/"
    assert result.status == 200
    assert result.load_event_fired is True
    assert result.wait_strategy == "load"
    assert result.allow_interactive is False
    assert (result.challenge, result.reloads) == (None, 0)
    assert {"goto", "quiet"} <= set(run.report.timings_ms)


async def test_navigate_reports_the_final_url_and_status() -> None:
    page = FakePage(
        url="https://example.com/en/",
        response=FakeResponse(status=404, url="https://example.com/en/"),
    )

    result = await navigation.navigate(make_run(page=page))

    assert result.final_url == "https://example.com/en/"
    assert result.status == 404


async def test_navigate_tolerates_a_missing_response() -> None:
    page = FakePage(url="")
    page.response = None  # e.g. same-document navigation

    result = await navigation.navigate(make_run(page=page))

    assert result.status is None
    assert result.final_url == "https://example.com/"


async def test_navigate_uses_domcontentloaded_for_webkit_load_config() -> None:
    page = FakePage()

    result = await navigation.navigate(
        make_run(page=page, config=make_config(browser="webkit"))
    )

    assert page.goto_calls[0][1]["wait_until"] == "domcontentloaded"
    assert result.load_event_fired is False
    assert result.allow_interactive is True


async def test_navigate_falls_back_to_lighter_wait_until_on_goto_timeout(
    caplog: pytest.LogCaptureFixture,
) -> None:
    # `load` times out (slow third-party subresource); `domcontentloaded` works.
    page = FakePage(goto_timeout_levels={"load"})
    caplog.set_level(logging.WARNING, logger="app.screenshots.capture.navigation")

    result = await navigation.navigate(make_run(page=page))

    attempted = [call[1].get("wait_until") for call in page.goto_calls]
    assert attempted == ["load", "domcontentloaded"]
    assert result.wait_strategy == "domcontentloaded"
    assert result.allow_interactive is True
    assert caplog.messages == [
        "navigation wait_until=load timed out for https://example.com/, "
        "falling back to wait_until=domcontentloaded"
    ]


async def test_navigate_raises_when_all_navigation_levels_time_out() -> None:
    page = FakePage(goto_timeout_levels={"networkidle", "load", "domcontentloaded"})
    run = make_run(page=page, config=make_config(wait_until="networkidle"))

    with pytest.raises(PlaywrightTimeoutError):
        await navigation.navigate(run)

    # Exhausted the full cascade before giving up.
    attempted = [call[1].get("wait_until") for call in page.goto_calls]
    assert attempted == ["networkidle", "load", "domcontentloaded"]
    assert run.report.current_phase == "goto"


async def test_navigate_waits_for_post_load_data_requests() -> None:
    page = FakePage()
    run = make_run(page=page)
    fetch = FakeRequest(resource_type="fetch")
    run.tracker.on_request(fetch)
    page.on_wait = lambda polls: polls == 3 and run.tracker.on_request_done(fetch)

    await navigation.navigate(run)

    # xhr/fetch count right after goto: busy for 300 ms, then quiet 500 ms.
    assert sum(page.waits) == 800


async def test_reload_is_not_implemented_yet() -> None:
    with pytest.raises(NotImplementedError):
        await navigation.reload(make_run(), reason="styles_missing")
