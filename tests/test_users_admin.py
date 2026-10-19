from datetime import datetime, timedelta

import pytest
from fastapi.routing import APIRoute

from app.auth.dependencies import get_current_admin_user
from app.auth.models import User, UserAdminCreate, UserCreate, UserRead, UserUpdate
from app.auth.service import register_user
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.projects.models import Project
from app.tasks.models import Task
from app.users.router import router as users_router
from app.users.service import create_user, delete_user, update_user, user_to_read


class _FakeResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def first(self) -> object | None:
        return self._values[0] if self._values else None

    def one(self) -> object:
        assert len(self._values) == 1
        return self._values[0]

    def all(self) -> list[object]:
        return self._values


class _FakeUserSession:
    def __init__(
        self,
        users: list[User] | None = None,
        projects: list[Project] | None = None,
        tasks: list[Task] | None = None,
    ) -> None:
        self.users = users or []
        self.projects = projects or []
        self.tasks = tasks or []

    async def exec(self, statement: object) -> _FakeResult:
        compiled = statement.compile()  # type: ignore[attr-defined]
        sql = str(compiled)
        sql_lower = sql.lower()
        params = compiled.params

        if sql_lower.startswith("update projects"):
            old_owner_id = params["owner_id_1"]
            new_owner_id = params["owner_id"]
            for project in self.projects:
                if project.owner_id == old_owner_id:
                    project.owner_id = new_owner_id
            return _FakeResult([])

        if sql_lower.startswith("update tasks"):
            old_creator_id = params["created_by_1"]
            new_creator_id = params["created_by"]
            for task in self.tasks:
                if task.created_by == old_creator_id:
                    task.created_by = new_creator_id
            return _FakeResult([])

        if "count" in sql_lower:
            if "users.is_admin" in sql:
                return _FakeResult([sum(1 for user in self.users if user.is_admin)])
            return _FakeResult([len(self.users)])

        if "users.email" in sql:
            email = next(
                value for key, value in params.items() if key.startswith("email")
            )
            return _FakeResult([user for user in self.users if user.email == email])

        return _FakeResult(self.users)

    async def get(self, model: type[User], key: object) -> User | None:
        assert model is User
        return next((user for user in self.users if user.id == key), None)

    def add(self, user: User) -> None:
        if not any(existing.id == user.id for existing in self.users):
            self.users.append(user)

    async def commit(self) -> None:
        return None

    async def refresh(self, user: User) -> None:
        return None

    async def delete(self, user: User) -> None:
        self.users = [existing for existing in self.users if existing.id != user.id]


def _user(
    email: str,
    *,
    is_admin: bool,
    is_active: bool = True,
    created_offset: int = 0,
) -> User:
    return User(
        name=email.split("@", 1)[0],
        email=email,
        hashed_password="hashed",
        is_admin=is_admin,
        is_active=is_active,
        created_at=datetime.utcnow() + timedelta(seconds=created_offset),
    )


def _project(owner: User, *, name: str = "Project") -> Project:
    return Project(
        owner_id=owner.id,
        name=name,
        url=f"https://{name.lower()}.example.com",
    )


def _task(project: Project, creator: User) -> Task:
    return Task(project_id=project.id, created_by=creator.id)


def _route(path: str, method: str) -> APIRoute:
    for route in users_router.routes:
        if (
            isinstance(route, APIRoute)
            and route.path == path
            and method in route.methods
        ):
            return route
    raise AssertionError(f"Route {method} {path} not found")


def test_list_and_create_user_routes_require_admin_dependency() -> None:
    for route in [
        _route("/", "GET"),
        _route("/", "POST"),
        _route("/{user_id}", "DELETE"),
    ]:
        assert any(
            dependency.call is get_current_admin_user
            for dependency in route.dependant.dependencies
        )


@pytest.mark.asyncio
async def test_first_registered_user_is_admin_and_later_users_are_regular() -> None:
    session = _FakeUserSession()

    first = await register_user(
        session,
        UserCreate(name="First", email="first@example.com", password="password123"),
    )
    second = await register_user(
        session,
        UserCreate(name="Second", email="second@example.com", password="password123"),
    )

    assert first.is_admin is True
    assert second.is_admin is False


@pytest.mark.asyncio
async def test_public_registration_does_not_allow_admin_self_assignment() -> None:
    session = _FakeUserSession([_user("admin@example.com", is_admin=True)])
    payload = UserCreate.model_validate(
        {
            "name": "User",
            "email": "user@example.com",
            "password": "password123",
            "is_admin": True,
        }
    )

    user = await register_user(session, payload)

    assert user.is_admin is False


@pytest.mark.asyncio
async def test_admin_create_user_can_assign_admin_role() -> None:
    session = _FakeUserSession([_user("admin@example.com", is_admin=True)])

    user = await create_user(
        session,
        UserAdminCreate(
            name="Second Admin",
            email="second-admin@example.com",
            password="password123",
            is_admin=True,
        ),
    )

    assert user.is_admin is True


@pytest.mark.asyncio
async def test_admin_dependency_rejects_non_admin_user() -> None:
    user = _user("user@example.com", is_admin=False)

    with pytest.raises(ForbiddenError) as exc_info:
        await get_current_admin_user(current_user=user)

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_non_admin_cannot_change_admin_role() -> None:
    user = _user("user@example.com", is_admin=False)
    session = _FakeUserSession([user])

    with pytest.raises(ForbiddenError) as exc_info:
        await update_user(
            session,
            user.id,
            UserUpdate(is_admin=True),
            current_user=user,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_admin_can_promote_another_user() -> None:
    admin = _user("admin@example.com", is_admin=True)
    user = _user("user@example.com", is_admin=False)
    session = _FakeUserSession([admin, user])

    updated = await update_user(
        session,
        user.id,
        UserUpdate(is_admin=True),
        current_user=admin,
    )

    assert updated.is_admin is True
    assert user.is_admin is True


@pytest.mark.asyncio
async def test_cannot_remove_last_admin() -> None:
    admin = _user("admin@example.com", is_admin=True)
    session = _FakeUserSession([admin])

    with pytest.raises(ConflictError) as exc_info:
        await update_user(
            session,
            admin.id,
            UserUpdate(is_admin=False),
            current_user=admin,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_admin_can_delete_user_and_reassign_projects_and_tasks() -> None:
    admin = _user("admin@example.com", is_admin=True)
    user = _user("user@example.com", is_admin=False)
    target = _user("target@example.com", is_admin=False)
    project = _project(user)
    other_project = _project(target, name="Other")
    task = _task(project, user)
    other_task = _task(other_project, admin)
    session = _FakeUserSession(
        [admin, user, target],
        projects=[project, other_project],
        tasks=[task, other_task],
    )

    await delete_user(
        session,
        user.id,
        reassign_to_user_id=target.id,
        current_user=admin,
    )

    assert user not in session.users
    assert project.owner_id == target.id
    assert other_project.owner_id == target.id
    assert task.created_by == target.id
    assert other_task.created_by == admin.id


@pytest.mark.asyncio
async def test_admin_cannot_delete_self() -> None:
    admin = _user("admin@example.com", is_admin=True)
    target = _user("target@example.com", is_admin=False)
    session = _FakeUserSession([admin, target])

    with pytest.raises(ForbiddenError) as exc_info:
        await delete_user(
            session,
            admin.id,
            reassign_to_user_id=target.id,
            current_user=admin,
        )

    assert exc_info.value.status_code == 403


@pytest.mark.asyncio
async def test_cannot_delete_last_admin() -> None:
    admin = _user("admin@example.com", is_admin=True)
    current_admin = _user("current@example.com", is_admin=True)
    target = _user("target@example.com", is_admin=False)
    session = _FakeUserSession([admin, target])

    with pytest.raises(ConflictError) as exc_info:
        await delete_user(
            session,
            admin.id,
            reassign_to_user_id=target.id,
            current_user=current_admin,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_user_requires_existing_reassignment_target() -> None:
    admin = _user("admin@example.com", is_admin=True)
    user = _user("user@example.com", is_admin=False)
    missing_target = _user("missing@example.com", is_admin=False)
    session = _FakeUserSession([admin, user])

    with pytest.raises(NotFoundError) as exc_info:
        await delete_user(
            session,
            user.id,
            reassign_to_user_id=missing_target.id,
            current_user=admin,
        )

    assert exc_info.value.status_code == 404


@pytest.mark.asyncio
async def test_delete_user_cannot_reassign_to_deleted_user() -> None:
    admin = _user("admin@example.com", is_admin=True)
    user = _user("user@example.com", is_admin=False)
    session = _FakeUserSession([admin, user])

    with pytest.raises(ConflictError) as exc_info:
        await delete_user(
            session,
            user.id,
            reassign_to_user_id=user.id,
            current_user=admin,
        )

    assert exc_info.value.status_code == 409


@pytest.mark.asyncio
async def test_delete_user_requires_active_reassignment_target() -> None:
    admin = _user("admin@example.com", is_admin=True)
    user = _user("user@example.com", is_admin=False)
    inactive_target = _user(
        "inactive@example.com",
        is_admin=False,
        is_active=False,
    )
    session = _FakeUserSession([admin, user, inactive_target])

    with pytest.raises(ConflictError) as exc_info:
        await delete_user(
            session,
            user.id,
            reassign_to_user_id=inactive_target.id,
            current_user=admin,
        )

    assert exc_info.value.status_code == 409


def test_user_read_contract_includes_is_admin() -> None:
    payload = user_to_read(_user("admin@example.com", is_admin=True)).model_dump()

    assert UserRead.model_validate(payload).is_admin is True
