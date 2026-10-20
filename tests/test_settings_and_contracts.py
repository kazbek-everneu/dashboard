import asyncio
import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.auth.dependencies import get_current_admin_user, get_current_user
from app.auth.models import User
from app.core.config import Settings
from app.core.config import settings as env_settings
from app.core.database import get_session
from app.core.exceptions import UnprocessableError
from app.main import create_app
from app.projects.models import (
    ProjectConfig,
    ProjectConfigRead,
    ProjectConfigUpdate,
    ViewportEntry,
)
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
            "default_browsers": ["chrome"],
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
        created_at=datetime.now(UTC),
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


def test_project_config_defaults_to_chrome() -> None:
    assert ProjectConfig(project_id=uuid.uuid4()).default_browsers == ["chrome"]


def test_browser_inputs_alias_legacy_chromium_to_chrome() -> None:
    entry = ViewportEntry(
        width=1280, height=720, browsers=["chromium", "chrome", "firefox"]
    )
    update = ProjectConfigUpdate(default_browsers=["chromium"])
    task = TaskCreate(
        project_id=uuid.uuid4(),
        override_browser="chromium",
        override_browsers=["chromium", "webkit"],
    )

    assert entry.browsers == ["chrome", "firefox"]
    assert update.default_browsers == ["chrome"]
    assert task.override_browser == "chrome"
    assert task.override_browsers == ["chrome", "webkit"]


@pytest.mark.parametrize(
    "build",
    [
        lambda: ViewportEntry(width=1280, height=720, browsers=["netscape"]),
        lambda: ProjectConfigUpdate(default_browsers=["netscape"]),
        lambda: TaskCreate(project_id=uuid.uuid4(), override_browser="netscape"),
        lambda: TaskCreate(project_id=uuid.uuid4(), override_browsers=["netscape"]),
    ],
)
def test_browser_inputs_reject_unknown_names(build) -> None:
    with pytest.raises(ValidationError, match="Unknown browser"):
        build()


# ---------------------------------------------------------------------------
# Task job timeout ceiling
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("value", [7200, 60, None])
def test_settings_accept_a_job_timeout_up_to_the_env_ceiling(value: int | None) -> None:
    session = _FakeSession()
    with patch.object(env_settings, "TASK_JOB_TIMEOUT_SECONDS", 7200):
        updated = asyncio.run(
            update_application_settings(
                session, ApplicationSettingsUpdate(task_job_timeout_seconds=value)
            )
        )

    assert updated.task_job_timeout_seconds == value


def test_settings_reject_a_job_timeout_above_the_env_ceiling() -> None:
    session = _FakeSession()
    asyncio.run(get_application_settings(session))

    with (
        patch.object(env_settings, "TASK_JOB_TIMEOUT_SECONDS", 7200),
        pytest.raises(UnprocessableError) as raised,
    ):
        asyncio.run(
            update_application_settings(
                session,
                ApplicationSettingsUpdate(
                    task_job_timeout_seconds=9200, screenshot_quality=10
                ),
            )
        )

    assert raised.value.status_code == 422
    assert "TASK_JOB_TIMEOUT_SECONDS" in raised.value.detail
    assert "7200" in raised.value.detail
    stored = session.store[APPLICATION_SETTINGS_SINGLETON_ID]
    assert stored.task_job_timeout_seconds is None  # nothing was applied
    assert stored.screenshot_quality == DEFAULT_SCREENSHOT_QUALITY


# ---------------------------------------------------------------------------
# Viewport / project config input validation
# ---------------------------------------------------------------------------


def _errors(build) -> list[tuple[tuple, str]]:
    with pytest.raises(ValidationError) as raised:
        build()
    return [(error["loc"], error["type"]) for error in raised.value.errors()]


@pytest.mark.parametrize(
    ("viewport", "loc", "kind"),
    [
        ({"width": 319, "height": 800}, "width", "greater_than_equal"),
        ({"width": 3841, "height": 800}, "width", "less_than_equal"),
        ({"width": 1280, "height": 399}, "height", "greater_than_equal"),
        ({"width": 1280, "height": 2161}, "height", "less_than_equal"),
        ({"width": "wide", "height": 800}, "width", "int_parsing"),
        ({"width": 1280}, "height", "missing"),
        ({"width": 1280, "height": 800, "device_type": "phone"}, "device_type", "literal_error"),
        ({"width": 1280, "height": 800, "browsers": ["netscape"]}, "browsers", "value_error"),
    ],
)
def test_viewport_inputs_are_validated(viewport: dict, loc: str, kind: str) -> None:
    ok = {"width": 1920, "height": 1080}

    assert _errors(lambda: ProjectConfigUpdate(viewports=[ok, viewport])) == [
        (("viewports", 1, loc), kind)
    ]
    assert _errors(
        lambda: TaskCreate(project_id=uuid.uuid4(), override_viewports=[ok, viewport])
    ) == [(("override_viewports", 1, loc), kind)]


def test_viewport_inputs_reject_duplicate_sizes() -> None:
    viewports = [
        {"width": 1024, "height": 768, "device_type": "desktop"},
        {"width": 1024, "height": 768, "device_type": "tablet"},
    ]

    assert _errors(lambda: ProjectConfigUpdate(viewports=viewports)) == [
        (("viewports",), "duplicate_viewport")
    ]
    with pytest.raises(ValidationError, match="Duplicate viewport 1024x768"):
        TaskCreate(project_id=uuid.uuid4(), override_viewports=viewports)


def test_viewport_inputs_are_stored_normalised() -> None:
    raw = [
        {"width": 1920, "height": 1080, "label": "ignored"},
        {"width": 834, "height": 1112, "browsers": ["chromium", "firefox"]},
        {"width": 1280, "height": 800, "device_type": "tablet", "browsers": None},
    ]

    update = ProjectConfigUpdate(viewports=raw)
    task = TaskCreate(project_id=uuid.uuid4(), override_viewports=raw)

    expected = [
        {"width": 1920, "height": 1080, "device_type": "desktop", "browsers": None},
        {
            "width": 834,
            "height": 1112,
            "device_type": "tablet",
            "browsers": ["chrome", "firefox"],
        },
        {"width": 1280, "height": 800, "device_type": "tablet", "browsers": None},
    ]
    assert update.viewports == expected
    assert task.override_viewports == expected
    assert raw[0] == {"width": 1920, "height": 1080, "label": "ignored"}  # untouched
    assert ProjectConfigUpdate(viewports=None).viewports is None
    assert TaskCreate(project_id=uuid.uuid4()).override_viewports is None


@pytest.mark.parametrize("value", ["load", "domcontentloaded", "networkidle"])
def test_project_config_accepts_the_known_wait_until_values(value: str) -> None:
    assert ProjectConfigUpdate(wait_until=value).wait_until == value


def test_project_config_rejects_unknown_wait_until_and_timeouts() -> None:
    assert _errors(lambda: ProjectConfigUpdate(wait_until="commit")) == [
        (("wait_until",), "literal_error")
    ]
    assert _errors(lambda: ProjectConfigUpdate(timeout=999)) == [
        (("timeout",), "greater_than_equal")
    ]
    assert _errors(lambda: ProjectConfigUpdate(timeout=120001)) == [
        (("timeout",), "less_than_equal")
    ]
    assert ProjectConfigUpdate(timeout=1000).timeout == 1000
    assert ProjectConfigUpdate(timeout=120000).timeout == 120000


def test_viewport_entry_stays_lenient_for_stored_data() -> None:
    stored = {"width": 100, "height": 50}

    entry = ViewportEntry(**stored)

    assert (entry.width, entry.height, entry.device_type) == (100, 50, "mobile")
    assert ViewportEntry.model_validate(stored).device_type == "mobile"
    assert stored == {"width": 100, "height": 50}  # the input is not mutated


# ---------------------------------------------------------------------------
# Validation errors through the app (422 {"detail": [{field, message}]})
# ---------------------------------------------------------------------------


@pytest.fixture
def client() -> TestClient:
    app = create_app()
    user = User(
        id=uuid.uuid4(),
        name="Admin",
        email="admin@example.com",
        hashed_password="hashed",
        is_admin=True,
    )

    async def no_session():
        yield SimpleNamespace()

    app.dependency_overrides[get_current_user] = lambda: user
    app.dependency_overrides[get_current_admin_user] = lambda: user
    app.dependency_overrides[get_session] = no_session
    return TestClient(app)


def test_invalid_project_config_is_a_422_with_field_paths(client: TestClient) -> None:
    response = client.patch(
        f"/projects/{uuid.uuid4()}/config",
        json={
            "viewports": [{"width": 1920, "height": 1080}, {"width": 100, "height": 800}],
            "wait_until": "commit",
        },
    )

    assert response.status_code == 422
    fields = {error["field"] for error in response.json()["detail"]}
    assert fields == {"viewports -> 1 -> width", "wait_until"}
    assert all(error["message"] for error in response.json()["detail"])


def test_duplicate_override_viewports_are_a_422(client: TestClient) -> None:
    response = client.post(
        "/tasks/",
        json={
            "project_id": str(uuid.uuid4()),
            "override_viewports": [
                {"width": 390, "height": 844},
                {"width": 390, "height": 844},
            ],
        },
    )

    assert response.status_code == 422
    (error,) = response.json()["detail"]
    assert error["field"] == "override_viewports"
    assert "Duplicate viewport 390x844" in error["message"]


def test_too_long_job_timeout_is_a_422_naming_the_env_var(client: TestClient) -> None:
    class _Session(_FakeSession):
        async def get(self, model, key):  # type: ignore[override]
            return ApplicationSettings(id=APPLICATION_SETTINGS_SINGLETON_ID)

    async def session():
        yield _Session()

    client.app.dependency_overrides[get_session] = session  # type: ignore[attr-defined]
    with patch.object(env_settings, "TASK_JOB_TIMEOUT_SECONDS", 7200):
        response = client.patch("/settings/", json={"task_job_timeout_seconds": 7201})

    assert response.status_code == 422
    assert "TASK_JOB_TIMEOUT_SECONDS" in response.json()["detail"]
