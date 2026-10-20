import re
import uuid
from pathlib import Path

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.core.database import get_session
from app.core.exceptions import ForbiddenError, NotFoundError
from app.projects.service import get_project
from app.screenshots.image_dimensions import read_image_dimensions_from_file
from app.screenshots.models import Screenshot, ScreenshotRead
from app.screenshots.service import request_screenshot_recapture
from app.screenshots.storage import (
    abs_file_path,
    archives_dir,
    generate_signed_url,
    verify_archive_token,
    verify_signed_url,
)
from app.tasks.models import Task

_ARCHIVE_FILENAME_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}\.zip$"
)

router = APIRouter()


@router.get("/screenshots/{task_id}", response_model=list[ScreenshotRead])
async def list_screenshots(
    task_id: uuid.UUID,
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=100, ge=1, le=1000),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list:
    task = await session.get(Task, task_id)
    if not task:
        raise NotFoundError("Task not found")
    # Verify the task's project exists (projects are shared across all users)
    await get_project(session, task.project_id, current_user)

    result = await session.exec(
        select(Screenshot)
        .where(Screenshot.task_id == task_id)
        .order_by(Screenshot.created_at)
        .offset(skip)
        .limit(limit)
    )
    screenshots = list(result.all())
    updated_dimensions = False
    for screenshot in screenshots:
        filename = "/".join(screenshot.file_path.split("/")[2:])
        screenshot.signed_url = generate_signed_url(str(task_id), filename)
        if screenshot.image_width is not None and screenshot.image_height is not None:
            continue
        file_path = abs_file_path(screenshot.file_path)
        if not file_path.is_file():
            continue
        image_dimensions = read_image_dimensions_from_file(file_path)
        if not image_dimensions:
            continue
        screenshot.image_width, screenshot.image_height = image_dimensions
        session.add(screenshot)
        updated_dimensions = True
    if updated_dimensions:
        await session.commit()
    return screenshots


@router.post("/screenshots/{screenshot_id}/recapture", response_model=ScreenshotRead)
async def recapture_screenshot_endpoint(
    screenshot_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> Screenshot:
    """Re-capture a single screenshot in place within its (terminal) task."""
    return await request_screenshot_recapture(session, screenshot_id, current_user)


@router.get("/files/{task_id}/{filename:path}")
async def serve_file(
    task_id: uuid.UUID,
    filename: str,
    expires: int | None = Query(default=None),
    token: str = Query(...),
    session: AsyncSession = Depends(get_session),
) -> FileResponse:
    """Serve a screenshot file after validating its HMAC token.

    No auth dependency — the HMAC token is the access control mechanism.
    Returns 403 if the token is invalid, 404 if the record or file is missing.

    ``filename`` is the path portion after ``/files/{task_id}/``,
    i.e. ``{url_dir}/{width}x{height}_{browser}.{fmt}``.
    """
    if not verify_signed_url(str(task_id), filename, expires=expires, token=token):
        raise ForbiddenError("Invalid or missing token")

    # Reconstruct the stored relative_path to look up the Screenshot record.
    # file_path = "{project_id}/{task_id}/{filename}", so we query by task_id
    # and the suffix that matches filename.  This prevents path-traversal because:
    #   1. The HMAC was already validated above.
    #   2. We only serve paths that exist in the DB, keyed by task_id.
    result = await session.exec(
        select(Screenshot).where(
            Screenshot.task_id == task_id,
            Screenshot.file_path.endswith(filename),  # type: ignore[attr-defined]
        )
    )
    record = result.first()
    if not record:
        raise NotFoundError("Screenshot record not found")

    file_path: Path = abs_file_path(record.file_path)
    if not file_path.is_file():
        raise NotFoundError("Screenshot file not found on disk")

    ext = Path(filename).suffix.lower()
    media_type = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }.get(ext, "image/jpeg")

    return FileResponse(
        path=str(file_path),
        media_type=media_type,
        headers={"Content-Disposition": f'inline; filename="{Path(filename).name}"'},
    )


@router.get("/archives/{filename}")
async def serve_archive(
    filename: str,
    expires: int | None = Query(default=None),
    token: str = Query(...),
) -> FileResponse:
    """Serve a task ZIP archive after validating its HMAC token.

    ``filename`` must match ``{uuid}.zip`` exactly — any other pattern is
    rejected before the token check to prevent path traversal.
    """
    if not _ARCHIVE_FILENAME_RE.match(filename):
        raise ForbiddenError("Invalid archive filename")

    if not verify_archive_token(filename, expires=expires, token=token):
        raise ForbiddenError("Invalid or missing token")

    archive_file = archives_dir() / filename
    if not archive_file.is_file():
        raise NotFoundError("Archive not found")

    return FileResponse(
        path=str(archive_file),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
