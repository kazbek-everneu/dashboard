"""Screenshot capture report fields: device type, warnings and capture metadata.

The screenshots router returns ORM rows and lets FastAPI serialize them through
ScreenshotRead, so the schema must accept both new rows and legacy rows that
predate the fields (NULL device type and metadata, empty warnings).
"""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from pydantic import ValidationError
from sqlalchemy.dialects.postgresql.asyncpg import dialect as asyncpg_dialect

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.core.database import get_session
from app.projects.models import Project
from app.screenshots.models import CaptureWarningRead, Screenshot, ScreenshotRead
from app.screenshots.router import router as screenshots_router
from app.tasks.models import Task

FONTS_TIMEOUT: dict[str, Any] = {
    "code": "fonts_timeout",
    "level": "warning",
    "message": "Fonts were still loading after 5 s",
    "details": {"families": ["Inter"]},
}
# No details: the schema fills in an empty dict.
OVERLAYS_HIDDEN: dict[str, Any] = {
    "code": "overlays_hidden",
    "level": "info",
    "message": "Hid 1 overlay",
}
CAPTURE_META: dict[str, Any] = {
    "v": 1,
    "attempts": 1,
    "browser_version": "149.0.7827.2",
    "device": {
        "type": "mobile",
        "emulation": "full",
        "is_mobile": True,
        "has_touch": True,
        "user_agent": "Mozilla/5.0 (Linux; Android 10; K) Mobile Safari/537.36",
    },
    "navigation": {
        "requested_url": "https://example.com/",
        "final_url": "https://example.com/",
        "http_status": 200,
        "wait_strategy": "load",
        "load_event": True,
        "reloads": 0,
        "challenge": None,
    },
    "timings_ms": {"goto": 812, "total": 4210},
}


def _legacy_payload(**overrides: Any) -> dict[str, Any]:
    # A ScreenshotRead payload as it looked before the capture report fields.
    payload: dict[str, Any] = {
        "id": str(uuid.uuid4()),
        "task_id": str(uuid.uuid4()),
        "url": "https://example.com/",
        "browser": "chrome",
        "width": 390,
        "height": 844,
        "image_width": 390,
        "image_height": 2400,
        "file_format": "jpeg",
        "file_path": "proj/task/home/390x844_chrome.jpg",
        "signed_url": "/files/task/home/390x844_chrome.jpg?token=abc",
        "status": "ok",
        "captured_with_unstable_dom": False,
        "error_message": None,
        "duration_seconds": 4.2,
        "created_at": "2026-10-20T12:00:00Z",
    }
    payload.update(overrides)
    return payload


def _screenshot(task_id: uuid.UUID | None = None, **kwargs: Any) -> Screenshot:
    defaults: dict[str, Any] = {
        "task_id": task_id or uuid.uuid4(),
        "url": "https://example.com/",
        "browser": "chrome",
        "width": 390,
        "height": 844,
        "image_width": 390,
        "image_height": 2400,
        "file_format": "jpeg",
        "file_path": "proj/task/home/390x844_chrome.jpg",
        "signed_url": "/files/task/home/390x844_chrome.jpg?token=abc",
        "status": "ok",
        "duration_seconds": 4.2,
        "created_at": datetime(2026, 10, 20, 12, 0, tzinfo=UTC),
    }
    defaults.update(kwargs)
    return Screenshot(**defaults)


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------


def test_screenshot_read_defaults_capture_report_for_legacy_payload() -> None:
    read = ScreenshotRead.model_validate(_legacy_payload())

    assert read.device_type is None
    assert read.warnings == []
    assert read.capture_meta is None


def test_screenshot_read_validates_warning_items() -> None:
    read = ScreenshotRead.model_validate(
        _legacy_payload(
            device_type="mobile",
            warnings=[FONTS_TIMEOUT, OVERLAYS_HIDDEN],
            capture_meta=CAPTURE_META,
        )
    )

    assert read.warnings == [
        CaptureWarningRead(
            code="fonts_timeout",
            level="warning",
            message="Fonts were still loading after 5 s",
            details={"families": ["Inter"]},
        ),
        CaptureWarningRead(
            code="overlays_hidden", level="info", message="Hid 1 overlay", details={}
        ),
    ]
    assert read.device_type == "mobile"
    assert read.capture_meta == CAPTURE_META


def test_screenshot_read_rejects_unknown_warning_level() -> None:
    bad = {**OVERLAYS_HIDDEN, "level": "error"}

    with pytest.raises(ValidationError) as exc_info:
        ScreenshotRead.model_validate(_legacy_payload(warnings=[bad]))

    assert exc_info.value.errors()[0]["loc"] == ("warnings", 0, "level")


def test_screenshot_read_from_orm_row_round_trips_capture_report() -> None:
    row = _screenshot(
        device_type="mobile",
        warnings=[FONTS_TIMEOUT, OVERLAYS_HIDDEN],
        capture_meta=CAPTURE_META,
    )

    read = ScreenshotRead.model_validate(row)
    payload = read.model_dump(mode="json")

    assert read.id == row.id
    assert payload["device_type"] == "mobile"
    assert payload["warnings"] == [FONTS_TIMEOUT, {**OVERLAYS_HIDDEN, "details": {}}]
    assert payload["capture_meta"] == CAPTURE_META
    assert ScreenshotRead.model_validate(payload) == read


def test_new_screenshot_row_has_empty_capture_report() -> None:
    first = _screenshot()
    second = _screenshot()

    assert first.device_type is None
    assert first.warnings == []
    assert first.capture_meta is None
    # default_factory: rows never share one mutable list.
    assert first.warnings is not second.warnings
    assert ScreenshotRead.model_validate(first).warnings == []


def test_capture_report_columns_match_migration() -> None:
    columns = Screenshot.__table__.c  # type: ignore[attr-defined]

    assert columns.device_type.nullable is True
    assert columns.device_type.type.length == 16
    assert columns.warnings.nullable is False
    assert str(columns.warnings.server_default.arg) == "'[]'::jsonb"
    assert columns.capture_meta.nullable is True
    # A row without metadata is SQL NULL like the legacy rows, not JSON null.
    dialect = asyncpg_dialect()
    bind = columns.capture_meta.type.dialect_impl(dialect).bind_processor(dialect)
    assert bind(None) is None
    assert bind({"v": 1}) == '{"v": 1}'


# ---------------------------------------------------------------------------
# Router: GET /screenshots/{task_id} returns ORM rows through ScreenshotRead
# ---------------------------------------------------------------------------


class _FakeExecResult:
    def __init__(self, rows: list[Screenshot]) -> None:
        self._rows = rows

    def all(self) -> list[Screenshot]:
        return list(self._rows)


class _FakeListSession:
    def __init__(self, task: Task, screenshots: list[Screenshot]) -> None:
        self.task = task
        self.screenshots = screenshots

    async def get(self, model: type, key: object) -> object | None:
        if model is Task and key == self.task.id:
            return self.task
        return None

    async def exec(self, _statement: object) -> _FakeExecResult:
        return _FakeExecResult(self.screenshots)

    def add(self, _item: object) -> None:
        return None

    async def commit(self) -> None:
        return None


async def test_list_endpoint_serializes_new_and_legacy_rows() -> None:
    user = User(name="User", email="user@example.com", hashed_password="hashed")
    project = Project(
        id=uuid.uuid4(), owner_id=user.id, name="Project", url="https://example.com"
    )
    task = Task(project_id=project.id, created_by=user.id, status="completed")
    legacy = _screenshot(task.id)
    reported = _screenshot(
        task.id,
        device_type="mobile",
        warnings=[FONTS_TIMEOUT, OVERLAYS_HIDDEN],
        capture_meta=CAPTURE_META,
    )
    session = _FakeListSession(task, [legacy, reported])

    api = FastAPI()
    api.include_router(screenshots_router)
    api.dependency_overrides[get_session] = lambda: session
    api.dependency_overrides[get_current_user] = lambda: user

    with patch(
        "app.screenshots.router.get_project", new=AsyncMock(return_value=project)
    ):
        async with AsyncClient(
            transport=ASGITransport(app=api), base_url="http://test"
        ) as client:
            response = await client.get(f"/screenshots/{task.id}")

    assert response.status_code == 200
    body = response.json()
    assert [item["id"] for item in body] == [str(legacy.id), str(reported.id)]
    assert body[0]["device_type"] is None
    assert body[0]["warnings"] == []
    assert body[0]["capture_meta"] is None
    assert body[1]["device_type"] == "mobile"
    assert body[1]["warnings"] == [FONTS_TIMEOUT, {**OVERLAYS_HIDDEN, "details": {}}]
    assert body[1]["capture_meta"] == CAPTURE_META
