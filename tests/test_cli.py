import io
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
