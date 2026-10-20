import uuid

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.core.database import get_session
from app.projects.models import (
    ProjectConfigRead,
    ProjectConfigUpdate,
    ProjectCreate,
    ProjectRead,
    ProjectSummary,
    ProjectUpdate,
    SitemapURLBulkUpdate,
    SitemapURLCreate,
    SitemapURLRead,
    SitemapURLUpdate,
)
from app.projects.service import (
    bulk_update_sitemap_urls,
    create_project,
    create_sitemap_url,
    delete_project,
    fetch_and_store_sitemap,
    get_project,
    get_project_config,
    list_project_summaries,
    list_projects,
    list_sitemap_urls_flat,
    toggle_sitemap_url,
    update_project,
    update_project_config,
)

router = APIRouter()


@router.get("/", response_model=list[ProjectRead])
async def list_projects_endpoint(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list:
    return await list_projects(session, current_user, skip=skip, limit=limit)


@router.post("/", response_model=ProjectRead, status_code=201)
async def create_project_endpoint(
    data: ProjectCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await create_project(session, data, current_user)


@router.get("/summary", response_model=list[ProjectSummary])
async def list_project_summaries_endpoint(
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list[ProjectSummary]:
    return await list_project_summaries(session, current_user)


@router.get("/{project_id}", response_model=ProjectRead)
async def get_project_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await get_project(session, project_id, current_user)


@router.patch("/{project_id}", response_model=ProjectRead)
async def update_project_endpoint(
    project_id: uuid.UUID,
    data: ProjectUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await update_project(session, project_id, data, current_user)


@router.delete("/{project_id}", status_code=204)
async def delete_project_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> None:
    await delete_project(session, project_id, current_user)


@router.get("/{project_id}/config", response_model=ProjectConfigRead)
async def get_config_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await get_project_config(session, project_id, current_user)


@router.patch("/{project_id}/config", response_model=ProjectConfigRead)
async def update_config_endpoint(
    project_id: uuid.UUID,
    data: ProjectConfigUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await update_project_config(session, project_id, data, current_user)


# ---------------------------------------------------------------------------
# Sitemap endpoints
# ---------------------------------------------------------------------------

@router.post("/{project_id}/sitemap/fetch")
async def fetch_sitemap_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await fetch_and_store_sitemap(session, project_id, current_user)


@router.get("/{project_id}/sitemap/urls", response_model=list[SitemapURLRead])
async def list_sitemap_urls_endpoint(
    project_id: uuid.UUID,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> list:
    return await list_sitemap_urls_flat(session, project_id, current_user)


@router.patch("/{project_id}/sitemap/urls/bulk")
async def bulk_update_sitemap_urls_endpoint(
    project_id: uuid.UUID,
    data: SitemapURLBulkUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> dict:
    return await bulk_update_sitemap_urls(session, project_id, data, current_user)


@router.patch(
    "/{project_id}/sitemap/urls/{url_id}",
    response_model=SitemapURLRead,
)
async def toggle_sitemap_url_endpoint(
    project_id: uuid.UUID,
    url_id: uuid.UUID,
    data: SitemapURLUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await toggle_sitemap_url(session, project_id, url_id, data, current_user)


@router.post(
    "/{project_id}/sitemap/urls",
    response_model=SitemapURLRead,
    status_code=201,
)
async def create_sitemap_url_endpoint(
    project_id: uuid.UUID,
    data: SitemapURLCreate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> object:
    return await create_sitemap_url(session, project_id, data, current_user)
