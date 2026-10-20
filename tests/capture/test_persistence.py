"""How capture_screenshot turns a CaptureResult into a file and a row."""

import uuid
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import AsyncMock, patch

from app.screenshots.capture.errors import PageAssetLoadError
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
            browser=FakeBrowser(),
            session=session or FakeSession(),
            task_id=task_id,
            project_id=project_id,
            url=URL,
            viewport={"width": 1280, "height": 720},
            config=make_config(),
            existing=existing,
        )
    return record, output_path, task_id


def _ok(data: bytes, duration: float = 1.234, **kwargs) -> CaptureResult:
    outcome = CaptureOutcome(data=data, duration=duration, **kwargs)
    return CaptureResult(outcome, None, outcome.report, 1, duration)


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


async def test_unstable_capture_keeps_its_warning_in_error_message(
    tmp_path: Path,
) -> None:
    result = _ok(
        b"img",
        captured_with_unstable_dom=True,
        warning_message=UNSTABLE_TEXT,
    )

    record, _, _ = await _persist(result, tmp_path)

    assert record.status == "ok"
    assert record.captured_with_unstable_dom is True
    assert record.error_message == UNSTABLE_TEXT
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


async def test_recapture_updates_the_existing_row_in_place(tmp_path: Path) -> None:
    old_created = datetime(2026, 1, 1, tzinfo=UTC)
    existing = Screenshot(
        id=uuid.uuid4(),
        task_id=uuid.uuid4(),
        url=URL,
        browser="chromium",
        width=1280,
        height=720,
        file_format="jpeg",
        file_path="old/path.jpg",
        signed_url="old",
        status="recapturing",
        captured_with_unstable_dom=True,
        error_message="old error",
        duration_seconds=99.0,
        created_at=old_created,
    )
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
    assert record.created_at > old_created  # cache-bust bump
    assert output_path.read_bytes() == data
