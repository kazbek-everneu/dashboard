"""Tests for task parameter merge logic (task overrides + app settings defaults)."""
import uuid
from datetime import UTC, datetime

from app.projects.models import ProjectConfig
from app.tasks.models import Task
from app.tasks.worker import _build_effective_config


def _make_config(**kwargs) -> ProjectConfig:
    defaults = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        default_browsers=["chrome"],
        viewports=[{"width": 1920, "height": 1080}, {"width": 390, "height": 844}],
        wait_until="load",
        timeout=30000,
    )
    defaults.update(kwargs)
    return ProjectConfig(**defaults)


def _make_task(**kwargs) -> Task:
    defaults = dict(
        id=uuid.uuid4(),
        project_id=uuid.uuid4(),
        created_by=uuid.uuid4(),
        status="pending",
        progress_total=0,
        progress_done=0,
        progress_failed=0,
        target_urls=["https://example.com/"],
        created_at=datetime.now(UTC),
        override_browser=None,
        override_browsers=None,
        override_viewports=None,
    )
    defaults.update(kwargs)
    return Task(**defaults)


class TestBuildEffectiveConfig:
    def test_uses_project_and_global_defaults_when_no_overrides(self) -> None:
        config = _make_config()
        task = _make_task()
        result = _build_effective_config(task, config, "jpeg", 85)
        assert result.browser == "chrome"
        assert result.fmt == "jpeg"
        assert result.quality == 85
        assert result.wait_until == "load"
        assert result.timeout == 30000

    def test_override_browser(self) -> None:
        config = _make_config(default_browsers=["chrome"])
        task = _make_task(override_browser="firefox")
        assert _build_effective_config(task, config, "jpeg", 85).browser == "firefox"

    def test_legacy_chromium_override_resolves_to_chrome(self) -> None:
        config = _make_config(default_browsers=["firefox"])
        task = _make_task(override_browser="chromium")
        assert _build_effective_config(task, config, "jpeg", 85).browser == "chrome"

    def test_override_browsers_take_precedence(self) -> None:
        config = _make_config(default_browsers=["chrome"])
        task = _make_task(override_browsers=["webkit", "firefox"])
        assert _build_effective_config(task, config, "jpeg", 85).browser == "webkit"

    def test_global_format_is_used(self) -> None:
        config = _make_config()
        task = _make_task()
        assert _build_effective_config(task, config, "png", 85).fmt == "png"

    def test_global_quality_is_used(self) -> None:
        config = _make_config()
        task = _make_task()
        assert _build_effective_config(task, config, "jpeg", 60).quality == 60

    def test_timeout_always_comes_from_project_config(self) -> None:
        config = _make_config(timeout=45000)
        task = _make_task()
        assert _build_effective_config(task, config, "jpeg", 85).timeout == 45000
