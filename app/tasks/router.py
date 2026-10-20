import asyncio
import uuid
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.core.database import get_session
from app.core.exceptions import NotFoundError
from app.projects.service import get_project
from app.screenshots.models import Screenshot
from app.screenshots.storage import (
    abs_file_path,
    archive_path,
    archives_dir,
    generate_archive_signed_url,
)
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
    """Fallback ZIP folder for a row whose stored file_path has no URL segment."""
    path = urlparse(url).path.strip("/")
    return path.replace("/", "_") or "homepage"


def _archive_name(task_id: str, screenshot: Screenshot) -> str:
    """The screenshot's path inside the task ZIP: ``{task_id}/{url_dir}/{file}``.

    Folder and file name are the stored ``file_path`` segments
    (``{project}/{task}/{url_dir}/{w}x{h}_{browser}.{ext}``), so the archive
    mirrors the disk layout and is as collision-free as the storage paths.
    """
    parts = PurePosixPath(screenshot.file_path).parts
    if len(parts) == 4 and not {".", ".."} & set(parts[2:]):
        return f"{task_id}/{parts[2]}/{parts[3]}"
    ext = "jpg" if screenshot.file_format == "jpeg" else screenshot.file_format
    filename = f"{screenshot.width}x{screenshot.height}_{screenshot.browser}.{ext}"
    return f"{task_id}/{_url_to_folder(screenshot.url)}/{filename}"


def _create_archive_sync(
    task_id: str,
    screenshots: list[Screenshot],
    archives_dir: Path,
) -> Path:
    """Build a ZIP archive from *screenshots* and write it to *archives_dir*.

    Runs synchronously — call via run_in_executor to avoid blocking the event loop.
    Missing files are silently skipped, and so is a second row pointing at a
    file already in the archive.
    """
    archive_path = archives_dir / f"{task_id}.zip"
    written: set[str] = set()
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for s in screenshots:
            abs_path = abs_file_path(s.file_path)
            if not abs_path.exists():
                continue
            arcname = _archive_name(task_id, s)
            if arcname in written:
                continue
            written.add(arcname)
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

    target_dir = archives_dir()
    target_dir.mkdir(parents=True, exist_ok=True)
    cached = archive_path(task_id)

    # Reuse cached archive for completed tasks — avoid re-zipping on every click
    if cached.exists() and task.status == "completed":
        return ArchiveResponse(
            archive_url=generate_archive_signed_url(str(task_id)),
            file_size=cached.stat().st_size,
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
    created = await asyncio.get_running_loop().run_in_executor(
        None, _create_archive_sync, str(task_id), screenshots, target_dir
    )

    return ArchiveResponse(
        archive_url=generate_archive_signed_url(str(task_id)),
        file_size=created.stat().st_size,
    )
