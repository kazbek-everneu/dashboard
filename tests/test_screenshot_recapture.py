import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.auth.models import User
from app.core.config import settings
from app.core.exceptions import ConflictError
from app.projects.models import Project, ProjectConfig
from app.screenshots.capture.report import CaptureReport
from app.screenshots.capture.types import CaptureOutcome, CaptureResult
from app.screenshots.models import Screenshot
from app.screenshots.service import (
    EffectiveConfig,
    build_recapture_job_id,
    request_screenshot_recapture,
)
from app.screenshots.service import capture_screenshot as _real_capture_screenshot
from app.tasks.limiter import CaptureLimiter
from app.tasks.models import Task
from app.tasks.worker import (
    _RECONNECT_ATTEMPTS,
    _RECONNECT_DELAYS_S,
    _recapture_device_type,
    recapture_screenshot,
    reset_orphaned_recapturing,
)

# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _user() -> User:
    return User(name="User", email="user@example.com", hashed_password="hashed")


def _project(user: User) -> Project:
    return Project(
        id=uuid.uuid4(), owner_id=user.id, name="Project", url="https://example.com"
    )


def _config(project_id: uuid.UUID) -> ProjectConfig:
    return ProjectConfig(
        project_id=project_id,
        default_browsers=["chrome"],
        viewports=[{"width": 1280, "height": 720}],
        wait_until="load",
        timeout=30000,
    )


def _task(project: Project, user: User, **kwargs: object) -> Task:
    defaults: dict[str, object] = {
        "project_id": project.id,
        "created_by": user.id,
        "status": "failed",
        "target_urls": ["https://example.com/"],
        "progress_total": 1,
        "progress_done": 0,
        "progress_failed": 1,
    }
    defaults.update(kwargs)
    return Task(**defaults)


def _screenshot(task: Task, **kwargs: object) -> Screenshot:
    # A legacy screenshot from the retired chromium browser: re-capture keeps its
    # browser name (same file path) and _launch_browser serves it with chrome.
    defaults: dict[str, object] = {
        "task_id": task.id,
        "url": "https://example.com/",
        "browser": "chromium",
        "width": 1280,
        "height": 720,
        "file_format": "jpeg",
        "file_path": "proj/task/home/1280x720_chromium.jpg",
        "signed_url": "/files/task/home/1280x720_chromium.jpg?token=abc",
        "status": "failed",
        "error_message": "boom",
        "duration_seconds": 0.0,
    }
    defaults.update(kwargs)
    return Screenshot(**defaults)


# ---------------------------------------------------------------------------
# Request-side fakes
# ---------------------------------------------------------------------------

class _FakeRequestSession:
    def __init__(self, screenshot: Screenshot, task: Task) -> None:
        self.screenshot = screenshot
        self.task = task

    async def get(self, model: type, key: object) -> object | None:
        if model is Screenshot:
            return self.screenshot if self.screenshot.id == key else None
        if model is Task:
            return self.task if self.task.id == key else None
        return None

    def add(self, _item: object) -> None:
        return None

    async def commit(self) -> None:
        return None

    async def refresh(self, _item: object) -> None:
        return None


class _FakeArqJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class _FakeRedis:
    def __init__(self, return_none: bool = False) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...], dict[str, object]]] = []
        self.return_none = return_none

    async def enqueue_job(self, function: str, *args: object, **kwargs: object):
        self.enqueued.append((function, args, kwargs))
        if self.return_none:
            return None
        return _FakeArqJob(str(kwargs["_job_id"]))


async def test_request_recapture_marks_recapturing_and_enqueues() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="failed")
    screenshot = _screenshot(task)
    session = _FakeRequestSession(screenshot, task)
    redis = _FakeRedis()

    with (
        patch(
            "app.projects.service.get_project",
            new=AsyncMock(return_value=project),
        ),
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=redis)),
    ):
        result = await request_screenshot_recapture(session, screenshot.id, user)

    assert result.status == "recapturing"
    assert result.error_message is None
    assert len(redis.enqueued) == 1
    fn, args, kwargs = redis.enqueued[0]
    assert fn == "recapture_screenshot"
    assert args == (str(screenshot.id),)
    # Unique job id per request, prefixed for log/grep readability.
    assert kwargs["_job_id"].startswith(build_recapture_job_id(screenshot.id) + ":")


async def test_request_recapture_rejected_while_task_running() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="running")
    screenshot = _screenshot(task, status="ok")
    session = _FakeRequestSession(screenshot, task)

    with patch(
        "app.projects.service.get_project", new=AsyncMock(return_value=project)
    ):
        with pytest.raises(ConflictError, match="running"):
            await request_screenshot_recapture(session, screenshot.id, user)

    # Status untouched — no enqueue attempted.
    assert screenshot.status == "ok"


async def test_request_recapture_allowed_on_ok_screenshot() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="completed", progress_done=1, progress_failed=0)
    screenshot = _screenshot(task, status="ok", error_message=None)
    session = _FakeRequestSession(screenshot, task)
    redis = _FakeRedis()

    with (
        patch(
            "app.projects.service.get_project",
            new=AsyncMock(return_value=project),
        ),
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=redis)),
    ):
        result = await request_screenshot_recapture(session, screenshot.id, user)

    assert result.status == "recapturing"
    assert len(redis.enqueued) == 1


async def test_request_recapture_restores_status_when_enqueue_returns_none() -> None:
    # Regression: a None return (e.g. ARQ de-dup against a cached completed job)
    # must NOT leave the row stuck in "recapturing".
    user = _user()
    project = _project(user)
    task = _task(project, user, status="completed", progress_done=1, progress_failed=0)
    screenshot = _screenshot(task, status="ok", error_message=None)
    session = _FakeRequestSession(screenshot, task)
    redis = _FakeRedis(return_none=True)

    with (
        patch(
            "app.projects.service.get_project",
            new=AsyncMock(return_value=project),
        ),
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=redis)),
    ):
        with pytest.raises(ConflictError):
            await request_screenshot_recapture(session, screenshot.id, user)

    # Previous status restored, not stuck in "recapturing".
    assert screenshot.status == "ok"


# ---------------------------------------------------------------------------
# Worker-side fakes
# ---------------------------------------------------------------------------

class _FakeScalarResult:
    def __init__(self, value: object | None = None, values: list[object] | None = None):
        self._value = value
        self._values = values

    def scalars(self) -> "_FakeScalarResult":
        return self

    def first(self) -> object:
        return self._value

    def all(self) -> list[object]:
        return self._values if self._values is not None else []


class _FakeWorkerSession:
    def __init__(self, task: Task, config: ProjectConfig, screenshots: list[Screenshot]):
        self.task = task
        self.config = config
        self.screenshots = screenshots

    async def __aenter__(self) -> "_FakeWorkerSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, model: type, key: object) -> object | None:
        if model is Task:
            return self.task if self.task.id == key else None
        if model is Screenshot:
            return next((s for s in self.screenshots if s.id == key), None)
        return None

    async def execute(self, _statement: object) -> _FakeScalarResult:
        return _FakeScalarResult(self.config)

    async def exec(self, _statement: object) -> _FakeScalarResult:
        return _FakeScalarResult(values=list(self.screenshots))

    def add(self, _item: object) -> None:
        return None

    async def commit(self) -> None:
        return None


class _FakeBrowser:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _FakePlaywrightContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_args: object) -> None:
        return None


async def test_worker_recaptures_in_place_and_flips_task_to_completed() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="failed", progress_done=0, progress_failed=1)
    screenshot = _screenshot(task, status="recapturing", error_message=None)
    original_id = screenshot.id
    original_created = screenshot.created_at
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    browser = _FakeBrowser()
    launch = AsyncMock(return_value=browser)

    async def fake_capture(**kwargs: object) -> Screenshot:
        existing = kwargs["existing"]
        config = kwargs["config"]
        assert isinstance(existing, Screenshot)
        assert isinstance(config, EffectiveConfig)
        # The row keeps its file (capture_screenshot writes to its file_path)
        # and format; the legacy chromium row is captured by chrome.
        assert existing.file_path == "proj/task/home/1280x720_chromium.jpg"
        assert config.browser == "chrome"
        assert config.fmt == "jpeg"
        assert kwargs["viewport"] == {"width": 1280, "height": 720}
        existing.status = "ok"
        existing.error_message = None
        existing.created_at = datetime(2030, 1, 1, tzinfo=UTC)
        return existing

    with (
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg", screenshot_quality=85
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(),
        ),
        patch("app.tasks.worker._launch_browser", new=launch),
        patch("app.tasks.worker.capture_screenshot", new=fake_capture),
    ):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    assert launch.await_args.args[1] == "chromium"  # aliased inside _launch_browser
    assert screenshot.id == original_id  # row updated in place
    assert screenshot.created_at != original_created  # cache-bust bump
    assert screenshot.status == "ok"
    assert task.progress_done == 1
    assert task.progress_failed == 0
    assert task.status == "completed"  # last failure fixed
    assert browser.closed is True


async def test_worker_recapture_failure_keeps_task_failed() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="failed", progress_done=0, progress_failed=1)
    screenshot = _screenshot(task, status="recapturing")
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    browser = _FakeBrowser()

    async def fake_capture(**kwargs: object) -> Screenshot:
        existing = kwargs["existing"]
        existing.status = "failed"
        existing.error_message = "still broken"
        return existing

    with (
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg", screenshot_quality=85
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(),
        ),
        patch(
            "app.tasks.worker._launch_browser",
            new=AsyncMock(return_value=browser),
        ),
        patch("app.tasks.worker.capture_screenshot", new=fake_capture),
    ):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    assert screenshot.status == "failed"
    assert task.progress_done == 0
    assert task.progress_failed == 1
    assert task.status == "failed"  # not flipped


async def test_worker_recapture_timeout_marks_failed() -> None:
    # A slow/hung capture must not leave the row pinned in "recapturing": the
    # per-recapture wall-clock bound flips it to "failed".
    user = _user()
    project = _project(user)
    task = _task(project, user, status="failed", progress_done=0, progress_failed=1)
    screenshot = _screenshot(task, status="recapturing", error_message=None)
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    browser = _FakeBrowser()

    async def slow_capture(**_kwargs: object) -> Screenshot:
        await asyncio.sleep(1)  # far longer than the patched timeout below
        return screenshot

    with (
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch.object(settings, "SCREENSHOT_RECAPTURE_TIMEOUT_SECONDS", 0.05),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg", screenshot_quality=85
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(),
        ),
        patch(
            "app.tasks.worker._launch_browser",
            new=AsyncMock(return_value=browser),
        ),
        patch("app.tasks.worker.capture_screenshot", new=slow_capture),
    ):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    assert screenshot.status == "failed"
    assert "timed out" in (screenshot.error_message or "")
    assert task.status == "failed"
    assert browser.closed is True


async def test_reset_orphaned_recapturing_resets_rows() -> None:
    # Startup self-heal: a row stranded in "recapturing" by a crash/redeploy is
    # flipped to "failed" and the parent task's counters are refreshed.
    user = _user()
    project = _project(user)
    task = _task(project, user, status="failed", progress_done=0, progress_failed=1)
    screenshot = _screenshot(task, status="recapturing", error_message=None)
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])

    with patch("app.tasks.worker.AsyncSession", return_value=session):
        reset = await reset_orphaned_recapturing(object())

    assert reset == 1
    assert screenshot.status == "failed"
    assert screenshot.error_message == "Re-capture interrupted by worker restart."
    assert task.progress_failed == 1
    assert task.status == "failed"


async def test_reset_orphaned_recapturing_noop_when_none_stuck() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="completed", progress_done=1, progress_failed=0)
    session = _FakeWorkerSession(task, _config(project.id), [])

    with patch("app.tasks.worker.AsyncSession", return_value=session):
        reset = await reset_orphaned_recapturing(object())

    assert reset == 0


async def test_worker_recapture_takes_capture_slot_before_connecting() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="failed", progress_done=0, progress_failed=1)
    screenshot = _screenshot(task, status="recapturing", error_message=None)
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    browser = _FakeBrowser()
    events: list[str] = []

    class _OrderLimiter(CaptureLimiter):
        async def acquire(self) -> None:
            events.append("slot")
            await super().acquire()

    async def fake_launch(*_args: object) -> _FakeBrowser:
        events.append("launch")
        return browser

    async def fake_capture(**kwargs: object) -> Screenshot:
        existing = kwargs["existing"]
        assert isinstance(existing, Screenshot)
        existing.status = "ok"
        return existing

    limiter = _OrderLimiter(2)
    with (
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg", screenshot_quality=85
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(),
        ),
        patch("app.tasks.worker._launch_browser", new=fake_launch),
        patch("app.tasks.worker.capture_screenshot", new=fake_capture),
    ):
        await recapture_screenshot(
            {"db_engine": object(), "capture_limiter": limiter}, str(screenshot.id)
        )

    assert events == ["slot", "launch"]
    assert limiter.in_use == 0
    assert screenshot.status == "ok"



# ---------------------------------------------------------------------------
# Device profile, browser provider, deadline, resets
# ---------------------------------------------------------------------------


class _VersionedBrowser:
    def __init__(self, version: str = "149.0.7827.114") -> None:
        self.version = version
        self.connected = True
        self.closed = False

    def is_connected(self) -> bool:
        return self.connected

    async def close(self) -> None:
        self.closed = True


@contextmanager
def _recapture_env(
    session: _FakeWorkerSession, launch: object, capture: object
) -> Iterator[None]:
    with (
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg", screenshot_quality=85
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(),
        ),
        patch("app.tasks.worker._launch_browser", new=launch),
        patch("app.tasks.worker.capture_screenshot", new=capture),
    ):
        yield


def test_recapture_device_type_resolution_order() -> None:
    user = _user()
    task = _task(_project(user), user)
    legacy = _screenshot(task, width=834, height=1112, device_type=None)
    viewports_task = [
        {"width": "bad"},  # malformed stored entries are skipped
        {"width": 834, "height": 1112, "device_type": "desktop"},
    ]
    viewports_project = [{"width": 834, "height": 1112, "device_type": "mobile"}]

    # 1. the row's own device type
    own = _screenshot(task, width=834, height=1112, device_type="mobile")
    assert _recapture_device_type(own, viewports_task, viewports_project) == "mobile"
    # 2. the task's viewport with the same size, then the project's
    assert _recapture_device_type(legacy, viewports_task, viewports_project) == "desktop"
    assert _recapture_device_type(legacy, None, viewports_project) == "mobile"
    # ... whose device type is auto-detected when it has none
    assert (
        _recapture_device_type(
            _screenshot(task, width=1280, height=1112, device_type=None),
            [{"width": 1280, "height": 1112}],
        )
        == "desktop"
    )
    # 3. the width
    other_size = [{"width": 834, "height": 900, "device_type": "desktop"}]
    assert _recapture_device_type(legacy, other_size, None) == "tablet"
    assert _recapture_device_type(legacy) == "tablet"


async def test_recapture_builds_the_device_profile_and_passes_a_deadline() -> None:
    user = _user()
    project = _project(user)
    task = _task(
        project,
        user,
        override_viewports=[{"width": 390, "height": 844, "device_type": "mobile"}],
    )
    screenshot = _screenshot(
        task, width=390, height=844, status="recapturing", device_type=None
    )
    config = _config(project.id)
    session = _FakeWorkerSession(task, config, [screenshot])
    browser = _VersionedBrowser()
    seen: dict[str, object] = {}

    async def fake_capture(**kwargs: object) -> Screenshot:
        seen.update(kwargs)
        seen["now"] = asyncio.get_running_loop().time()
        existing = kwargs["existing"]
        assert isinstance(existing, Screenshot)
        existing.status = "ok"
        return existing

    with (
        _recapture_env(session, AsyncMock(return_value=browser), fake_capture),
        patch.object(settings, "SCREENSHOT_RECAPTURE_TIMEOUT_SECONDS", 300),
    ):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    profile = seen["profile"]
    assert seen["device_type"] == "mobile"
    assert profile.device_type == "mobile"
    assert profile.is_mobile is True
    # Built with the connected browser's version.
    assert "Chrome/149.0.0.0" in (profile.user_agent or "")
    assert "Mobile Safari" in (profile.user_agent or "")
    assert seen["existing"] is screenshot
    assert callable(seen["browser_provider"])
    deadline = seen["deadline_at"]
    assert isinstance(deadline, float)
    # now + SCREENSHOT_RECAPTURE_TIMEOUT_SECONDS - 15, set right before the call
    assert 284.0 < deadline - seen["now"] <= 285.0
    assert browser.closed is True


async def test_recapture_provider_reconnects_a_dropped_browser() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user)
    screenshot = _screenshot(task, status="recapturing")
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    first, second = _VersionedBrowser(), _VersionedBrowser()
    launch = AsyncMock(side_effect=[first, second])
    provided: list[object] = []

    async def fake_capture(**kwargs: object) -> Screenshot:
        provider = kwargs["browser_provider"]
        provided.append(await provider())  # connected: the same browser
        first.connected = False
        provided.append(await provider())  # dropped: reconnected
        provided.append(await provider())  # the replacement is connected
        existing = kwargs["existing"]
        existing.status = "ok"
        return existing

    with _recapture_env(session, launch, fake_capture):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    assert provided == [first, second, second]
    reconnect = launch.await_args_list[1]
    assert reconnect.args[1] == "chromium"
    assert reconnect.kwargs == {
        "attempts": _RECONNECT_ATTEMPTS,
        "delays": _RECONNECT_DELAYS_S,
    }
    assert _RECONNECT_ATTEMPTS == 6
    assert _RECONNECT_DELAYS_S == (1, 2, 3, 4, 5)
    assert first.closed is True  # the stale browser
    assert second.closed is True  # the one in use at the end


async def test_recapture_provider_returns_none_when_reconnect_fails() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user)
    screenshot = _screenshot(task, status="recapturing")
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    browser = _VersionedBrowser()
    launch = AsyncMock(side_effect=[browser, ConnectionError("refused")])
    provided: list[object] = []

    async def fake_capture(**kwargs: object) -> Screenshot:
        browser.connected = False
        provided.append(await kwargs["browser_provider"]())
        existing = kwargs["existing"]
        existing.status = "failed"
        existing.error_message = "Browser connection lost; reconnect failed"
        return existing

    with _recapture_env(session, launch, fake_capture):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    assert provided == [None]
    assert screenshot.status == "failed"
    assert browser.closed is True


async def test_recapture_of_a_legacy_mobile_row_keeps_its_file(tmp_path: Path) -> None:
    # Q12: an old 390x844 row (device_type NULL, path from the old naming) is
    # re-captured with mobile emulation into the same file.
    user = _user()
    project = _project(user)
    task = _task(project, user, status="completed", progress_done=1, progress_failed=0)
    old_path = f"{project.id}/{task.id}/list/390x844_chrome.jpg"
    screenshot = _screenshot(
        task,
        url="https://example.com/list?page=2",
        browser="chrome",
        width=390,
        height=844,
        file_path=old_path,
        status="recapturing",
        device_type=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])
    session.refresh = AsyncMock()  # type: ignore[attr-defined]
    report = CaptureReport()
    report.warn("overlays_hidden", "Hid 1 overlay")
    outcome = CaptureOutcome(data=b"img", duration=1.0, report=report)
    pipeline = AsyncMock(return_value=CaptureResult(outcome, None, report, 1, 1.0))
    written: list[str] = []

    with (
        _recapture_env(session, AsyncMock(return_value=_VersionedBrowser()), None),
        patch("app.tasks.worker.capture_screenshot", new=_real_capture_screenshot),
        patch("app.screenshots.capture.pipeline.capture_with_retries", pipeline),
        patch(
            "app.screenshots.service._write_file",
            side_effect=lambda path, _data: written.append(path),
        ),
        patch("app.tasks.worker.remove_paths"),
    ):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    profile = pipeline.await_args.kwargs["profile"]
    assert profile.device_type == "mobile" and profile.is_mobile is True
    assert written == [old_path]
    assert screenshot.file_path == old_path
    assert screenshot.status == "ok"
    assert screenshot.device_type == "mobile"
    assert screenshot.created_at > datetime(2026, 1, 1, tzinfo=UTC)
    assert [w["code"] for w in screenshot.warnings] == ["overlays_hidden"]
    assert screenshot.capture_meta["device"]["type"] == "mobile"


async def test_recapture_failure_reset_clears_warnings_and_meta() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user)
    old_warnings = [{"code": "dom_unstable", "level": "warning", "message": "x"}]
    screenshot = _screenshot(
        task,
        status="recapturing",
        device_type="mobile",
        warnings=old_warnings,
        capture_meta={"v": 1},
    )
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])

    async def broken_capture(**_kwargs: object) -> Screenshot:
        raise RuntimeError("context crashed")

    with _recapture_env(session, AsyncMock(return_value=_FakeBrowser()), broken_capture):
        await recapture_screenshot({"db_engine": object()}, str(screenshot.id))

    assert screenshot.status == "failed"
    assert screenshot.error_message == "Re-capture failed: context crashed"
    assert screenshot.warnings == [] and screenshot.warnings is not old_warnings
    assert screenshot.capture_meta is None
    assert screenshot.device_type == "mobile"  # unchanged


async def test_reset_orphaned_recapturing_clears_warnings_and_meta() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user)
    screenshot = _screenshot(
        task,
        status="recapturing",
        device_type="tablet",
        warnings=[{"code": "overlays_hidden", "level": "info", "message": "x"}],
        capture_meta={"v": 1},
    )
    session = _FakeWorkerSession(task, _config(project.id), [screenshot])

    with patch("app.tasks.worker.AsyncSession", return_value=session):
        await reset_orphaned_recapturing(object())

    assert screenshot.status == "failed"
    assert screenshot.warnings == []
    assert screenshot.capture_meta is None
    assert screenshot.device_type == "tablet"


async def test_request_recapture_keeps_warnings_and_meta() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="completed", progress_done=1, progress_failed=0)
    warnings = [{"code": "overlays_hidden", "level": "info", "message": "x"}]
    meta = {"v": 1, "attempts": 1}
    screenshot = _screenshot(
        task,
        status="ok",
        error_message=None,
        device_type="mobile",
        warnings=warnings,
        capture_meta=meta,
    )
    session = _FakeRequestSession(screenshot, task)

    with (
        patch(
            "app.projects.service.get_project",
            new=AsyncMock(return_value=project),
        ),
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=_FakeRedis())),
    ):
        result = await request_screenshot_recapture(session, screenshot.id, user)

    assert result.status == "recapturing"
    assert result.warnings == warnings
    assert result.capture_meta == meta
    assert result.device_type == "mobile"
