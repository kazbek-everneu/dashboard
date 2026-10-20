"""Operator commands, run as ``python -m app.cli <command>``.

In Docker: ``docker compose exec api python -m app.cli <command>``.
"""

import argparse
import asyncio
import getpass
import sys

from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import UserCreate
from app.auth.service import create_user_account
from app.core.database import engine
from app.core.exceptions import ConflictError

MIN_PASSWORD_LENGTH = 8


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("Password: ")
    if password != getpass.getpass("Repeat password: "):
        raise SystemExit("Passwords do not match")
    return password


async def create_admin(email: str, name: str, password: str) -> None:
    try:
        async with AsyncSession(engine) as session:
            user = await create_user_account(
                session,
                UserCreate(name=name, email=email, password=password),
                is_admin=True,
            )
            print(f"Created admin {user.email} (id={user.id})")
    except ConflictError as exc:
        raise SystemExit(f"Error: {exc.detail}") from exc
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    commands = parser.add_subparsers(dest="command", required=True)

    create_admin_parser = commands.add_parser(
        "create-admin",
        help="Create an admin account, e.g. the first one on an empty database.",
    )
    create_admin_parser.add_argument("--email", required=True)
    create_admin_parser.add_argument(
        "--name", help="Display name (default: the part of the email before @)."
    )
    create_admin_parser.add_argument(
        "--password-stdin",
        action="store_true",
        help="Read the password from stdin instead of prompting for it.",
    )

    args = parser.parse_args(argv)
    if args.command == "create-admin":
        email = args.email.strip()
        password = _read_password(args.password_stdin)
        if len(password) < MIN_PASSWORD_LENGTH:
            raise SystemExit(
                f"Password must be at least {MIN_PASSWORD_LENGTH} characters"
            )
        name = (args.name or email.split("@", 1)[0]).strip()
        asyncio.run(create_admin(email, name, password))


if __name__ == "__main__":
    main()
