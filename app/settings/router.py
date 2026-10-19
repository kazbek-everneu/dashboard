from fastapi import APIRouter, Depends
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import User
from app.core.database import get_session
from app.settings.models import ApplicationSettingsRead, ApplicationSettingsUpdate
from app.settings.service import (
    get_effective_settings,
    update_application_settings,
)

router = APIRouter()


@router.get("/", response_model=ApplicationSettingsRead)
async def get_application_settings_endpoint(
    session: AsyncSession = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> object:
    return await get_effective_settings(session)


@router.patch("/", response_model=ApplicationSettingsRead)
async def update_application_settings_endpoint(
    data: ApplicationSettingsUpdate,
    session: AsyncSession = Depends(get_session),
    _current_user: User = Depends(get_current_user),
) -> object:
    await update_application_settings(session, data)
    return await get_effective_settings(session)
