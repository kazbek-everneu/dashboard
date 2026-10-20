import logging
import uuid
from collections import defaultdict
from datetime import datetime
from urllib.parse import urlparse

from sqlmodel import delete, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import User
from app.core.exceptions import ConflictError, NotFoundError, UnprocessableError
from app.core.url_security import normalize_project_child_url, normalize_project_url
from app.projects.models import (
    Project,
    ProjectConfig,
    ProjectConfigUpdate,
    ProjectCreate,
    ProjectSummary,
    ProjectUpdate,
    SitemapURL,
    SitemapURLBulkUpdate,
    SitemapURLCreate,
    SitemapURLUpdate,
)

logger = logging.getLogger(__name__)


async def _validate_url(url: str) -> None:
    await normalize_project_url(url)


def _project_name_from_url(url: str) -> str:
    hostname = urlparse(url).hostname or ""
    return hostname.removeprefix("www.")


def _default_enabled_sitemap_urls(parsed: list[object]) -> set[str]:
    latest_by_cpt: dict[str, object] = {}
    for entry in parsed:
        if getattr(entry, "cpt", None) not in {"post", "product"}:
            continue
        current = latest_by_cpt.get(entry.cpt)
        if current is None:
            latest_by_cpt[entry.cpt] = entry
            continue

        entry_lastmod = getattr(entry, "lastmod", None)
        current_lastmod = getattr(current, "lastmod", None)
        if entry_lastmod is not None and current_lastmod is not None:
            if entry_lastmod >= current_lastmod:
                latest_by_cpt[entry.cpt] = entry
        elif entry_lastmod is not None:
            latest_by_cpt[entry.cpt] = entry
        elif current_lastmod is None and getattr(entry, "order", -1) >= getattr(
            current, "order", -1
        ):
            latest_by_cpt[entry.cpt] = entry

    enabled_urls = {
        entry.url
        for entry in parsed
        if getattr(entry, "cpt", None) not in {"post", "product"}
    }
    enabled_urls.update(entry.url for entry in latest_by_cpt.values())
    return enabled_urls


# ---------------------------------------------------------------------------
# Project CRUD
# ---------------------------------------------------------------------------


async def create_project(
    session: AsyncSession, data: ProjectCreate, user: User
) -> Project:
    normalized_url = await normalize_project_url(data.url)
    project_name = _project_name_from_url(normalized_url)
    owner_id = user.id  # capture before commit expires the session-bound user object
    project = Project(owner_id=owner_id, name=project_name, url=normalized_url)
    session.add(project)
    await session.flush()  # get project.id before creating config

    config = ProjectConfig(project_id=project.id)
    session.add(config)

    await session.commit()
    await session.refresh(project)
    logger.info("Created project id=%s owner=%s", str(project.id), str(owner_id))
    return project


async def list_projects(
    session: AsyncSession, user: User, skip: int = 0, limit: int = 50
) -> list[Project]:
    result = await session.exec(
        select(Project).order_by(Project.created_at).offset(skip).limit(limit)
    )
    return list(result.all())


async def list_project_summaries(
    session: AsyncSession,
    user: User,
) -> list[ProjectSummary]:
    from app.tasks.models import Task

    projects = await list_projects(session, user, skip=0, limit=500)
    project_ids = [project.id for project in projects]
    if not project_ids:
        return []

    task_rows = await session.exec(
        select(Task)
        .where(Task.project_id.in_(project_ids))  # type: ignore[attr-defined]
        .order_by(Task.created_at.desc())  # type: ignore[attr-defined]
    )
    by_project: dict[uuid.UUID, ProjectSummary] = {
        project.id: ProjectSummary(
            project_id=project.id,
            last_task_status=None,
            screenshots_count=0,
        )
        for project in projects
    }
    for task in task_rows.all():
        summary = by_project[task.project_id]
        if summary.last_task_status is None:
            summary.last_task_status = task.status
        summary.screenshots_count += task.progress_done
    return list(by_project.values())


async def get_project(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise NotFoundError("Project not found")
    return project


async def update_project(
    session: AsyncSession,
    project_id: uuid.UUID,
    data: ProjectUpdate,
    user: User,
) -> Project:
    project = await get_project(session, project_id, user)
    if data.url is not None:
        project.url = await normalize_project_url(data.url)
    if data.name is not None:
        project.name = data.name
    session.add(project)
    await session.commit()
    await session.refresh(project)
    return project


async def delete_project(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> None:
    project = await get_project(session, project_id, user)
    await session.delete(project)
    await session.commit()
    logger.info("Deleted project id=%s", project_id)


# ---------------------------------------------------------------------------
# ProjectConfig
# ---------------------------------------------------------------------------


async def _get_config_for_project(
    session: AsyncSession, project_id: uuid.UUID
) -> ProjectConfig:
    result = await session.exec(
        select(ProjectConfig).where(ProjectConfig.project_id == project_id)
    )
    config = result.first()
    if not config:
        raise NotFoundError("Project config not found")
    return config


async def get_project_config(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> ProjectConfig:
    await get_project(session, project_id, user)  # verify project exists
    return await _get_config_for_project(session, project_id)


async def update_project_config(
    session: AsyncSession,
    project_id: uuid.UUID,
    data: ProjectConfigUpdate,
    user: User,
) -> ProjectConfig:
    await get_project(session, project_id, user)  # verify project exists
    config = await _get_config_for_project(session, project_id)

    update_fields = data.model_dump(exclude_unset=True)
    for field, value in update_fields.items():
        setattr(config, field, value)

    session.add(config)
    await session.commit()
    await session.refresh(config)
    return config


# ---------------------------------------------------------------------------
# SitemapURL
# ---------------------------------------------------------------------------


async def fetch_and_store_sitemap(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> dict[str, int]:
    """Fetch the project's sitemap, replace all SitemapURL rows, return summary."""
    from app.projects.sitemap import fetch_sitemap_urls

    project = await get_project(session, project_id, user)

    try:
        parsed = await fetch_sitemap_urls(project.url)
    except RuntimeError as exc:
        logger.error("Sitemap fetch failed for project %s: %s", project_id, exc)
        raise UnprocessableError(str(exc)) from exc

    # Replace all existing rows for this project
    await session.exec(delete(SitemapURL).where(SitemapURL.project_id == project_id))

    now = datetime.utcnow()
    enabled_urls = _default_enabled_sitemap_urls(parsed)
    cpt_counts: dict[str, int] = defaultdict(int)
    for entry in parsed:
        session.add(
            SitemapURL(
                project_id=project_id,
                url=entry.url,
                cpt=entry.cpt,
                is_enabled=entry.url in enabled_urls,
                last_fetched_at=now,
            )
        )
        cpt_counts[entry.cpt] += 1

    await session.commit()
    logger.info(
        "Stored %d sitemap URLs for project %s: %s",
        len(parsed),
        project_id,
        dict(cpt_counts),
    )
    return {"total": len(parsed), "by_cpt": dict(cpt_counts)}


async def list_sitemap_urls_flat(
    session: AsyncSession, project_id: uuid.UUID, user: User
) -> list[SitemapURL]:
    """Return SitemapURL rows as flat list."""
    await get_project(session, project_id, user)  # ownership check

    result = await session.exec(
        select(SitemapURL)
        .where(SitemapURL.project_id == project_id)
        .order_by(SitemapURL.cpt, SitemapURL.url)
    )
    return list(result.all())


async def toggle_sitemap_url(
    session: AsyncSession,
    project_id: uuid.UUID,
    url_id: uuid.UUID,
    data: SitemapURLUpdate,
    user: User,
) -> SitemapURL:
    await get_project(session, project_id, user)  # ownership check

    sitemap_url = await session.get(SitemapURL, url_id)
    if not sitemap_url or sitemap_url.project_id != project_id:
        raise NotFoundError("Sitemap URL not found")

    sitemap_url.is_enabled = data.is_enabled
    session.add(sitemap_url)
    await session.commit()
    await session.refresh(sitemap_url)
    return sitemap_url


async def create_sitemap_url(
    session: AsyncSession,
    project_id: uuid.UUID,
    data: SitemapURLCreate,
    user: User,
) -> SitemapURL:
    project = await get_project(session, project_id, user)  # ownership check
    normalized_url = await normalize_project_child_url(project.url, data.url)

    # Check if URL already exists
    existing = await session.exec(
        select(SitemapURL).where(
            SitemapURL.project_id == project_id,
            SitemapURL.url == normalized_url,
        )
    )
    if existing.first():
        raise ConflictError("URL already exists in sitemap")

    sitemap_url = SitemapURL(
        project_id=project_id,
        url=normalized_url,
        cpt=data.cpt,
        is_enabled=True,
        last_fetched_at=datetime.utcnow(),
    )
    session.add(sitemap_url)
    await session.commit()
    await session.refresh(sitemap_url)
    return sitemap_url


async def bulk_update_sitemap_urls(
    session: AsyncSession,
    project_id: uuid.UUID,
    data: SitemapURLBulkUpdate,
    user: User,
) -> dict[str, int]:
    """Toggle is_enabled, delete, and create sitemap URLs in one transaction."""
    project = await get_project(session, project_id, user)  # ownership check

    # Toggle is_enabled for existing URLs
    for item in data.updates:
        url = await session.get(SitemapURL, item.url_id)
        if url and url.project_id == project_id:
            url.is_enabled = item.is_enabled
            session.add(url)

    # Delete requested URLs
    for url_id in data.delete_ids:
        url = await session.get(SitemapURL, url_id)
        if url and url.project_id == project_id:
            await session.delete(url)

    # Create new URLs — skip any that already exist
    new_count = 0
    for item in data.new_urls:
        normalized_url = await normalize_project_child_url(project.url, item.url)
        existing = await session.exec(
            select(SitemapURL).where(
                SitemapURL.project_id == project_id,
                SitemapURL.url == normalized_url,
            )
        )
        if not existing.first():
            session.add(
                SitemapURL(
                    project_id=project_id,
                    url=normalized_url,
                    cpt=item.cpt,
                    is_enabled=True,
                    last_fetched_at=datetime.utcnow(),
                )
            )
            new_count += 1

    await session.commit()
    logger.info(
        "Bulk updated sitemap for project=%s: %d toggles, %d created, %d deleted",
        project_id,
        len(data.updates),
        new_count,
        len(data.delete_ids),
    )
    return {
        "updated": len(data.updates),
        "created": new_count,
        "deleted": len(data.delete_ids),
    }
