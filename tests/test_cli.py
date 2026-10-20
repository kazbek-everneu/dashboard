import io
import uuid
from unittest.mock import AsyncMock, patch

import pytest

from app import cli
from app.auth.models import User
from app.core.exceptions import ConflictError


class _FakeSessionContext:
    async def __aenter__(self) -> object:
        return object()

    async def __aexit__(self, *_exc: object) -> bool:
        return False


def _run_create_admin(argv: list[str], stdin: str) -> AsyncMock:
    created = User(name="x", email="x", hashed_password="h", is_admin=True)
    create_account = AsyncMock(return_value=created)
    with (
        patch("sys.stdin", io.StringIO(stdin)),
        patch("app.cli.AsyncSession", return_value=_FakeSessionContext()),
        patch("app.cli.engine") as engine,
        patch("app.cli.create_user_account", create_account),
    ):
        engine.dispose = AsyncMock()
        cli.main(argv)
        engine.dispose.assert_awaited_once()
    return create_account


def test_create_admin_creates_an_admin_account() -> None:
    create_account = _run_create_admin(
        ["create-admin", "--email", " boss@example.com ", "--password-stdin"],
        "s3cret-password\n",
    )

    create_account.assert_awaited_once()
    data = create_account.await_args.args[1]
    assert data.email == "boss@example.com"
    assert data.name == "boss"
    assert data.password == "s3cret-password"
    assert create_account.await_args.kwargs == {"is_admin": True}


def test_create_admin_rejects_short_password() -> None:
    with pytest.raises(SystemExit, match="at least 8"):
        _run_create_admin(
            ["create-admin", "--email", "boss@example.com", "--password-stdin"],
            "short\n",
        )


def test_create_admin_reports_existing_email() -> None:
    with (
        patch("sys.stdin", io.StringIO("s3cret-password\n")),
        patch("app.cli.AsyncSession", return_value=_FakeSessionContext()),
        patch("app.cli.engine") as engine,
        patch(
            "app.cli.create_user_account",
            AsyncMock(side_effect=ConflictError("Email already registered")),
        ),
        pytest.raises(SystemExit, match="Email already registered"),
    ):
        engine.dispose = AsyncMock()
        cli.main(["create-admin", "--email", "boss@example.com", "--password-stdin"])


def test_find_orphan_paths_only_lists_unowned_uuid_entries(tmp_path) -> None:
    live_project, gone_project = str(uuid.uuid4()), str(uuid.uuid4())
    live_task, gone_task, foreign_task = (str(uuid.uuid4()) for _ in range(3))
    for rel in [
        f"{live_project}/{live_task}/root",
        f"{live_project}/{gone_task}/root",
        f"{live_project}/{foreign_task}/root",
        f"{gone_project}/{uuid.uuid4()}/root",
        "not-a-uuid/keep",
        "archives",
    ]:
        (tmp_path / rel).mkdir(parents=True)
    for name in [f"{live_task}.zip", f"{gone_task}.zip", "notes.zip"]:
        (tmp_path / "archives" / name).write_bytes(b"x")

    orphans = cli.find_orphan_paths(
        tmp_path,
        project_ids={live_project, str(uuid.uuid4())},
        # foreign_task exists but belongs to another project
        task_projects={live_task: live_project, foreign_task: str(uuid.uuid4())},
    )

    assert set(orphans) == {
        tmp_path / live_project / gone_task,
        tmp_path / live_project / foreign_task,
        tmp_path / gone_project,
        tmp_path / "archives" / f"{gone_task}.zip",
    }


def test_find_orphan_paths_handles_missing_data_dir(tmp_path) -> None:
    assert cli.find_orphan_paths(tmp_path / "missing", set(), {}) == []
