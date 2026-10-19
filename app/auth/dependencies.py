import uuid

from fastapi import Cookie, Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.models import User
from app.auth.service import (
    decode_access_token_payload,
    get_user_by_id,
    is_access_token_stale_for_user,
    is_token_revoked,
)
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import ForbiddenError, UnauthorizedError

_bearer = HTTPBearer(auto_error=False)


async def get_current_user(
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    access_token: str | None = Cookie(
        default=None,
        alias=settings.ACCESS_TOKEN_COOKIE_NAME,
    ),
    session: AsyncSession = Depends(get_session),
) -> User:
    token = credentials.credentials if credentials else access_token
    if not token:
        raise UnauthorizedError()

    payload = decode_access_token_payload(token)

    jti = payload.get("jti")
    if jti and await is_token_revoked(jti):
        raise UnauthorizedError("Token has been revoked")

    user = await get_user_by_id(session, uuid.UUID(payload["sub"]))
    if is_access_token_stale_for_user(payload, user):
        raise UnauthorizedError("Password has changed")

    return user


async def get_current_admin_user(
    current_user: User = Depends(get_current_user),
) -> User:
    if not current_user.is_admin:
        raise ForbiddenError("Admin access required")
    return current_user
