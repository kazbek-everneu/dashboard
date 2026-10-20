import uuid
from datetime import datetime
from typing import Any

from pydantic import field_validator
from sqlalchemy import Column, ForeignKey, String
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel

from app.core.time import UtcDateTime, utcnow
from app.projects.models import validate_browser_names


class Task(SQLModel, table=True):
    __tablename__ = "tasks"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    created_by: uuid.UUID = Field(foreign_key="users.id")
    # pending | running | completed | failed | cancelled | timed_out
    status: str = Field(default="pending")

    progress_total: int = Field(default=0)
    progress_done: int = Field(default=0)
    progress_failed: int = Field(default=0)
    worker_job_id: str | None = Field(
        default=None,
        sa_column=Column(String(length=128), nullable=True, index=True),
    )

    # Optional overrides — None means "use ProjectConfig default"
    override_browser: str | None = Field(default=None)  # legacy single-browser override
    override_browsers: list[str] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )
    override_viewports: list[dict[str, Any]] | None = Field(
        default=None,
        sa_column=Column(JSONB, nullable=True),
    )

    # URLs to screenshot — stored as JSON array of strings
    target_urls: list[str] = Field(
        default_factory=list,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )

    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)
    started_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    completed_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    # Beaten by the worker while it runs the task; app.tasks.lifecycle
    # treats a running task without a recent beat as orphaned.
    heartbeat_at: datetime | None = Field(default=None, sa_type=UtcDateTime)


# ---------------------------------------------------------------------------
# Request / Response schemas
# ---------------------------------------------------------------------------

class TaskCreate(SQLModel):
    project_id: uuid.UUID
    target_urls: list[str] | None = None  # None → use enabled SitemapURLs
    override_browser: str | None = None  # legacy single-browser override
    override_browsers: list[str] | None = None
    override_viewports: list[dict[str, Any]] | None = None

    @field_validator("override_browser")
    @classmethod
    def validate_override_browser(cls, v: str | None) -> str | None:
        return validate_browser_names([v])[0] if v else v

    @field_validator("override_browsers")
    @classmethod
    def validate_override_browsers(cls, v: list[str] | None) -> list[str] | None:
        return validate_browser_names(v) if v is not None else None


class TaskRead(SQLModel):
    id: uuid.UUID
    project_id: uuid.UUID
    created_by: uuid.UUID
    status: str
    progress_total: int
    progress_done: int
    progress_failed: int
    target_urls: list[str]
    override_browser: str | None
    override_browsers: list[str] | None
    override_viewports: list[dict[str, Any]] | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None

    @classmethod
    def from_task(cls, task: Task) -> "TaskRead":
        return cls(
            id=task.id,
            project_id=task.project_id,
            created_by=task.created_by,
            status=task.status,
            progress_total=task.progress_total,
            progress_done=task.progress_done,
            progress_failed=task.progress_failed,
            target_urls=task.target_urls,
            override_browser=task.override_browser,
            override_browsers=task.override_browsers,
            override_viewports=task.override_viewports,
            created_at=task.created_at,
            started_at=task.started_at,
            completed_at=task.completed_at,
        )


class TaskListResponse(SQLModel):
    items: list[TaskRead]
    total: int


class TaskStatsResponse(SQLModel):
    total: int
    pending: int
    running: int
    completed: int
    failed: int
    cancelled: int
    timed_out: int
