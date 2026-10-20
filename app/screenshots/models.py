import uuid
from datetime import datetime

from sqlalchemy import Column, ForeignKey
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
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)


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
    created_at: datetime
