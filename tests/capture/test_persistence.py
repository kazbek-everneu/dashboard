"""How capture_screenshot turns a CaptureResult into a file and a row."""

import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.screenshots import storage
from app.screenshots.capture.errors import CaptureDeadlineExceeded, PageAssetLoadError
from app.screenshots.capture.profiles import DeviceProfile
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureOutcome, CaptureResult
from app.screenshots.models import Screenshot
from app.screenshots.service import capture_screenshot
from tests.capture.fakes import FakeBrowser, FakeSession, make_config, png_bytes

URL = "https://example.com/pricing"
UNSTABLE_TEXT = (
    "Captured with unstable DOM after stabilization timeout. "
    "Content may be incomplete or still settling."
)


async def _persist(
    result: CaptureResult,
    tmp_path: Path,
    *,
    existing: Screenshot | None = None,
    session: FakeSession | None = None,
    **kwargs,
) -> tuple[Screenshot, Path, uuid.UUID]:
    task_id = existing.task_id if existing is not None else uuid.uuid4()
    project_id = uuid.uuid4()
    output_path = tmp_path / "pricing" / "1280x720_chromium.jpg"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with (
        patch(
            "app.screenshots.capture.pipeline.capture_with_retries",
            AsyncMock(return_value=result),
        ),
        patch(
            "app.screenshots.service.build_file_path",
            return_value=f"{project_id}/{task_id}/pricing/1280x720_chromium.jpg",
        ),
        patch("app.screenshots.service.generate_signed_url", return_value="signed"),
        patch("app.screenshots.service.ensure_parent"),
        patch("app.screenshots.service.abs_file_path", return_value=output_path),
    ):
        record = await capture_screenshot(
            browser=kwargs.pop("browser", None) or FakeBrowser(),
            session=session or FakeSession(),
            task_id=task_id,
            project_id=project_id,
            url=URL,
            viewport=kwargs.pop("viewport", {"width": 1280, "height": 720}),
            config=make_config(),
            existing=existing,
            **kwargs,
        )
    return record, output_path, task_id


def _ok(
    data: bytes,
    duration: float = 1.234,
    *,
    report: CaptureReport | None = None,
    attempts: int = 1,
    **kwargs,
) -> CaptureResult:
    outcome = CaptureOutcome(
        data=data, duration=duration, report=report or CaptureReport(), **kwargs
    )
    return CaptureResult(outcome, None, outcome.report, attempts, duration)


def _existing(**overrides) -> Screenshot:
    values: dict = {
        "id": uuid.uuid4(),
        "task_id": uuid.uuid4(),
        "url": URL,
        "browser": "chromium",
        "width": 1280,
        "height": 720,
        "file_format": "jpeg",
        "file_path": "proj/task/pricing/1280x720_chromium.jpg",
        "signed_url": "old",
        "status": "recapturing",
        "captured_with_unstable_dom": True,
        "error_message": "old error",
        "duration_seconds": 99.0,
        "created_at": datetime(2026, 1, 1, tzinfo=UTC),
    }
    values.update(overrides)
    return Screenshot(**values)


async def test_ok_capture_writes_the_file_and_a_new_row(tmp_path: Path) -> None:
    session = FakeSession()
    data = png_bytes(1280, 3000)

    record, output_path, task_id = await _persist(_ok(data), tmp_path, session=session)

    assert output_path.read_bytes() == data
    assert record.task_id == task_id
    assert (record.url, record.browser) == (URL, "chromium")
    assert (record.width, record.height) == (1280, 720)
    assert (record.image_width, record.image_height) == (1280, 3000)
    assert record.file_format == "jpeg"
    assert record.file_path.endswith("/pricing/1280x720_chromium.jpg")
    assert record.signed_url == "signed"
    assert record.status == "ok"
    assert record.error_message is None
    assert record.captured_with_unstable_dom is False
    assert record.duration_seconds == 1.234
    assert session.added == [record]
    assert session.commits == 1
    assert session.refreshed == [record]


async def test_ok_row_stores_the_report_and_the_profile_device(
    tmp_path: Path,
) -> None:
    report = CaptureReport()
    report.warn("overlays_hidden", "Hid 2 overlays", samples=["div#cookie"])
    report.set_meta("navigation", final_url=URL, http_status=200)
    report.timings_ms["total"] = 1234
    profile = DeviceProfile(device_type="mobile", is_mobile=True, has_touch=True)

    record, _, _ = await _persist(
        _ok(b"img", report=report, attempts=2),
        tmp_path,
        viewport={"width": 390, "height": 844},
        profile=profile,
    )

    assert record.device_type == "mobile"
    assert record.error_message is None
    assert record.warnings == report.to_db()[0]
    assert [w["code"] for w in record.warnings] == ["overlays_hidden"]
    meta = record.capture_meta
    assert meta is not None
    assert meta["v"] == 1
    assert meta["navigation"] == {"final_url": URL, "http_status": 200}
    assert meta["timings_ms"] == {"total": 1234}
    # Filled in by the service when the report has no value of its own.
    assert meta["attempts"] == 2
    assert meta["browser_version"] == "149.0.7800.0"
    assert meta["device"] == profile.meta()
    assert "failure" not in meta


async def test_report_values_win_over_the_service_defaults(tmp_path: Path) -> None:
    report = CaptureReport()
    report.meta["attempts"] = 7
    report.set_meta("device", type="tablet", emulation="full")

    record, _, _ = await _persist(_ok(b"img", report=report), tmp_path)

    assert record.capture_meta["attempts"] == 7
    assert record.capture_meta["device"] == {"type": "tablet", "emulation": "full"}


async def test_the_device_type_comes_from_the_viewport_without_a_profile(
    tmp_path: Path,
) -> None:
    record, _, _ = await _persist(
        _ok(b"img"), tmp_path, viewport={"width": 834, "height": 1112}
    )

    assert record.device_type == "tablet"
    assert record.capture_meta["device"]["type"] == "tablet"


async def test_unstable_capture_is_a_warning_not_an_error_message(
    tmp_path: Path,
) -> None:
    report = CaptureReport()
    report.warn("dom_unstable", UNSTABLE_TEXT, reason="dom")
    result = _ok(
        b"img",
        report=report,
        captured_with_unstable_dom=True,
        warning_message=UNSTABLE_TEXT,
    )

    record, _, _ = await _persist(result, tmp_path)

    assert record.status == "ok"
    assert record.captured_with_unstable_dom is True
    assert record.error_message is None
    assert [(w["code"], w["message"]) for w in record.warnings] == [
        ("dom_unstable", UNSTABLE_TEXT)
    ]
    assert record.image_width is None  # b"img" is not a readable image


async def test_failed_capture_writes_no_file(tmp_path: Path) -> None:
    error = PageAssetLoadError("Visible page assets did not load: 1 visible img")
    result = CaptureResult(None, error, CaptureReport(), 2, 9.5)

    record, output_path, _ = await _persist(result, tmp_path)

    assert not output_path.exists()
    assert record.status == "failed"
    assert record.error_message == "Visible page assets did not load: 1 visible img"
    assert record.captured_with_unstable_dom is False
    assert (record.image_width, record.image_height) == (None, None)
    assert record.duration_seconds == 9.5
    assert record.device_type == "desktop"


async def test_failed_row_stores_partial_meta_with_the_failure(
    tmp_path: Path,
) -> None:
    report = CaptureReport()
    report.warn("fonts_timeout", "Fonts still loading")
    report.set_meta("navigation", http_status=200)
    with pytest.raises(PageAssetLoadError), report.phase("readiness"):
        raise PageAssetLoadError("missing")
    result = CaptureResult(None, PageAssetLoadError("missing"), report, 2, 9.5)

    record, _, _ = await _persist(result, tmp_path)

    assert record.status == "failed"
    assert record.warnings == []  # a failed row has no warnings
    meta = record.capture_meta
    assert meta["navigation"] == {"http_status": 200}
    assert "readiness" in meta["timings_ms"]
    assert meta["attempts"] == 2
    assert meta["failure"] == {"class": "PageAssetLoadError", "phase": "readiness"}


async def test_failure_phase_prefers_the_error_and_keeps_the_report_section(
    tmp_path: Path,
) -> None:
    deadline = CaptureDeadlineExceeded(phase="snapshot")
    record, _, _ = await _persist(
        CaptureResult(None, deadline, CaptureReport(current_phase="goto"), 1, 3.0),
        tmp_path,
    )
    assert record.capture_meta["failure"] == {
        "class": "CaptureDeadlineExceeded",
        "phase": "snapshot",
    }

    report = CaptureReport()
    report.set_meta("failure", **{"class": "NavigationError", "phase": "goto"})
    record, _, _ = await _persist(
        CaptureResult(None, RuntimeError("x"), report, 1, 1.0), tmp_path
    )
    assert record.capture_meta["failure"] == {
        "class": "NavigationError",
        "phase": "goto",
    }


async def test_recapture_updates_the_existing_row_in_place(tmp_path: Path) -> None:
    existing = _existing()
    old_created = existing.created_at
    original_id = existing.id
    data = png_bytes(1280, 2000)

    record, output_path, _ = await _persist(
        _ok(data, duration=2.0), tmp_path, existing=existing
    )

    assert record is existing
    assert record.id == original_id
    assert record.status == "ok"
    assert record.error_message is None
    assert record.captured_with_unstable_dom is False
    assert (record.image_width, record.image_height) == (1280, 2000)
    assert record.duration_seconds == 2.0
    assert record.signed_url == "signed"
    assert record.device_type == "desktop"
    assert record.created_at > old_created  # cache-bust bump
    assert output_path.read_bytes() == data


async def test_recapture_keeps_the_rows_own_file_path(tmp_path: Path) -> None:
    # A row from before a storage naming change keeps (and overwrites) its file.
    existing = _existing(file_path="proj/task/old-name/1280x720_chromium.jpg")
    written: list[str] = []
    session = FakeSession()

    with (
        patch(
            "app.screenshots.capture.pipeline.capture_with_retries",
            AsyncMock(return_value=_ok(b"img")),
        ),
        patch("app.screenshots.service.build_file_path") as build_file_path,
        patch(
            "app.screenshots.service._write_file",
            side_effect=lambda path, _data: written.append(path),
        ),
    ):
        record = await capture_screenshot(
            browser=FakeBrowser(),
            session=session,
            task_id=existing.task_id,
            project_id=uuid.uuid4(),
            url=URL,
            viewport={"width": 1280, "height": 720},
            config=make_config(),
            existing=existing,
        )

    build_file_path.assert_not_called()
    assert written == ["proj/task/old-name/1280x720_chromium.jpg"]
    assert record.file_path == "proj/task/old-name/1280x720_chromium.jpg"
    assert record.signed_url.startswith(
        f"/files/{existing.task_id}/old-name/1280x720_chromium.jpg?token="
    )


async def test_rows_get_new_warning_and_meta_objects(tmp_path: Path) -> None:
    # JSONB columns do not track in-place mutation: every write assigns new
    # objects, and the report's own containers are never shared with the row.
    old_warnings = [{"code": "dom_unstable", "level": "warning", "message": "x"}]
    old_meta = {"v": 1, "attempts": 1}
    existing = _existing(warnings=old_warnings, capture_meta=old_meta)
    report = CaptureReport()
    report.warn("overlays_hidden", "Hid 1 overlay")

    record, _, _ = await _persist(_ok(b"img", report=report), tmp_path, existing=existing)

    assert record.warnings is not old_warnings
    assert record.capture_meta is not old_meta
    assert old_warnings == [{"code": "dom_unstable", "level": "warning", "message": "x"}]
    assert old_meta == {"v": 1, "attempts": 1}
    assert record.capture_meta is not report.meta
    record.capture_meta["navigation"] = {}
    assert "navigation" not in report.meta

    ok_warnings = record.warnings
    failed, _, _ = await _persist(
        CaptureResult(None, RuntimeError("boom"), report, 1, 1.0),
        tmp_path,
        existing=record,
    )
    assert failed.warnings == []
    assert failed.warnings is not ok_warnings
    assert [w["code"] for w in ok_warnings] == ["overlays_hidden"]


async def test_the_file_is_written_off_the_event_loop(tmp_path: Path) -> None:
    threads: list[int] = []

    def record_thread(path: Path, data: bytes) -> None:
        threads.append(threading.get_ident())
        path.write_bytes(data)

    with patch("app.screenshots.service.write_bytes_atomic", record_thread):
        _, output_path, _ = await _persist(_ok(b"img"), tmp_path)

    assert output_path.read_bytes() == b"img"
    assert threads and threads[0] != threading.get_ident()


async def test_a_failed_write_leaves_no_partial_file(tmp_path: Path) -> None:
    existing = _existing()
    output_path = tmp_path / "pricing" / "1280x720_chromium.jpg"
    output_path.parent.mkdir(parents=True)
    output_path.write_bytes(b"previous image")

    with (
        patch.object(storage.os, "replace", side_effect=OSError("disk full")),
        pytest.raises(OSError, match="disk full"),
    ):
        await _persist(_ok(b"new image"), tmp_path, existing=existing)

    # The old file is untouched and no temp file is left behind.
    assert output_path.read_bytes() == b"previous image"
    assert sorted(p.name for p in output_path.parent.iterdir()) == [
        "1280x720_chromium.jpg"
    ]
