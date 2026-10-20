import uuid
from datetime import datetime
from typing import Any, Literal

from sqlalchemy import Column, ForeignKey, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from app.core.time import UtcDateTime, utcnow


class Screenshot(SQLModel, table=True):
    __tablename__ = "screenshots"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    task_id: uuid.UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("tasks.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    url: str = Field(max_length=2048)
    browser: str = Field(max_length=50)
    width: int
    height: int
    image_width: int | None = Field(default=None)
    image_height: int | None = Field(default=None)
    file_format: str = Field(default="jpeg", max_length=10)
    file_path: str = Field(max_length=1024)
    signed_url: str = Field(max_length=2048)
    status: str = Field(default="ok")  # "ok" | "failed"
    captured_with_unstable_dom: bool = Field(default=False)
    error_message: str | None = Field(default=None)
    duration_seconds: float = Field(default=0.0)
    # The emulated device profile: "desktop" | "tablet" | "mobile"; NULL on rows
    # captured before device emulation (derive it from the width instead).
    device_type: str | None = Field(default=None, max_length=16)
    # Capture warnings, each {code, level, message, details} (CaptureWarningRead).
    # JSONB does not track in-place mutation: always assign a new list.
    warnings: list[dict[str, Any]] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default=text("'[]'::jsonb")),
    )
    # Versioned capture metadata ("v": 1 — device, navigation, page, readiness,
    # overlays, timings_ms, failure). None is stored as SQL NULL, not JSON null.
    capture_meta: dict[str, Any] | None = Field(
        default=None,
        sa_column=Column(JSONB(none_as_null=True), nullable=True),
    )
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)


class CaptureWarningRead(SQLModel):
    code: str
    level: Literal["info", "warning"]
    message: str
    details: dict[str, Any] = Field(default_factory=dict)


class ScreenshotRead(SQLModel):
    id: uuid.UUID
    task_id: uuid.UUID
    url: str
    browser: str
    width: int
    height: int
    image_width: int | None
    image_height: int | None
    file_format: str
    file_path: str
    signed_url: str
    status: str
    captured_with_unstable_dom: bool
    error_message: str | None
    duration_seconds: float
    device_type: str | None = None
    warnings: list[CaptureWarningRead] = Field(default_factory=list)
    capture_meta: dict[str, Any] | None = None
    created_at: datetime
