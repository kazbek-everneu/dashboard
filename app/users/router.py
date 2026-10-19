import uuid

from fastapi import APIRouter, Depends, Query
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_admin_user, get_current_user
from app.auth.models import (
    User,
    UserAdminCreate,
    UserListResponse,
    UserRead,
    UserUpdate,
)
from app.core.database import get_session
from app.users.service import create_user, delete_user, list_users, update_user

router = APIRouter()


@router.get("/", response_model=UserListResponse)
async def list_users_endpoint(
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=20, ge=1, le=200),
    session: AsyncSession = Depends(get_session),
    _current_user: User = Depends(get_current_admin_user),
) -> UserListResponse:
    return await list_users(session, skip=skip, limit=limit)


@router.post("/", response_model=UserRead, status_code=201)
async def create_user_endpoint(
    data: UserAdminCreate,
    session: AsyncSession = Depends(get_session),
    _current_user: User = Depends(get_current_admin_user),
) -> UserRead:
    return await create_user(session, data)


@router.patch("/{user_id}", response_model=UserRead)
async def update_user_endpoint(
    user_id: uuid.UUID,
    data: UserUpdate,
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_user),
) -> UserRead:
    return await update_user(session, user_id, data, current_user=current_user)


@router.delete("/{user_id}", status_code=204)
async def delete_user_endpoint(
    user_id: uuid.UUID,
    reassign_to_user_id: uuid.UUID = Query(...),
    session: AsyncSession = Depends(get_session),
    current_user: User = Depends(get_current_admin_user),
) -> None:
    await delete_user(
        session,
        user_id,
        reassign_to_user_id=reassign_to_user_id,
        current_user=current_user,
    )
