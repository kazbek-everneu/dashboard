from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from app.core.config import settings as env_settings
from app.core.exceptions import UnprocessableError
from app.settings.models import (
    APPLICATION_SETTINGS_SINGLETON_ID,
    ApplicationSettings,
    ApplicationSettingsUpdate,
)

# Maps nullable DB override columns to their .env (app.core.config.Settings) source.
# When the DB column is NULL the .env value is used; a set value overrides it.
_ENV_FALLBACKS: dict[str, str] = {
    "sem_limit": "SEM_LIMIT",
    "task_job_timeout_seconds": "TASK_JOB_TIMEOUT_SECONDS",
    "reuse_browser_context": "REUSE_BROWSER_CONTEXT",
    "access_token_expire_minutes": "ACCESS_TOKEN_EXPIRE_MINUTES",
    "password_reset_token_expire_minutes": "PASSWORD_RESET_TOKEN_EXPIRE_MINUTES",
    "password_reset_rate_limit_max": "PASSWORD_RESET_RATE_LIMIT_MAX",
    "password_reset_rate_limit_window_seconds": (
        "PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS"
    ),
    "smtp_host": "SMTP_HOST",
    "smtp_port": "SMTP_PORT",
    "smtp_from_email": "SMTP_FROM_EMAIL",
    "smtp_use_tls": "SMTP_USE_TLS",
    "frontend_base_url": "FRONTEND_BASE_URL",
}


def resolve_setting(row: object, name: str) -> Any:
    """Return the effective value for *name*: the DB override if set, else .env.

    Uses getattr-with-default so it tolerates lightweight row stand-ins (e.g.
    test doubles) that omit the newer override attributes.
    """
    value = getattr(row, name, None)
    if value is not None:
        return value
    return getattr(env_settings, _ENV_FALLBACKS[name])


async def get_effective_settings(session: AsyncSession) -> dict[str, Any]:
    """Effective settings: persisted overrides merged over .env defaults."""
    row = await get_application_settings(session)
    data: dict[str, Any] = {
        "id": row.id,
        "screenshot_format": row.screenshot_format,
        "screenshot_quality": row.screenshot_quality,
    }
    for name in _ENV_FALLBACKS:
        data[name] = resolve_setting(row, name)
    return data


async def get_application_settings(session: AsyncSession) -> ApplicationSettings:
    settings = await session.get(ApplicationSettings, APPLICATION_SETTINGS_SINGLETON_ID)
    if settings is not None:
        return settings

    settings = ApplicationSettings(id=APPLICATION_SETTINGS_SINGLETON_ID)
    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings


def _check_task_job_timeout(value: int | None) -> None:
    """Refuse a task job timeout above TASK_JOB_TIMEOUT_SECONDS.

    That env value is the worker's ARQ ``job_timeout``: ARQ cancels every job
    at it, so a longer setting could never take effect.
    """
    ceiling = env_settings.TASK_JOB_TIMEOUT_SECONDS
    if value is not None and value > ceiling:
        raise UnprocessableError(
            f"Task job timeout cannot exceed {ceiling} s: the worker stops every "
            f"job at TASK_JOB_TIMEOUT_SECONDS ({ceiling}). Raise "
            "TASK_JOB_TIMEOUT_SECONDS in .env and restart the worker to allow "
            "a longer timeout."
        )


async def update_application_settings(
    session: AsyncSession,
    data: ApplicationSettingsUpdate,
) -> ApplicationSettings:
    updates = data.model_dump(exclude_unset=True)
    _check_task_job_timeout(updates.get("task_job_timeout_seconds"))
    settings = await get_application_settings(session)
    for field, value in updates.items():
        setattr(settings, field, value)

    session.add(settings)
    await session.commit()
    await session.refresh(settings)
    return settings

