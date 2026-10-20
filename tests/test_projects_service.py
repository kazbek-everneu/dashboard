import uuid

import pytest

from app.auth.models import User
from app.core.exceptions import ConflictError, ForbiddenError
from app.projects.models import (
    Project,
    ProjectConfig,
    ProjectConfigUpdate,
    ProjectUpdate,
)
from app.projects.service import (
    _project_name_from_url,
    delete_project,
    update_project,
    update_project_config,
)


def test_project_name_from_url_strips_leading_www() -> None:
    assert _project_name_from_url("https://www.example.com/path") == "example.com"


def test_project_name_from_url_keeps_other_subdomains() -> None:
    assert _project_name_from_url("https://shop.example.com/path") == "shop.example.com"


class _FakeResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def first(self) -> object | None:
        return self._values[0] if self._values else None


class _FakeProjectSession:
    """Answers ``exec`` calls in order from *exec_results*."""

    def __init__(
        self, project: Project, exec_results: list[list[object]] | None = None
    ) -> None:
        self.project = project
        self.exec_results = list(exec_results or [])
        self.deleted: list[object] = []
        self.committed = False

    async def get(self, model: type, key: object) -> object | None:
        assert model is Project
        return self.project if self.project.id == key else None

    async def exec(self, _statement: object) -> _FakeResult:
        return _FakeResult(self.exec_results.pop(0) if self.exec_results else [])

    def add(self, _item: object) -> None:
        return None

    async def delete(self, item: object) -> None:
        self.deleted.append(item)

    async def commit(self) -> None:
        self.committed = True

    async def refresh(self, _item: object) -> None:
        return None


def _user(*, is_admin: bool = False) -> User:
    return User(
        id=uuid.uuid4(),
        name="User",
        email=f"{uuid.uuid4()}@example.com",
        hashed_password="hashed",
        is_admin=is_admin,
    )


def _project(owner: User) -> Project:
    return Project(
        id=uuid.uuid4(), owner_id=owner.id, name="example.com", url="https://example.com"
    )


@pytest.mark.parametrize("actor", ["owner", "admin"])
async def test_owner_and_admin_can_rename_project(actor: str) -> None:
    owner = _user()
    project = _project(owner)
    user = owner if actor == "owner" else _user(is_admin=True)
    session = _FakeProjectSession(project)

    updated = await update_project(
        session, project.id, ProjectUpdate(name="Renamed"), user  # type: ignore[arg-type]
    )

    assert updated.name == "Renamed"


async def test_other_user_cannot_change_or_delete_project() -> None:
    project = _project(_user())
    stranger = _user()
    session = _FakeProjectSession(
        project, exec_results=[[ProjectConfig(project_id=project.id)]]
    )

    with pytest.raises(ForbiddenError):
        await update_project(
            session, project.id, ProjectUpdate(name="Mine now"), stranger  # type: ignore[arg-type]
        )
    with pytest.raises(ForbiddenError):
        await update_project_config(
            session,  # type: ignore[arg-type]
            project.id,
            ProjectConfigUpdate(timeout=1000),
            stranger,
        )
    with pytest.raises(ForbiddenError):
        await delete_project(session, project.id, stranger)  # type: ignore[arg-type]

    assert project.name == "example.com"
    assert session.deleted == []
    assert session.committed is False


async def test_owner_can_update_project_config() -> None:
    owner = _user()
    project = _project(owner)
    config = ProjectConfig(project_id=project.id, timeout=30000)
    session = _FakeProjectSession(project, exec_results=[[config]])

    updated = await update_project_config(
        session, project.id, ProjectConfigUpdate(timeout=45000), owner  # type: ignore[arg-type]
    )

    assert updated.timeout == 45000


async def test_delete_project_refuses_while_a_capture_is_running() -> None:
    owner = _user()
    project = _project(owner)
    # 1st exec: active task lookup, 2nd: recapturing screenshot lookup
    session = _FakeProjectSession(project, exec_results=[[uuid.uuid4()], []])

    with pytest.raises(ConflictError, match="still running"):
        await delete_project(session, project.id, owner)  # type: ignore[arg-type]

    assert session.deleted == []


async def test_delete_project_refuses_while_a_recapture_is_running() -> None:
    owner = _user()
    project = _project(owner)
    session = _FakeProjectSession(project, exec_results=[[], [uuid.uuid4()]])

    with pytest.raises(ConflictError, match="still running"):
        await delete_project(session, project.id, owner)  # type: ignore[arg-type]


async def test_admin_can_delete_idle_project() -> None:
    project = _project(_user())
    session = _FakeProjectSession(project, exec_results=[[], []])

    await delete_project(session, project.id, _user(is_admin=True))  # type: ignore[arg-type]

    assert session.deleted == [project]
    assert session.committed is True
