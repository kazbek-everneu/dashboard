import asyncio
import uuid
from datetime import datetime
from types import SimpleNamespace

import pytest
from pydantic import ValidationError

from app.core.config import Settings
from app.projects.models import ProjectConfigRead
from app.settings.models import (
    APPLICATION_SETTINGS_SINGLETON_ID,
    DEFAULT_SCREENSHOT_FORMAT,
    DEFAULT_SCREENSHOT_QUALITY,
    ApplicationSettings,
    ApplicationSettingsUpdate,
)
from app.settings.service import (
    get_application_settings,
    resolve_setting,
    update_application_settings,
)
from app.tasks.models import Task, TaskCreate, TaskRead


class _FakeSession:
    def __init__(self) -> None:
        self.store: dict[int, ApplicationSettings] = {}

    async def get(
        self,
        model: type[ApplicationSettings],
        key: int,
    ) -> ApplicationSettings | None:
        assert model is ApplicationSettings
        return self.store.get(key)

    def add(self, settings: ApplicationSettings) -> None:
        self.store[settings.id] = settings

    async def commit(self) -> None:
        return None

    async def refresh(self, settings: ApplicationSettings) -> None:
        return None


def test_application_settings_service_creates_default_singleton() -> None:
    session = _FakeSession()

    settings = asyncio.run(get_application_settings(session))

    assert settings.id == APPLICATION_SETTINGS_SINGLETON_ID
    assert settings.screenshot_format == DEFAULT_SCREENSHOT_FORMAT
    assert settings.screenshot_quality == DEFAULT_SCREENSHOT_QUALITY


def test_settings_parses_comma_separated_cors_origins() -> None:
    settings = Settings(
        DATABASE_URL="postgresql+asyncpg://qa:qa@localhost/qa_test",
        REDIS_URL="redis://localhost:6379",
        SECRET_KEY="test-secret-key-with-enough-length",
        CORS_ALLOWED_ORIGINS="http://localhost:15173, http://127.0.0.1:15173",
    )

    assert settings.cors_allowed_origins == [
        "http://localhost:15173",
        "http://127.0.0.1:15173",
    ]
    assert settings.TASK_JOB_TIMEOUT_SECONDS == 7200


def _settings(**overrides: object) -> Settings:
    return Settings(
        DATABASE_URL="postgresql+asyncpg://qa:qa@localhost/qa_test",
        REDIS_URL="redis://localhost:6379",
        SECRET_KEY="test-secret-key-with-enough-length",
        **overrides,
    )


@pytest.mark.parametrize("endpoint", ["", "ws://browser", "wss://10.0.0.5/"])
def test_browser_ws_endpoint_accepts_base_host(endpoint: str) -> None:
    assert _settings(BROWSER_WS_ENDPOINT=endpoint).BROWSER_WS_ENDPOINT == endpoint


@pytest.mark.parametrize(
    "endpoint",
    [
        # An explicit port is the classic misconfiguration: the worker appends
        # the per-browser port itself, producing ws://host:3000:3001/.
        "ws://10.0.0.5:3000/",
        "http://browser",
        "browser",
        "ws://browser/secret-path",
    ],
)
def test_browser_ws_endpoint_rejects_port_path_or_bad_scheme(endpoint: str) -> None:
    with pytest.raises(ValidationError):
        _settings(BROWSER_WS_ENDPOINT=endpoint)


def test_application_settings_service_updates_quality_and_format() -> None:
    session = _FakeSession()
    asyncio.run(get_application_settings(session))

    updated = asyncio.run(
        update_application_settings(
            session,
            ApplicationSettingsUpdate(screenshot_format="png", screenshot_quality=70),
        )
    )

    assert updated.screenshot_format == "png"
    assert updated.screenshot_quality == 70


def test_project_config_read_excludes_removed_screenshot_fields() -> None:
    config = ProjectConfigRead.model_validate(
        {
            "id": str(uuid.uuid4()),
            "project_id": str(uuid.uuid4()),
            "default_browsers": ["chromium"],
            "viewports": [
                {
                    "width": 1920,
                    "height": 1080,
                    "device_type": "desktop",
                    "browsers": None,
                }
            ],
            "wait_until": "load",
            "timeout": 30000,
        }
    )

    payload = config.model_dump()
    assert "format" not in payload
    assert "quality" not in payload
    assert "full_page" not in payload
    assert "hide_overlays" not in payload
    assert "scroll_to_lazy_load" not in payload


def test_task_models_exclude_removed_format_quality_and_timeout_fields() -> None:
    task = Task(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        status="pending",
        progress_total=0,
        progress_done=0,
        progress_failed=0,
        override_browser=None,
        override_browsers=None,
        override_viewports=None,
        target_urls=["https://example.com/"],
        created_at=datetime.utcnow(),
    )

    read_payload = TaskRead.from_task(task).model_dump()
    create_payload = TaskCreate(project_id=uuid.uuid4()).model_dump()

    assert "override_format" not in read_payload
    assert "override_quality" not in read_payload
    assert "override_timeout" not in read_payload
    assert "worker_job_id" not in read_payload
    assert "override_format" not in create_payload
    assert "override_quality" not in create_payload
    assert "override_timeout" not in create_payload


def test_reuse_browser_context_defaults_on_and_db_override_wins() -> None:
    assert resolve_setting(SimpleNamespace(), "reuse_browser_context") is True
    assert (
        resolve_setting(
            SimpleNamespace(reuse_browser_context=False), "reuse_browser_context"
        )
        is False
    )
