import asyncio
import uuid
import zipfile
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import NotFoundError
from app.projects.service import get_project
from app.screenshots.models import Screenshot
from app.screenshots.storage import abs_file_path, generate_archive_signed_url
from app.tasks.models import (
    Task,
    TaskCreate,
    TaskListResponse,
    TaskRead,
    TaskStatsResponse,
)
from app.tasks.service import (
    cancel_task,
    create_task,
    delete_task,
    get_task,
    get_task_stats,
    list_tasks,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# Archive helpers (kept module-level so run_in_executor can call them)
# ---------------------------------------------------------------------------

class ArchiveResponse(BaseModel):
    archive_url: str
    file_size: int


def _url_to_folder(url: str) -> str:
    """Convert a page URL to a ZIP folder name (flat, no nesting)."""
    path = urlparse(url).path.strip("/")
    return path.replace("/", "_") or "homepage"


def _create_archive_sync(
    task_id: str,
    screenshots: list[Screenshot],
    archives_dir: Path,
) -> Path:
    """Build a ZIP archive from *screenshots* and write it to *archives_dir*.

    Runs synchronously — call via run_in_executor to avoid blocking the event loop.
    Missing files are silently skipped.
    """
    archive_path = archives_dir / f"{task_id}.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in screenshots:
            abs_path = abs_file_path(s.file_path)
            if not abs_path.exists():
                continue
            folder = _url_to_folder(s.url)
            ext = "jpg" if s.file_format == "jpeg" else s.file_format
            filename = f"{s.width}x{s.height}_{s.browser}.{ext}"
            arcname = f"{task_id}/{folder}/{filename}"
            zf.write(abs_path, arcname)
    return archive_path


@router.post("/", response_model=TaskRead, status_code=201)
async def create_task_endpoint(
    data: TaskCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return await create_task(session, data, current_user)


@router.get("/", response_model=TaskListResponse)
async def list_tasks_endpoint(
    project_id: uuid.UUID | None = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskListResponse:
    return await list_tasks(session, current_user, project_id, skip=skip, limit=limit)


@router.get("/stats", response_model=TaskStatsResponse)
async def task_stats_endpoint(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskStatsResponse:
    return await get_task_stats(session, current_user)


@router.get("/{task_id}", response_model=TaskRead)
async def get_task_endpoint(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return await get_task(session, task_id, current_user)


@router.post("/{task_id}/cancel", response_model=TaskRead)
async def cancel_task_endpoint(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> TaskRead:
    return await cancel_task(session, task_id, current_user)


@router.delete("/{task_id}", status_code=204)
async def delete_task_endpoint(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    await delete_task(session, task_id, current_user)


@router.post("/{task_id}/archive", response_model=ArchiveResponse)
async def create_archive_endpoint(
    task_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> ArchiveResponse:
    """Create (or return an existing) ZIP archive of all completed screenshots.

    Returns 400 if the task is still running or has no completed screenshots.
    For completed tasks, returns the cached archive if it already exists.
    """
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    await get_project(session, task.project_id, current_user)

    if task.status != "completed":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Task must be completed to create archive",
        )

    archives_dir = Path(settings.DATA_DIR) / "archives"
    archives_dir.mkdir(parents=True, exist_ok=True)
    archive_path = archives_dir / f"{task_id}.zip"

    # Reuse cached archive for completed tasks — avoid re-zipping on every click
    if archive_path.exists() and task.status == "completed":
        return ArchiveResponse(
            archive_url=generate_archive_signed_url(str(task_id)),
            file_size=archive_path.stat().st_size,
        )

    result = await session.exec(
        select(Screenshot).where(
            Screenshot.task_id == task_id,
            Screenshot.status == "ok",
        )
    )
    screenshots = list(result.all())

    if not screenshots:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="No completed screenshots to archive",
        )

    # Run ZIP creation in a thread pool — never blocks the event loop
    archive_path = await asyncio.get_running_loop().run_in_executor(
        None, _create_archive_sync, str(task_id), screenshots, archives_dir
    )

    return ArchiveResponse(
        archive_url=generate_archive_signed_url(str(task_id)),
        file_size=archive_path.stat().st_size,
    )
