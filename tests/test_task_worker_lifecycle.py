import asyncio
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from arq.jobs import JobStatus

from app.auth.models import User
from app.core.exceptions import ConflictError
from app.projects.models import Project, ProjectConfig
from app.screenshots.models import Screenshot
from app.screenshots.service import EffectiveConfig
from app.tasks.limiter import CaptureLimiter
from app.tasks.models import Task, TaskCreate
from app.tasks.service import _abort_worker_job, cancel_task, create_task, delete_task
from app.tasks.worker import (
    _CHROMIUM_LAUNCH_ARGS,
    _launch_browser,
    run_screenshot_task,
)


def test_chromium_launch_args_keep_local_shm_flag() -> None:
    # Local launches run without the browser container's `ipc: host`, so the
    # /dev/shm workaround must stay in the worker-side args.
    assert "--disable-dev-shm-usage" in _CHROMIUM_LAUNCH_ARGS


class _FakeResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def first(self) -> object | None:
        return self._values[0] if self._values else None


class _FakeTaskSession:
    def __init__(self, tasks: list[Task] | None = None) -> None:
        self.tasks = tasks or []

    async def exec(self, _statement: object) -> _FakeResult:
        return _FakeResult([])

    async def get(self, model: type[Task], key: object) -> Task | None:
        assert model is Task
        return next((task for task in self.tasks if task.id == key), None)

    def add(self, task: Task) -> None:
        if not any(existing.id == task.id for existing in self.tasks):
            self.tasks.append(task)

    async def commit(self) -> None:
        return None

    async def rollback(self) -> None:
        return None

    async def refresh(self, task: Task) -> None:
        return None

    async def delete(self, task: Task) -> None:
        self.tasks = [existing for existing in self.tasks if existing.id != task.id]


class _FakeArqJob:
    def __init__(self, job_id: str) -> None:
        self.job_id = job_id


class _FakeRedis:
    def __init__(self) -> None:
        self.enqueued: list[tuple[str, tuple[object, ...], dict[str, object]]] = []

    async def enqueue_job(
        self,
        function: str,
        *args: object,
        **kwargs: object,
    ) -> _FakeArqJob:
        self.enqueued.append((function, args, kwargs))
        return _FakeArqJob(str(kwargs["_job_id"]))


def _user() -> User:
    return User(
        name="User",
        email="user@example.com",
        hashed_password="hashed",
    )


def _project(user: User) -> Project:
    return Project(
        id=uuid.uuid4(),
        owner_id=user.id,
        name="Project",
        url="https://example.com",
    )


def _config(project_id: uuid.UUID) -> ProjectConfig:
    return ProjectConfig(
        project_id=project_id,
        default_browsers=["chromium"],
        viewports=[{"width": 1280, "height": 720}],
        wait_until="load",
        timeout=30000,
    )


def _task(project: Project, user: User, **kwargs: object) -> Task:
    defaults: dict[str, object] = {
        "project_id": project.id,
        "created_by": user.id,
        "status": "pending",
        "target_urls": ["https://example.com/"],
    }
    defaults.update(kwargs)
    return Task(**defaults)


async def test_create_task_saves_worker_job_id_and_enqueues_with_custom_id() -> None:
    user = _user()
    project = _project(user)
    session = _FakeTaskSession()
    redis = _FakeRedis()

    with (
        patch("app.tasks.service.get_project", new=AsyncMock(return_value=project)),
        patch(
            "app.tasks.service._load_config",
            new=AsyncMock(return_value=_config(project.id)),
        ),
        patch(
            "app.tasks.service._resolve_target_urls",
            new=AsyncMock(return_value=["https://example.com/"]),
        ),
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=redis)),
    ):
        result = await create_task(
            session,
            TaskCreate(project_id=project.id, target_urls=["https://example.com/"]),
            user,
        )

    task = session.tasks[0]
    assert task.worker_job_id == f"screenshot-task:{task.id}"
    assert redis.enqueued == [
        (
            "run_screenshot_task",
            (str(task.id),),
            {"_job_id": task.worker_job_id},
        )
    ]
    assert result.id == task.id
    assert "worker_job_id" not in result.model_dump()


async def test_cancel_task_aborts_worker_job_before_marking_cancelled() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="running", worker_job_id="job-1")
    session = _FakeTaskSession([task])
    abort_worker_job = AsyncMock()
    set_cancel_marker = AsyncMock()

    with (
        patch("app.tasks.service.get_project", new=AsyncMock(return_value=project)),
        patch("app.tasks.service._abort_worker_job", new=abort_worker_job),
        patch("app.tasks.service._set_user_cancel_marker", new=set_cancel_marker),
    ):
        result = await cancel_task(session, task.id, user)

    set_cancel_marker.assert_awaited_once_with(task.id)
    abort_worker_job.assert_awaited_once_with(
        "job-1",
        failure_detail="Task is still stopping. Please try again.",
    )
    assert result.status == "cancelled"
    assert task.status == "cancelled"
    assert task.completed_at is not None


async def test_cancel_task_keeps_legacy_db_cancel_fallback() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="running", worker_job_id=None)
    session = _FakeTaskSession([task])
    abort_worker_job = AsyncMock()

    with (
        patch("app.tasks.service.get_project", new=AsyncMock(return_value=project)),
        patch("app.tasks.service._abort_worker_job", new=abort_worker_job),
    ):
        await cancel_task(session, task.id, user)

    abort_worker_job.assert_not_called()
    assert task.status == "cancelled"


async def test_cancel_task_does_not_mark_cancelled_when_abort_is_unconfirmed() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="running", worker_job_id="job-1")
    session = _FakeTaskSession([task])
    clear_cancel_marker = AsyncMock()

    with (
        patch("app.tasks.service.get_project", new=AsyncMock(return_value=project)),
        patch("app.tasks.service._set_user_cancel_marker", new=AsyncMock()),
        patch("app.tasks.service._clear_user_cancel_marker", new=clear_cancel_marker),
        patch(
            "app.tasks.service._abort_worker_job",
            new=AsyncMock(side_effect=ConflictError("Task is still stopping.")),
        ),
    ):
        with pytest.raises(ConflictError):
            await cancel_task(session, task.id, user)

    assert task.status == "running"
    clear_cancel_marker.assert_awaited_once_with(task.id)


async def test_delete_task_aborts_leftover_worker_job_before_delete() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="cancelled", worker_job_id="job-1")
    session = _FakeTaskSession([task])
    abort_worker_job = AsyncMock()

    with (
        patch("app.tasks.service.get_project", new=AsyncMock(return_value=project)),
        patch("app.tasks.service._abort_worker_job", new=abort_worker_job),
    ):
        await delete_task(session, task.id, user)

    abort_worker_job.assert_awaited_once_with(
        "job-1",
        failure_detail="Task is still stopping. Please try again.",
    )
    assert session.tasks == []


async def test_delete_task_blocks_when_leftover_worker_job_is_still_active() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="cancelled", worker_job_id="job-1")
    session = _FakeTaskSession([task])

    with (
        patch("app.tasks.service.get_project", new=AsyncMock(return_value=project)),
        patch(
            "app.tasks.service._abort_worker_job",
            new=AsyncMock(side_effect=ConflictError("Task is still stopping.")),
        ),
    ):
        with pytest.raises(ConflictError):
            await delete_task(session, task.id, user)

    assert session.tasks == [task]


class _FakeWorkerJob:
    def __init__(
        self,
        status: JobStatus,
        *,
        abort_result: bool = True,
        abort_error: Exception | None = None,
    ) -> None:
        self.status_value = status
        self.abort_result = abort_result
        self.abort_error = abort_error
        self.abort_kwargs: dict[str, object] | None = None

    async def status(self) -> JobStatus:
        return self.status_value

    async def abort(self, **kwargs: object) -> bool:
        self.abort_kwargs = kwargs
        if self.abort_error:
            raise self.abort_error
        return self.abort_result


async def test_abort_worker_job_calls_arq_abort_for_active_jobs() -> None:
    fake_job = _FakeWorkerJob(JobStatus.queued)

    with (
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=object())),
        patch("app.tasks.service.Job", return_value=fake_job),
    ):
        await _abort_worker_job("job-1", failure_detail="still stopping")

    assert fake_job.abort_kwargs == {"timeout": 15.0, "poll_delay": 0.2}


async def test_abort_worker_job_timeout_raises_conflict() -> None:
    fake_job = _FakeWorkerJob(JobStatus.in_progress, abort_error=TimeoutError())

    with (
        patch("app.core.redis.get_redis_pool", new=AsyncMock(return_value=object())),
        patch("app.tasks.service.Job", return_value=fake_job),
    ):
        with pytest.raises(ConflictError, match="still stopping"):
            await _abort_worker_job("job-1", failure_detail="still stopping")


class _FakeScalarResult:
    def __init__(
        self,
        value: object | None = None,
        values: list[object] | None = None,
    ) -> None:
        self._value = value
        self._values = values

    def scalars(self) -> "_FakeScalarResult":
        return self

    def first(self) -> object:
        return self._value

    def all(self) -> list[object]:
        if self._values is not None:
            return self._values
        return [] if self._value is None else [self._value]


class _FakeWorkerSession:
    def __init__(self, task: Task, config: ProjectConfig) -> None:
        self.task = task
        self.config = config
        self.screenshots: list[Screenshot] = []

    async def __aenter__(self) -> "_FakeWorkerSession":
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def get(self, model: type[Task], key: object) -> Task | None:
        assert model is Task
        return self.task if self.task.id == key else None

    async def execute(self, _statement: object) -> _FakeScalarResult:
        return _FakeScalarResult(self.config)

    async def exec(self, _statement: object) -> _FakeScalarResult:
        return _FakeScalarResult(values=list(self.screenshots))

    def add(self, item: Task | Screenshot) -> None:
        if isinstance(item, Screenshot):
            self.screenshots.append(item)
        return None

    async def commit(self) -> None:
        return None


class _FakeBrowserContext:
    def __init__(self) -> None:
        self.closed = False

    async def add_init_script(self, *_args: object) -> None:
        return None

    async def close(self) -> None:
        self.closed = True


class _FakeBrowser:
    def __init__(self, connected: bool = True) -> None:
        self.closed = False
        self.connected = connected
        self.contexts: list[_FakeBrowserContext] = []

    def is_connected(self) -> bool:
        return self.connected

    async def new_context(self, **_kwargs: object) -> _FakeBrowserContext:
        context = _FakeBrowserContext()
        self.contexts.append(context)
        return context

    async def close(self) -> None:
        self.closed = True


class _FakeBrowserType:
    """Fake BrowserType. ``connect_plan`` / ``launch_plan`` hold one entry per
    upcoming call: an Exception to raise, or None to succeed. An empty plan
    always succeeds."""

    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.launch_kwargs: list[dict[str, object]] = []
        self.connect_calls: list[tuple[str, dict[str, object]]] = []
        self.connect_plan: list[Exception | None] = []
        self.launch_plan: list[Exception | None] = []

    async def launch(self, **kwargs: object) -> _FakeBrowser:
        self.launch_kwargs.append(kwargs)
        if self.launch_plan:
            step = self.launch_plan.pop(0)
            if step is not None:
                raise step
        return self.browser

    async def connect(self, endpoint: str, **kwargs: object) -> _FakeBrowser:
        self.connect_calls.append((endpoint, kwargs))
        if self.connect_plan:
            step = self.connect_plan.pop(0)
            if step is not None:
                raise step
        return self.browser


class _FakePlaywright:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.chromium = _FakeBrowserType(browser)
        self.firefox = _FakeBrowserType(browser)
        self.webkit = _FakeBrowserType(browser)


class _FakePlaywrightContext:
    def __init__(self, browser: _FakeBrowser) -> None:
        self.browser = browser
        self.playwright = _FakePlaywright(browser)

    async def __aenter__(self) -> _FakePlaywright:
        return self.playwright

    async def __aexit__(self, *_args: object) -> None:
        return None


async def test_worker_does_not_restart_already_cancelled_task() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="cancelled")
    config = _config(project.id)

    with (
        patch(
            "app.tasks.worker.AsyncSession",
            return_value=_FakeWorkerSession(task, config),
        ),
        patch("app.tasks.worker.async_playwright") as async_playwright,
    ):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    async_playwright.assert_not_called()
    assert task.status == "cancelled"
    assert task.completed_at is not None


async def test_worker_marks_task_cancelled_and_closes_browser_on_abort() -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="pending")
    config = _config(project.id)
    browser = _FakeBrowser()
    session = _FakeWorkerSession(task, config)

    with (
        patch("app.tasks.worker.settings.SEM_LIMIT", 1),
        patch(
            "app.tasks.worker.AsyncSession",
            return_value=session,
        ),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg",
                    screenshot_quality=85,
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(browser),
        ),
        patch(
            "app.tasks.worker.capture_screenshot",
            new=AsyncMock(side_effect=asyncio.CancelledError()),
        ),
        patch(
            "app.tasks.worker.is_user_cancel_requested",
            new=AsyncMock(return_value=True),
        ),
        patch("app.tasks.worker._clear_user_cancel_marker", new=AsyncMock()),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_screenshot_task({"db_engine": object()}, str(task.id))

    assert task.status == "cancelled"
    assert task.completed_at is not None
    assert browser.closed is True
    assert session.screenshots == []


async def test_worker_marks_task_timed_out_and_records_missing_screenshots() -> None:
    user = _user()
    project = _project(user)
    task = _task(
        project,
        user,
        status="pending",
        target_urls=["https://example.com/a/", "https://example.com/b/"],
        progress_total=2,
    )
    config = _config(project.id)
    browser = _FakeBrowser()
    session = _FakeWorkerSession(task, config)
    monotonic_values = [0.0]
    capture_calls = 0

    def fake_monotonic() -> float:
        if monotonic_values:
            return monotonic_values.pop(0)
        return 2.0

    async def capture_once_then_cancel(**kwargs: object) -> Screenshot:
        nonlocal capture_calls
        capture_calls += 1
        if capture_calls > 1:
            raise asyncio.CancelledError()

        viewport = kwargs["viewport"]
        config = kwargs["config"]
        assert isinstance(viewport, dict)
        assert isinstance(config, EffectiveConfig)
        record = Screenshot(
            task_id=task.id,
            url=str(kwargs["url"]),
            browser=config.browser,
            width=int(viewport["width"]),
            height=int(viewport["height"]),
            file_format=config.fmt,
            file_path="existing.jpg",
            signed_url="signed",
            status="ok",
            duration_seconds=1.0,
        )
        kwargs_session = kwargs["session"]
        assert isinstance(kwargs_session, _FakeWorkerSession)
        kwargs_session.add(record)
        return record

    with (
        patch("app.tasks.worker.settings.SEM_LIMIT", 1),
        patch("app.tasks.worker.settings.TASK_JOB_TIMEOUT_SECONDS", 1),
        patch("app.tasks.worker.time.monotonic", new=fake_monotonic),
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg",
                    screenshot_quality=85,
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(browser),
        ),
        patch("app.tasks.worker.capture_screenshot", new=capture_once_then_cancel),
        patch(
            "app.tasks.worker.is_user_cancel_requested",
            new=AsyncMock(return_value=False),
        ),
    ):
        with pytest.raises(asyncio.CancelledError):
            await run_screenshot_task({"db_engine": object()}, str(task.id))

    assert task.status == "timed_out"
    assert task.progress_done == 1
    assert task.progress_failed == 1
    assert task.progress_total == 2
    assert task.completed_at is not None
    assert browser.closed is True
    assert len(session.screenshots) == 2
    placeholder = next(s for s in session.screenshots if s.status == "failed")
    assert placeholder.error_message == "Task timed out before this capture completed."
    assert placeholder.duration_seconds == 0.0


@pytest.mark.parametrize(
    ("browser_name", "browser_type", "expected_launch_kwargs"),
    [
        (
            "chromium",
            "chromium",
            {"headless": True, "args": list(_CHROMIUM_LAUNCH_ARGS)},
        ),
        (
            "chrome",
            "chromium",
            {
                "headless": True,
                "args": list(_CHROMIUM_LAUNCH_ARGS),
                "channel": "chrome",
            },
        ),
        (
            "msedge",
            "chromium",
            {
                "headless": True,
                "args": list(_CHROMIUM_LAUNCH_ARGS),
                "channel": "msedge",
            },
        ),
        ("firefox", "firefox", {"headless": True}),
        ("webkit", "webkit", {"headless": True}),
    ],
)
async def test_worker_launches_browser_with_expected_kwargs(
    browser_name: str,
    browser_type: str,
    expected_launch_kwargs: dict[str, object],
) -> None:
    user = _user()
    project = _project(user)
    task = _task(project, user, status="pending")
    config = _config(project.id)
    config.default_browsers = [browser_name]
    browser = _FakeBrowser()
    playwright_context = _FakePlaywrightContext(browser)

    with (
        patch("app.tasks.worker.settings.SEM_LIMIT", 1),
        patch(
            "app.tasks.worker.AsyncSession",
            return_value=_FakeWorkerSession(task, config),
        ),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg",
                    screenshot_quality=85,
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=playwright_context,
        ),
        patch(
            "app.tasks.worker.capture_screenshot",
            new=AsyncMock(return_value=SimpleNamespace(status="ok")),
        ),
    ):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    launched_type = getattr(playwright_context.playwright, browser_type)
    assert launched_type.launch_kwargs == [expected_launch_kwargs]
    assert browser.closed is True


async def test_launch_browser_launches_locally_when_endpoint_empty() -> None:
    browser = _FakeBrowser()
    pw = _FakePlaywright(browser)

    with patch("app.tasks.worker.settings.BROWSER_WS_ENDPOINT", ""):
        result = await _launch_browser(pw, "chrome")

    assert result is browser
    assert pw.chromium.connect_calls == []
    assert pw.chromium.launch_kwargs == [
        {
            "headless": True,
            "args": list(_CHROMIUM_LAUNCH_ARGS),
            "channel": "chrome",
        }
    ]


@pytest.mark.parametrize(
    ("browser_name", "browser_type", "port"),
    [
        ("chromium", "chromium", 3000),
        ("chrome", "chromium", 3001),
        ("msedge", "chromium", 3002),
        ("firefox", "firefox", 3003),
        ("webkit", "webkit", 3004),
    ],
)
async def test_launch_browser_connects_to_remote_when_endpoint_set(
    browser_name: str,
    browser_type: str,
    port: int,
) -> None:
    browser = _FakeBrowser()
    pw = _FakePlaywright(browser)

    with (
        patch("app.tasks.worker.settings.BROWSER_WS_ENDPOINT", "ws://browser"),
        patch("app.tasks.worker.settings.BROWSER_CONNECT_TIMEOUT_MS", 5000),
    ):
        result = await _launch_browser(pw, browser_name)

    assert result is browser
    connected = getattr(pw, browser_type)
    # Remote mode connects to the per-browser port (each browser/channel is its
    # own launchServer); channel + launch args are baked into that server.
    assert connected.connect_calls == [(f"ws://browser:{port}/", {"timeout": 5000})]
    assert connected.launch_kwargs == []


async def test_launch_browser_appends_shared_secret_ws_path() -> None:
    browser = _FakeBrowser()
    pw = _FakePlaywright(browser)

    with (
        patch("app.tasks.worker.settings.BROWSER_WS_ENDPOINT", "ws://browser"),
        patch("app.tasks.worker.settings.BROWSER_WS_PATH", "/s3cret/"),
        patch("app.tasks.worker.settings.BROWSER_CONNECT_TIMEOUT_MS", 5000),
    ):
        result = await _launch_browser(pw, "chromium")

    assert result is browser
    assert pw.chromium.connect_calls == [
        ("ws://browser:3000/s3cret", {"timeout": 5000})
    ]


async def test_launch_browser_retries_connect_until_success() -> None:
    browser = _FakeBrowser()
    pw = _FakePlaywright(browser)
    pw.chromium.connect_plan = [ConnectionError("refused"), ConnectionError("refused")]

    with (
        patch("app.tasks.worker.settings.BROWSER_WS_ENDPOINT", "ws://browser"),
        patch("app.tasks.worker._CONNECT_RETRY_DELAY_S", 0),
    ):
        result = await _launch_browser(pw, "chromium")

    assert result is browser
    assert len(pw.chromium.connect_calls) == 3


async def test_launch_browser_raises_after_connect_retries_exhausted() -> None:
    browser = _FakeBrowser()
    pw = _FakePlaywright(browser)
    pw.chromium.connect_plan = [ConnectionError("refused")] * 3

    with (
        patch("app.tasks.worker.settings.BROWSER_WS_ENDPOINT", "ws://browser"),
        patch("app.tasks.worker._CONNECT_RETRY_DELAY_S", 0),
    ):
        with pytest.raises(ConnectionError):
            await _launch_browser(pw, "chromium")

    assert len(pw.chromium.connect_calls) == 3


async def test_worker_reconnects_when_browser_drops_mid_task() -> None:
    user = _user()
    project = _project(user)
    task = _task(
        project,
        user,
        status="pending",
        target_urls=["https://example.com/a/", "https://example.com/b/"],
        progress_total=2,
    )
    config = _config(project.id)
    browser = _FakeBrowser()
    playwright_context = _FakePlaywrightContext(browser)
    capture_calls = 0

    async def capture_then_drop_connection(**kwargs: object) -> SimpleNamespace:
        nonlocal capture_calls
        capture_calls += 1
        if capture_calls == 1:
            # Simulate the browser connection dying after the first capture.
            browser.connected = False
        return SimpleNamespace(status="ok")

    with (
        patch("app.tasks.worker.settings.SEM_LIMIT", 1),
        patch(
            "app.tasks.worker.AsyncSession",
            return_value=_FakeWorkerSession(task, config),
        ),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg",
                    screenshot_quality=85,
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=playwright_context,
        ),
        patch(
            "app.tasks.worker.capture_screenshot",
            new=capture_then_drop_connection,
        ),
    ):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    # Initial launch + one reconnect for the second combo.
    assert len(playwright_context.playwright.chromium.launch_kwargs) == 2
    assert capture_calls == 2
    assert task.status == "completed"


async def test_worker_marks_task_failed_when_reconnect_fails() -> None:
    user = _user()
    project = _project(user)
    task = _task(
        project,
        user,
        status="pending",
        target_urls=["https://example.com/a/", "https://example.com/b/"],
        progress_total=2,
    )
    config = _config(project.id)
    browser = _FakeBrowser()
    playwright_context = _FakePlaywrightContext(browser)
    # Initial launch succeeds; the reconnect attempt fails.
    playwright_context.playwright.chromium.launch_plan = [
        None,
        RuntimeError("browser host down"),
    ]
    capture_calls = 0

    async def capture_then_drop_connection(**kwargs: object) -> SimpleNamespace:
        nonlocal capture_calls
        capture_calls += 1
        browser.connected = False
        return SimpleNamespace(status="ok")

    with (
        patch("app.tasks.worker.settings.SEM_LIMIT", 1),
        patch(
            "app.tasks.worker.AsyncSession",
            return_value=_FakeWorkerSession(task, config),
        ),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg",
                    screenshot_quality=85,
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=playwright_context,
        ),
        patch(
            "app.tasks.worker.capture_screenshot",
            new=capture_then_drop_connection,
        ),
    ):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    # The second combo is abandoned instead of failing through capture retries.
    assert capture_calls == 1
    assert task.status == "failed"
    assert task.completed_at is not None


# ---------------------------------------------------------------------------
# Shared browser contexts and the worker-wide capture limiter
# ---------------------------------------------------------------------------


@contextmanager
def _patched_run(
    session: _FakeWorkerSession,
    browser: _FakeBrowser,
    capture: object,
) -> Iterator[None]:
    with (
        patch("app.tasks.worker.settings.SEM_LIMIT", 1),
        patch("app.tasks.worker.AsyncSession", return_value=session),
        patch(
            "app.tasks.worker.get_application_settings",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    screenshot_format="jpeg",
                    screenshot_quality=85,
                )
            ),
        ),
        patch(
            "app.tasks.worker.async_playwright",
            return_value=_FakePlaywrightContext(browser),
        ),
        patch("app.tasks.worker.capture_screenshot", new=capture),
    ):
        yield


def _two_viewport_task(urls: list[str]) -> tuple[Task, ProjectConfig]:
    user = _user()
    project = _project(user)
    task = _task(
        project,
        user,
        target_urls=urls,
        override_viewports=[
            {"width": 1280, "height": 720},
            {"width": 390, "height": 844},
        ],
    )
    return task, _config(project.id)


async def test_worker_shares_one_context_per_url_and_browser() -> None:
    task, config = _two_viewport_task(
        ["https://example.com/a/", "https://example.com/b/"]
    )
    browser = _FakeBrowser()
    calls: list[tuple[str, int, object]] = []

    async def capture(**kwargs: object) -> SimpleNamespace:
        viewport = kwargs["viewport"]
        assert isinstance(viewport, dict)
        calls.append((str(kwargs["url"]), viewport["width"], kwargs["context"]))
        return SimpleNamespace(status="ok")

    with _patched_run(_FakeWorkerSession(task, config), browser, capture):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    assert [(url, width) for url, width, _ in calls] == [
        ("https://example.com/a/", 1280),
        ("https://example.com/a/", 390),
        ("https://example.com/b/", 1280),
        ("https://example.com/b/", 390),
    ]
    context_a, context_b = browser.contexts
    assert [context for *_, context in calls] == [
        context_a,
        context_a,
        context_b,
        context_b,
    ]
    assert context_a.closed and context_b.closed
    assert task.status == "completed"


async def test_worker_uses_isolated_contexts_when_reuse_disabled() -> None:
    task, config = _two_viewport_task(["https://example.com/a/"])
    browser = _FakeBrowser()
    contexts: list[object] = []

    async def capture(**kwargs: object) -> SimpleNamespace:
        contexts.append(kwargs["context"])
        return SimpleNamespace(status="ok")

    with (
        _patched_run(_FakeWorkerSession(task, config), browser, capture),
        patch("app.tasks.worker.settings.REUSE_BROWSER_CONTEXT", False),
    ):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    assert contexts == [None, None]
    assert browser.contexts == []


async def test_worker_replaces_shared_context_after_failed_capture() -> None:
    user = _user()
    project = _project(user)
    task = _task(
        project,
        user,
        override_viewports=[
            {"width": 1280, "height": 720},
            {"width": 1024, "height": 768},
            {"width": 390, "height": 844},
        ],
    )
    browser = _FakeBrowser()
    contexts: list[object] = []

    async def capture(**kwargs: object) -> SimpleNamespace:
        contexts.append(kwargs["context"])
        return SimpleNamespace(status="failed" if len(contexts) == 1 else "ok")

    with _patched_run(_FakeWorkerSession(task, _config(project.id)), browser, capture):
        await run_screenshot_task({"db_engine": object()}, str(task.id))

    first, second = browser.contexts
    assert contexts == [first, second, second]
    assert first.closed and second.closed


async def test_worker_takes_a_shared_limiter_slot_per_capture() -> None:
    task, config = _two_viewport_task(
        ["https://example.com/a/", "https://example.com/b/"]
    )

    class _SpyLimiter(CaptureLimiter):
        def __init__(self) -> None:
            super().__init__(1)
            self.acquired = 0

        async def acquire(self) -> None:
            self.acquired += 1
            await super().acquire()

    limiter = _SpyLimiter()

    async def capture(**_kwargs: object) -> SimpleNamespace:
        assert limiter.in_use == 1
        return SimpleNamespace(status="ok")

    with (
        _patched_run(_FakeWorkerSession(task, config), _FakeBrowser(), capture),
        patch("app.tasks.worker.settings.SEM_LIMIT", 3),
    ):
        await run_screenshot_task(
            {"db_engine": object(), "capture_limiter": limiter}, str(task.id)
        )

    assert limiter.acquired == 4
    assert limiter.limit == 3  # resized to the task's effective sem_limit
    assert limiter.in_use == 0
