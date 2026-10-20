"""capture_screenshot end to end: real pipeline on fakes, files and rows."""

import importlib
import logging
import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

import app.screenshots.capture
from app.screenshots.capture.errors import (
    AttemptFailed,
    PageAssetLoadError,
    PageStabilizationTimeoutError,
)
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureOutcome, CaptureResult
from app.screenshots.models import Screenshot
from app.screenshots.service import capture_screenshot
from tests.capture.fakes import (
    READY_STABILITY_STATE,
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


async def test_capture_screenshot_fails_after_visible_image_asset_retry(
    tmp_path: Path,
) -> None:
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})

    result, output_path = await _capture(page, tmp_path)

    assert result.status == "failed"
    assert result.captured_with_unstable_dom is False
    # Same text as before the split, retry included.
    assert result.error_message == (
        "Visible page assets did not load: 1 visible img; samples: img: "
        "https://cdn.example.com/missing.jpg; ignored hidden pending images: 2"
    )
    assert len(page.goto_calls) == 2
    assert not output_path.exists()


async def test_capture_screenshot_includes_network_diagnostics(tmp_path: Path) -> None:
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

    result, _ = await _capture(page, tmp_path)

    assert result.status == "failed"
    assert result.error_message is not None
    assert "Network diagnostics" in result.error_message
    assert "net::ERR_CONNECTION_RESET" in result.error_message
    assert "HTTP 403" in result.error_message


async def test_capture_screenshot_does_not_save_blank_assetless_page(
    tmp_path: Path,
) -> None:
    page = FakePage(
        script_results={
            "stability-state": {
                **READY_STABILITY_STATE,
                "scrollHeight": 720,
                "textLength": 0,
            }
        }
    )

    result, output_path = await _capture(page, tmp_path)

    assert result.status == "failed"
    assert result.error_message == (
        "Page rendered blank after stabilization: no visible text or "
        "image/background assets"
    )
    assert not output_path.exists()


async def test_capture_screenshot_recovers_when_load_event_times_out(
    tmp_path: Path,
) -> None:
    # A page whose `load` event never fires (third-party stall) must still
    # produce an `ok` screenshot via the domcontentloaded fallback rather than
    # a timeout `failed` row.
    page = FakePage(goto_timeout_levels={"load"})

    result, output_path = await _capture(page, tmp_path)

    assert result.status == "ok"
    assert output_path.read_bytes() == b"img"


async def test_capture_screenshot_navigation_timeout_is_not_retried(
    tmp_path: Path,
) -> None:
    page = FakePage(goto_timeout_levels={"load", "domcontentloaded"})

    result, _ = await _capture(page, tmp_path)

    assert result.status == "failed"
    assert result.error_message == "Timeout 1000ms exceeded"
    assert len(page.goto_calls) == 2  # one attempt: load, then domcontentloaded


async def test_capture_screenshot_stores_the_unstable_dom_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    stability = AsyncMock(side_effect=PageStabilizationTimeoutError("busy"))
    caplog.set_level(logging.INFO, logger="app.screenshots.service")

    with patch("app.screenshots.capture.readiness.wait_for_dom_stability", stability):
        result, output_path = await _capture(FakePage(), tmp_path)

    assert result.status == "ok"
    assert result.captured_with_unstable_dom is True
    assert result.error_message == (
        "Captured with unstable DOM after stabilization timeout. "
        "Content may be incomplete or still settling."
    )
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
    page = FakePage(script_results={"decode-images": MISSING_IMAGE_STATE})

    await _capture(page, tmp_path)

    capture_lines = [m for m in caplog.messages if m.startswith("capture task=")]
    assert len(capture_lines) == 1
    assert "device=desktop status=failed warnings=[]" in capture_lines[0]


async def test_capture_screenshot_records_attempt_time_for_failed_rows(
    tmp_path: Path,
) -> None:
    failing = AsyncMock(side_effect=PageAssetLoadError("missing"))
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
            AttemptFailed(PageAssetLoadError("missing"), CaptureReport()),
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


def test_importing_the_capture_package_disables_fonts_ready_wait(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PW_TEST_SCREENSHOT_NO_FONTS_READY", raising=False)

    importlib.reload(app.screenshots.capture)

    assert os.environ["PW_TEST_SCREENSHOT_NO_FONTS_READY"] == "1"
