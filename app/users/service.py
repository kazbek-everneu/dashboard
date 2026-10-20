import uuid

from sqlalchemy import update
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import (
    User,
    UserAdminCreate,
    UserListResponse,
    UserRead,
    UserUpdate,
)
from app.auth.service import create_user_account, hash_password
from app.core.exceptions import ConflictError, ForbiddenError, NotFoundError
from app.core.time import utcnow
from app.projects.models import Project
from app.tasks.models import Task


def user_to_read(user: User) -> UserRead:
    return UserRead(
        id=user.id,
        name=user.name,
        email=user.email,
        is_active=user.is_active,
        is_admin=user.is_admin,
        created_at=user.created_at,
    )


async def _count_admins(session: AsyncSession) -> int:
    return int(
        (
            await session.exec(
                select(func.count()).select_from(User).where(User.is_admin)
            )
        ).one()
    )


async def list_users(
    session: AsyncSession,
    skip: int = 0,
    limit: int = 50,
) -> UserListResponse:
    total = int((await session.exec(select(func.count()).select_from(User))).one())
    result = await session.exec(
        select(User)
        .order_by(User.created_at.desc())  # type: ignore[attr-defined]
        .offset(skip)
        .limit(limit)
    )
    items = [user_to_read(user) for user in result.all()]
    return UserListResponse(items=items, total=total)


async def create_user(session: AsyncSession, data: UserAdminCreate) -> UserRead:
    user = await create_user_account(session, data, is_admin=data.is_admin)
    return user_to_read(user)


async def update_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    data: UserUpdate,
    current_user: User,
) -> UserRead:
    user = await session.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")

    if current_user.id != user_id and not current_user.is_admin:
        raise ForbiddenError("Cannot modify another user's account")

    if data.is_admin is not None:
        if not current_user.is_admin:
            raise ForbiddenError("Admin access required")
        if (
            user.is_admin
            and not data.is_admin
            and await _count_admins(session) <= 1
        ):
            raise ConflictError("Cannot remove the last admin")
        user.is_admin = data.is_admin

    if data.name is not None:
        user.name = data.name.strip()
    if data.password:
        user.hashed_password = hash_password(data.password)
        user.password_changed_at = utcnow()

    session.add(user)
    await session.commit()
    await session.refresh(user)

    return user_to_read(user)


async def delete_user(
    session: AsyncSession,
    user_id: uuid.UUID,
    reassign_to_user_id: uuid.UUID,
    current_user: User,
) -> None:
    if not current_user.is_admin:
        raise ForbiddenError("Admin access required")
    if current_user.id == user_id:
        raise ForbiddenError("Cannot delete your own account")

    user = await session.get(User, user_id)
    if not user:
        raise NotFoundError("User not found")
    if user_id == reassign_to_user_id:
        raise ConflictError("Cannot reassign data to the deleted user")
    if user.is_admin and await _count_admins(session) <= 1:
        raise ConflictError("Cannot delete the last admin")

    reassign_to_user = await session.get(User, reassign_to_user_id)
    if not reassign_to_user:
        raise NotFoundError("Reassignment target user not found")
    if not reassign_to_user.is_active:
        raise ConflictError("Reassignment target user must be active")

    await session.exec(
        update(Project)
        .where(Project.owner_id == user_id)
        .values(owner_id=reassign_to_user_id)
    )
    await session.exec(
        update(Task)
        .where(Task.created_by == user_id)
        .values(created_by=reassign_to_user_id)
    )
    await session.delete(user)
    await session.commit()
