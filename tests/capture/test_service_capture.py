"""capture_screenshot end to end: real pipeline on fakes, files and rows."""

import asyncio
import importlib
import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

import app.screenshots.capture
from app.screenshots.capture import clock, pipeline
from app.screenshots.capture.errors import (
    AttemptFailed,
    BlankPageError,
)
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import (
    CaptureOutcome,
    CaptureResult,
    ReadinessResult,
)
from app.screenshots.models import Screenshot
from app.screenshots.service import capture_screenshot
from tests.capture.fakes import (
    FakeBrowser,
    FakeContext,
    FakePage,
    FakeRequest,
    FakeResponse,
    FakeSession,
    make_config,
)

URL = "https://example.com/"
DESKTOP = {"width": 1280, "height": 720}
MISSING_IMAGE_STATE = {
    "visibleImageCount": 1,
    "visibleBackgroundCount": 0,
    "missingVisibleImageCount": 1,
    "missingBackgroundImageCount": 0,
    "hiddenPendingImageCount": 2,
    "missingSamples": [{"kind": "img", "url": "https://cdn.example.com/missing.jpg"}],
}


@contextmanager
def _storage(
    tmp_path: Path, task_id: uuid.UUID, project_id: uuid.UUID
) -> Iterator[Path]:
    output_path = tmp_path / "example.jpg"
    with (
        patch(
            "app.screenshots.service.build_file_path",
            return_value=f"{project_id}/{task_id}/example.jpg",
        ),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.ensure_parent"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
        patch("app.screenshots.capture.pipeline.asyncio.sleep", new=AsyncMock()),
    ):
        yield output_path


@contextmanager
def _results() -> Iterator[list[CaptureResult]]:
    """Collect what pipeline.capture_with_retries returns to the service."""
    results: list[CaptureResult] = []
    real = pipeline.capture_with_retries

    async def spy(**kwargs) -> CaptureResult:
        result = await real(**kwargs)
        results.append(result)
        return result

    with patch("app.screenshots.capture.pipeline.capture_with_retries", spy):
        yield results


def _blank_page() -> AsyncMock:
    return AsyncMock(return_value=ReadinessResult(blank=True))


async def _capture(
    page: FakePage | None, tmp_path: Path, **kwargs
) -> tuple[Screenshot, Path]:
    task_id = kwargs.pop("task_id", uuid.uuid4())
    project_id = uuid.uuid4()
    browser = kwargs.pop("browser", None) or FakeBrowser(page)
    with _storage(tmp_path, task_id, project_id) as output_path:
        result = await capture_screenshot(
            browser=browser,
            session=kwargs.pop("session", FakeSession()),
            task_id=task_id,
            project_id=project_id,
            url=URL,
            viewport=kwargs.pop("viewport", DESKTOP),
            config=kwargs.pop("config", make_config()),
            **kwargs,
        )
    return result, output_path


async def test_capture_screenshot_saves_a_settled_page(tmp_path: Path) -> None:
    session = FakeSession()

    result, output_path = await _capture(FakePage(), tmp_path, session=session)

    assert result.status == "ok"
    assert result.error_message is None
    assert result.captured_with_unstable_dom is False
    assert output_path.read_bytes() == b"img"
    assert session.record is result


async def test_capture_screenshot_saves_a_page_with_a_missing_image(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    # A broken image no longer costs the screenshot: it is saved with an
    # assets_missing warning, without a retry or a reload.
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})
    caplog.set_level(logging.INFO, logger="app.screenshots.service")

    with _results() as results:
        result, output_path = await _capture(page, tmp_path)

    assert result.status == "ok"
    assert result.error_message is None
    assert result.captured_with_unstable_dom is False
    assert output_path.read_bytes() == b"img"
    assert len(page.goto_calls) == 1
    assert page.reload_calls == []
    (capture,) = results
    assert capture.ok is True and capture.attempts == 1
    warning = capture.report.warning("assets_missing")
    assert warning is not None and warning.level == "warning"
    assert warning.details["count"] == 1
    assert warning.details["samples"][0]["url"] == "https://cdn.example.com/missing.jpg"
    capture_lines = [m for m in caplog.messages if m.startswith("capture task=")]
    assert "status=ok warnings=['assets_missing']" in capture_lines[0]


async def test_capture_screenshot_report_keeps_the_network_diagnostics(
    tmp_path: Path,
) -> None:
    page = FakePage(
        script_results={
            "decode-images": {**MISSING_IMAGE_STATE, "hiddenPendingImageCount": 0}
        },
        failed_requests=[
            FakeRequest(
                resource_type="image",
                url="https://cdn.example.com/missing.jpg",
                failure="net::ERR_CONNECTION_RESET",
            )
        ],
        bad_responses=[
            FakeResponse(
                resource_type="image",
                status=403,
                url="https://cdn.example.com/blocked.jpg",
            )
        ],
    )

    with _results() as results:
        result, output_path = await _capture(page, tmp_path)

    assert result.status == "ok"
    assert output_path.read_bytes() == b"img"
    (capture,) = results
    assert capture.report.to_db()[0][0]["code"] == "assets_missing"


async def test_capture_screenshot_does_not_save_blank_page(tmp_path: Path) -> None:
    page = FakePage(
        failed_requests=[
            FakeRequest(
                resource_type="stylesheet",
                url="https://cdn.example.com/app.css",
                failure="net::ERR_CONNECTION_RESET",
            )
        ],
    )
    browser = FakeBrowser(page)

    with (
        patch("app.screenshots.capture.readiness.finalize", _blank_page()),
        _results() as results,
    ):
        result, output_path = await _capture(page, tmp_path, browser=browser)

    assert result.status == "failed"
    assert result.error_message == (
        "Page rendered blank after stabilization: no visible text or "
        "image/background assets; Network diagnostics: failed requests: "
        "stylesheet net::ERR_CONNECTION_RESET https://cdn.example.com/app.css"
    )
    assert not output_path.exists()
    # A blank page is retried in a fresh context.
    assert len(browser.contexts) == 2
    (capture,) = results
    assert isinstance(capture.error, BlankPageError)
    assert capture.report.meta["failure"] == {
        "class": "BlankPageError",
        "phase": "readiness",
    }


async def test_capture_screenshot_recovers_when_load_event_times_out(
    tmp_path: Path,
) -> None:
    # A page whose `load` event never fires (third-party stall) must still
    # produce an `ok` screenshot after the DOMContentLoaded goto rather than
    # a timeout `failed` row.
    page = FakePage(load_state_error=PlaywrightTimeoutError("Timeout 1000ms"))

    result, output_path = await _capture(page, tmp_path)

    assert result.status == "ok"
    assert output_path.read_bytes() == b"img"


async def test_capture_screenshot_navigation_timeout_is_not_retried(
    tmp_path: Path,
) -> None:
    page = FakePage(goto_timeout_levels={"load", "domcontentloaded"})
    browser = FakeBrowser(page)

    with _results() as results:
        result, _ = await _capture(page, tmp_path, browser=browser)

    assert result.status == "failed"
    assert result.error_message == (
        "Navigation timed out after 1000 ms: https://example.com/"
    )
    assert len(page.goto_calls) == 1  # one goto, one attempt
    assert len(browser.contexts) == 1
    (capture,) = results
    assert capture.report.meta["failure"]["class"] == "NavigationTimeoutError"
    assert capture.report.meta["attempts"] == 1


async def test_capture_screenshot_stores_the_unstable_dom_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    unsettled = AsyncMock(
        return_value=ReadinessResult(stable=False, unstable_reason="dom")
    )
    caplog.set_level(logging.INFO, logger="app.screenshots.service")

    with patch("app.screenshots.capture.readiness.finalize", unsettled):
        result, output_path = await _capture(FakePage(), tmp_path)

    assert result.status == "ok"
    assert result.captured_with_unstable_dom is True
    assert result.error_message is None  # the text is the dom_unstable warning
    assert [(w["code"], w["message"]) for w in result.warnings] == [
        (
            "dom_unstable",
            "Captured with unstable DOM after stabilization timeout. "
            "Content may be incomplete or still settling.",
        )
    ]
    assert output_path.read_bytes() == b"img"
    capture_lines = [m for m in caplog.messages if m.startswith("capture task=")]
    assert len(capture_lines) == 1
    assert "status=ok warnings=['dom_unstable'] timings_ms={" in capture_lines[0]


async def test_capture_screenshot_logs_one_structured_line(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    task_id = uuid.uuid4()
    caplog.set_level(logging.INFO, logger="app.screenshots.service")

    await _capture(
        FakePage(),
        tmp_path,
        task_id=task_id,
        viewport={"width": 390, "height": 844},
        config=make_config(browser="chrome"),
    )

    capture_lines = [m for m in caplog.messages if m.startswith("capture task=")]
    assert len(capture_lines) == 1
    line = capture_lines[0]
    assert line.startswith(
        f"capture task={task_id} url=https://example.com/ vp=390x844 "
        "browser=chrome device=mobile status=ok warnings=[] timings_ms={"
    )
    for phase in ("total", "context", "goto", "quiet", "prepare", "readiness"):
        assert f"'{phase}': " in line


async def test_capture_screenshot_logs_failures_too(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO, logger="app.screenshots.service")

    with patch("app.screenshots.capture.readiness.finalize", _blank_page()):
        await _capture(FakePage(), tmp_path)

    capture_lines = [m for m in caplog.messages if m.startswith("capture task=")]
    assert len(capture_lines) == 1
    assert "device=desktop status=failed warnings=[]" in capture_lines[0]


async def test_capture_screenshot_records_attempt_time_for_failed_rows(
    tmp_path: Path,
) -> None:
    failing = AsyncMock(side_effect=BlankPageError("missing"))
    # attempt 1: 10 → 13 s, attempt 2: 20 → 24 s
    clock = iter([10.0, 13.0, 20.0, 24.0])

    with (
        patch("app.screenshots.capture.pipeline.run_attempt", failing),
        patch("app.screenshots.capture.clock.now", side_effect=lambda: next(clock)),
    ):
        result, _ = await _capture(FakePage(), tmp_path)

    assert result.status == "failed"
    assert result.error_message == "missing"
    assert result.duration_seconds == 7.0


async def test_capture_screenshot_retries_in_fresh_context(tmp_path: Path) -> None:
    shared = FakeContext()
    run_attempt = AsyncMock(
        side_effect=[
            AttemptFailed(BlankPageError(), CaptureReport()),
            CaptureOutcome(data=b"img", duration=0.5),
        ]
    )

    with patch("app.screenshots.capture.pipeline.run_attempt", run_attempt):
        result, _ = await _capture(FakePage(), tmp_path, context=shared)

    assert result.status == "ok"
    assert result.duration_seconds == 0.5
    assert [call.kwargs["context"] for call in run_attempt.await_args_list] == [
        shared,
        None,
    ]


async def test_capture_screenshot_passes_new_options_through(tmp_path: Path) -> None:
    profile = DeviceProfile(device_type="tablet")
    provider = AsyncMock()
    task_id = uuid.uuid4()
    outcome = CaptureOutcome(data=b"img", duration=0.25)
    capture = AsyncMock(
        return_value=CaptureResult(outcome, None, outcome.report, 1, 0.25)
    )
    browser = FakeBrowser()

    with patch("app.screenshots.capture.pipeline.capture_with_retries", capture):
        result, _ = await _capture(
            None,
            tmp_path,
            browser=browser,
            task_id=task_id,
            profile=profile,
            device_type="mobile",
            browser_provider=provider,
            deadline_at=123.0,
        )

    assert result.status == "ok"
    kwargs = capture.await_args.kwargs
    assert kwargs["profile"] is profile  # an explicit profile wins
    assert kwargs["browser_provider"] is provider
    assert kwargs["deadline_at"] == 123.0
    assert kwargs["task_id"] == task_id
    assert kwargs["browser"] is browser
    assert kwargs["context"] is None


async def test_capture_screenshot_builds_the_profile_from_device_type(
    tmp_path: Path,
) -> None:
    outcome = CaptureOutcome(data=b"img", duration=0.25)
    capture = AsyncMock(
        return_value=CaptureResult(outcome, None, outcome.report, 1, 0.25)
    )

    with patch("app.screenshots.capture.pipeline.capture_with_retries", capture):
        await _capture(None, tmp_path, device_type="tablet")
        await _capture(None, tmp_path, viewport={"width": 390, "height": 844})

    explicit, by_width = (call.kwargs["profile"] for call in capture.await_args_list)
    assert explicit.device_type == "tablet"
    assert by_width.device_type == "mobile"
    # Built with the browser's version, so the device user agent is set too.
    assert explicit.emulation == by_width.emulation == "full"


async def test_capture_screenshot_fails_when_the_browser_cannot_come_back(
    tmp_path: Path,
) -> None:
    page = FakePage()
    provider = AsyncMock(return_value=None)

    result, output_path = await _capture(
        page,
        tmp_path,
        browser=FakeBrowser(page, connected=False),
        browser_provider=provider,
    )

    assert result.status == "failed"
    assert result.error_message == "Browser connection lost; reconnect failed"
    assert not output_path.exists()
    provider.assert_awaited_once()
    assert page.goto_calls == []


async def test_capture_screenshot_attempt_deadline_fails_the_row(
    tmp_path: Path,
) -> None:
    async def hang(*_args, **_kwargs) -> None:
        await asyncio.get_running_loop().create_future()

    with (
        patch("app.screenshots.capture.config.CLOSE_TIMEOUT_S", 0.05),
        patch("app.screenshots.capture.config.ATTEMPT_BUDGET_MIN_S", 0.05),
        patch("app.screenshots.capture.config.ATTEMPT_BUDGET_MAX_S", 0.05),
        patch(
            "app.screenshots.capture.prepare.prepare_page", AsyncMock(side_effect=hang)
        ),
        _results() as results,
    ):
        result, _ = await asyncio.wait_for(_capture(FakePage(), tmp_path), 5)

    assert result.status == "failed"
    assert result.error_message == "Capture attempt ran out of time (phase: prepare)"
    (capture,) = results
    assert capture.attempts == 2
    assert not isinstance(capture.error, TimeoutError)


async def test_capture_screenshot_passes_the_deadline_to_the_attempts(
    tmp_path: Path,
) -> None:
    run_attempt = AsyncMock(return_value=CaptureOutcome(data=b"img", duration=0.5))
    deadline_at = clock.now() + 120

    with patch("app.screenshots.capture.pipeline.run_attempt", run_attempt):
        result, _ = await _capture(FakePage(), tmp_path, deadline_at=deadline_at)

    assert result.status == "ok"
    assert 115 < run_attempt.await_args.kwargs["budget_s"] <= 120


def test_importing_the_capture_package_disables_fonts_ready_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PW_TEST_SCREENSHOT_NO_FONTS_READY", raising=False)

    importlib.reload(app.screenshots.capture)

    assert os.environ["PW_TEST_SCREENSHOT_NO_FONTS_READY"] == "1"
