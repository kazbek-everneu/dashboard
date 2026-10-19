import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, field_validator, model_validator
from sqlalchemy import Column, ForeignKey
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlmodel import Field, SQLModel


# ---------------------------------------------------------------------------
# ViewportEntry — validated shape for a single viewport in the JSONB column
# ---------------------------------------------------------------------------

class ViewportEntry(BaseModel):
    """Pydantic model for one viewport entry stored in the JSONB viewports column.

    ``device_type`` is auto-detected from ``width`` when not explicitly supplied.
    ``browsers`` is ``None`` (inherit project default) or a non-empty list of
    browser names for a per-viewport override.
    """

    width: int
    height: int
    device_type: Literal["desktop", "tablet", "mobile"] = "desktop"
    browsers: list[str] | None = None

    @model_validator(mode="before")
    @classmethod
    def auto_detect_device_type(cls, data: Any) -> Any:
        """If ``device_type`` is absent from the raw dict, derive it from ``width``."""
        if isinstance(data, dict) and "device_type" not in data:
            width = data.get("width", 1920)
            if width >= 1024:
                data["device_type"] = "desktop"
            elif width >= 768:
                data["device_type"] = "tablet"
            else:
                data["device_type"] = "mobile"
        return data

    @field_validator("browsers")
    @classmethod
    def validate_browsers(cls, v: list[str] | None) -> list[str] | None:
        valid = {"chromium", "chrome", "firefox", "webkit", "msedge"}
        if v is not None:
            for b in v:
                if b not in valid:
                    raise ValueError(f"Unknown browser: {b!r}")
        return v


# ---------------------------------------------------------------------------
# Project
# ---------------------------------------------------------------------------

class Project(SQLModel, table=True):
    __tablename__ = "projects"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    owner_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    name: str = Field(max_length=255)
    url: str = Field(max_length=2048)
    created_at: datetime = Field(default_factory=datetime.utcnow)


class ProjectCreate(SQLModel):
    url: str


class ProjectUpdate(SQLModel):
    name: str | None = None
    url: str | None = None


class ProjectRead(SQLModel):
    id: uuid.UUID
    owner_id: uuid.UUID
    name: str
    url: str
    created_at: datetime


class ProjectSummary(SQLModel):
    project_id: uuid.UUID
    last_task_status: str | None
    screenshots_count: int


# ---------------------------------------------------------------------------
# ProjectConfig
# ---------------------------------------------------------------------------

def _default_viewports() -> list[dict[str, int]]:
    return [
        {"width": 1920, "height": 1080},
        {"width": 1280, "height": 800},
        {"width": 390, "height": 844},
    ]


class ProjectConfig(SQLModel, table=True):
    __tablename__ = "project_configs"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            unique=True,
            index=True,
        )
    )

    default_browsers: list[Any] = Field(
        default_factory=lambda: ["chromium"],
        sa_column=Column(JSONB, nullable=False, server_default='["chromium"]'),
    )
    viewports: list[dict[str, Any]] = Field(
        default_factory=_default_viewports,
        sa_column=Column(JSONB, nullable=False, server_default="[]"),
    )
    wait_until: str = Field(default="load")
    timeout: int = Field(default=30000)


class ProjectConfigUpdate(SQLModel):
    default_browsers: list[str] | None = None
    viewports: list[dict[str, Any]] | None = None
    wait_until: str | None = None
    timeout: int | None = None


class ProjectConfigRead(SQLModel):
    id: uuid.UUID
    project_id: uuid.UUID
    default_browsers: list[str]
    viewports: list[dict[str, Any]]

    @model_validator(mode="before")
    @classmethod
    def coerce_default_browsers(cls, data: Any) -> Any:
        """Backwards-compat: coerce old string default_browser to a list."""
        if isinstance(data, dict):
            # Old column name
            if "default_browser" in data and "default_browsers" not in data:
                data["default_browsers"] = [data.pop("default_browser")]
            v = data.get("default_browsers")
            if isinstance(v, str):
                data["default_browsers"] = [v]
            elif not v:
                data["default_browsers"] = ["chromium"]
        return data
    wait_until: str
    timeout: int


# ---------------------------------------------------------------------------
# SitemapURL
# ---------------------------------------------------------------------------

class SitemapURL(SQLModel, table=True):
    __tablename__ = "sitemap_urls"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    project_id: uuid.UUID = Field(
        sa_column=Column(
            PGUUID(as_uuid=True),
            ForeignKey("projects.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        )
    )
    url: str = Field(max_length=2048)
    cpt: str = Field(default="page", max_length=100)
    is_enabled: bool = Field(default=True)
    last_fetched_at: datetime = Field(default_factory=datetime.utcnow)


class SitemapURLRead(SQLModel):
    id: uuid.UUID
    project_id: uuid.UUID
    url: str
    cpt: str
    is_enabled: bool
    last_fetched_at: datetime


class SitemapURLUpdate(SQLModel):
    is_enabled: bool


class SitemapURLCreate(SQLModel):
    url: str
    cpt: str = "manual"


class SitemapURLsGrouped(SQLModel):
    cpt: str
    urls: list[SitemapURLRead]


# ---------------------------------------------------------------------------
# Bulk sitemap update
# ---------------------------------------------------------------------------

class SitemapURLBulkUpdateItem(SQLModel):
    url_id: uuid.UUID
    is_enabled: bool


class SitemapURLBulkCreateItem(SQLModel):
    url: str
    cpt: str = "manual"


class SitemapURLBulkUpdate(SQLModel):
    updates: list[SitemapURLBulkUpdateItem] = []
    new_urls: list[SitemapURLBulkCreateItem] = []
    delete_ids: list[uuid.UUID] = []


class SitemapURLBulkResult(SQLModel):
    updated: int
    created: int
    deleted: int
