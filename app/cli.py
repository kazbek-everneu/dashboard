"""Operator commands, run as ``python -m app.cli <command>``.

In Docker: ``docker compose exec api python -m app.cli <command>``.
"""

import argparse
import asyncio
import getpass
import os
import sys
import uuid
from pathlib import Path

from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import UserCreate
from app.auth.service import create_user_account
from app.core.config import settings
from app.core.database import engine
from app.core.exceptions import ConflictError
from app.projects.models import Project
from app.screenshots.storage import ARCHIVES_DIRNAME, remove_paths
from app.tasks.models import Task

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


def _is_uuid(name: str) -> bool:
    try:
        return str(uuid.UUID(name)) == name
    except ValueError:
        return False


def find_orphan_paths(
    data_dir: Path, project_ids: set[str], task_projects: dict[str, str]
) -> list[Path]:
    """Screenshot directories and archives under *data_dir* no row owns.

    *task_projects* maps every existing task id to its project id. Only
    UUID-named entries are considered, so nothing else in DATA_DIR is touched.
    """
    if not data_dir.is_dir():
        return []
    orphans: list[Path] = []
    for project_entry in sorted(data_dir.iterdir()):
        if not project_entry.is_dir() or not _is_uuid(project_entry.name):
            continue
        if project_entry.name not in project_ids:
            orphans.append(project_entry)
            continue
        for task_entry in sorted(project_entry.iterdir()):
            if (
                task_entry.is_dir()
                and _is_uuid(task_entry.name)
                and task_projects.get(task_entry.name) != project_entry.name
            ):
                orphans.append(task_entry)
    archives = data_dir / ARCHIVES_DIRNAME
    if archives.is_dir():
        for archive in sorted(archives.glob("*.zip")):
            if _is_uuid(archive.stem) and archive.stem not in task_projects:
                orphans.append(archive)
    return orphans


def _size_bytes(path: Path) -> int:
    if path.is_file():
        return path.stat().st_size
    return sum(
        (Path(root) / name).stat().st_size
        for root, _dirs, files in os.walk(path)
        for name in files
    )


async def cleanup_files(apply: bool) -> None:
    try:
        async with AsyncSession(engine) as session:
            project_rows = (await session.exec(select(Project.id))).all()
            task_rows = (await session.exec(select(Task.id, Task.project_id))).all()
    finally:
        await engine.dispose()
    project_ids = {str(pid) for pid in project_rows}
    task_projects = {str(tid): str(pid) for tid, pid in task_rows}

    orphans = find_orphan_paths(Path(settings.DATA_DIR), project_ids, task_projects)
    total = 0
    for path in orphans:
        size = _size_bytes(path)
        total += size
        print(f"{size / 1024 / 1024:10.1f} MB  {path}")
    print(f"{len(orphans)} orphaned path(s), {total / 1024 / 1024:.1f} MB in total")
    if not orphans:
        return
    if apply:
        remove_paths(orphans)
        print("Deleted.")
    else:
        print("Dry run: nothing deleted. Re-run with --apply to delete them.")


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

    cleanup_parser = commands.add_parser(
        "cleanup-files",
        help=(
            "List screenshot files and archives left behind by deleted projects "
            "or tasks; delete them with --apply."
        ),
    )
    cleanup_parser.add_argument(
        "--apply", action="store_true", help="Delete the files instead of listing them."
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
    elif args.command == "cleanup-files":
        asyncio.run(cleanup_files(args.apply))


if __name__ == "__main__":
    main()
