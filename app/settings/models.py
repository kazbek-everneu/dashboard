from enum import StrEnum

import sqlalchemy as sa
from sqlmodel import Field, SQLModel

DEFAULT_SCREENSHOT_FORMAT = "jpeg"
DEFAULT_SCREENSHOT_QUALITY = 85
APPLICATION_SETTINGS_SINGLETON_ID = 1


class ScreenshotFormat(StrEnum):
    JPEG = "jpeg"
    PNG = "png"


class ApplicationSettings(SQLModel, table=True):
    __tablename__ = "application_settings"

    id: int = Field(default=APPLICATION_SETTINGS_SINGLETON_ID, primary_key=True)
    screenshot_format: ScreenshotFormat = Field(
        default=ScreenshotFormat.JPEG,
        sa_column=sa.Column(
            sa.Enum(
                ScreenshotFormat,
                name="screenshot_format_enum",
                native_enum=False,
                values_callable=lambda enum_cls: [member.value for member in enum_cls],
            ),
            nullable=False,
            server_default=DEFAULT_SCREENSHOT_FORMAT,
        ),
    )
    screenshot_quality: int = Field(default=DEFAULT_SCREENSHOT_QUALITY, ge=0, le=100)

    # Nullable overrides for values that otherwise come from .env (app.core.config).
    # NULL means "inherit the .env default"; a set value overrides it at runtime.
    # See app.settings.service._ENV_FALLBACKS / resolve_setting.
    # Workers & performance
    sem_limit: int | None = Field(default=None, ge=1, le=64)
    task_job_timeout_seconds: int | None = Field(default=None, ge=1)
    reuse_browser_context: bool | None = Field(default=None)
    # Security & sessions
    access_token_expire_minutes: int | None = Field(default=None, ge=1)
    password_reset_token_expire_minutes: int | None = Field(default=None, ge=1)
    password_reset_rate_limit_max: int | None = Field(default=None, ge=1)
    password_reset_rate_limit_window_seconds: int | None = Field(default=None, ge=1)
    # Email / SMTP (credentials SMTP_USERNAME/SMTP_PASSWORD stay in .env only)
    smtp_host: str | None = Field(default=None)
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_from_email: str | None = Field(default=None)
    smtp_use_tls: bool | None = Field(default=None)
    frontend_base_url: str | None = Field(default=None)


class ApplicationSettingsRead(SQLModel):
    """Effective settings: nullable overrides resolved against .env defaults."""

    id: int
    screenshot_format: ScreenshotFormat
    screenshot_quality: int
    sem_limit: int
    task_job_timeout_seconds: int
    reuse_browser_context: bool
    access_token_expire_minutes: int
    password_reset_token_expire_minutes: int
    password_reset_rate_limit_max: int
    password_reset_rate_limit_window_seconds: int
    smtp_host: str | None
    smtp_port: int
    smtp_from_email: str
    smtp_use_tls: bool
    frontend_base_url: str


class ApplicationSettingsUpdate(SQLModel):
    screenshot_format: ScreenshotFormat | None = None
    screenshot_quality: int | None = Field(default=None, ge=0, le=100)
    sem_limit: int | None = Field(default=None, ge=1, le=64)
    task_job_timeout_seconds: int | None = Field(default=None, ge=1)
    reuse_browser_context: bool | None = None
    access_token_expire_minutes: int | None = Field(default=None, ge=1)
    password_reset_token_expire_minutes: int | None = Field(default=None, ge=1)
    password_reset_rate_limit_max: int | None = Field(default=None, ge=1)
    password_reset_rate_limit_window_seconds: int | None = Field(default=None, ge=1)
    smtp_host: str | None = None
    smtp_port: int | None = Field(default=None, ge=1, le=65535)
    smtp_from_email: str | None = None
    smtp_use_tls: bool | None = None
    frontend_base_url: str | None = None
