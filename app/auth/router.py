from datetime import UTC, datetime

from fastapi import APIRouter, Cookie, Depends, Request, Response, status
from fastapi.security import (
    HTTPAuthorizationCredentials,
    HTTPBearer,
    OAuth2PasswordRequestForm,
)
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.dependencies import get_current_user
from app.auth.models import (
    PasswordResetConfirm,
    PasswordResetRequest,
    PasswordResetResponse,
    TokenResponse,
    User,
    UserCreate,
    UserRead,
)
from app.auth.service import (
    authenticate_user,
    confirm_password_reset,
    create_access_token,
    decode_access_token_payload,
    register_user,
    request_password_reset,
    revoke_token,
)
from app.core.config import settings
from app.core.database import get_session
from app.core.exceptions import UnauthorizedError
from app.settings.service import get_effective_settings

router = APIRouter()

_bearer = HTTPBearer(auto_error=False)


@router.post("/register", response_model=UserRead, status_code=201)
async def register(
    data: UserCreate,
    session: AsyncSession = Depends(get_session),
) -> User:
    return await register_user(session, data)


@router.post("/login", response_model=TokenResponse)
async def login(
    response: Response,
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
) -> TokenResponse:
    user = await authenticate_user(session, form.username, form.password)
    effective = await get_effective_settings(session)
    expire_minutes = effective["access_token_expire_minutes"]
    token = create_access_token(user.id, expire_minutes)
    response.set_cookie(
        key=settings.ACCESS_TOKEN_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.ACCESS_TOKEN_COOKIE_SECURE,
        samesite=settings.ACCESS_TOKEN_COOKIE_SAMESITE,
        max_age=expire_minutes * 60,
        path="/",
    )
    return TokenResponse(access_token=token)


@router.post(
    "/password-reset/request",
    response_model=PasswordResetResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def password_reset_request(
    data: PasswordResetRequest,
    request: Request,
    session: AsyncSession = Depends(get_session),
) -> PasswordResetResponse:
    client_host = request.client.host if request.client else "unknown"
    await request_password_reset(session, data.email, client_host)
    return PasswordResetResponse()


@router.post("/password-reset/confirm", response_model=PasswordResetResponse)
async def password_reset_confirm(
    data: PasswordResetConfirm,
    session: AsyncSession = Depends(get_session),
) -> PasswordResetResponse:
    await confirm_password_reset(session, data.token, data.password)
    return PasswordResetResponse(message="Password has been reset.")


@router.get("/me", response_model=UserRead)
async def me(current_user: User = Depends(get_current_user)) -> User:
    return current_user


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
    access_token: str | None = Cookie(
        default=None,
        alias=settings.ACCESS_TOKEN_COOKIE_NAME,
    ),
) -> None:
    token = credentials.credentials if credentials else access_token
    if token:
        try:
            payload = decode_access_token_payload(token)
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                expire_dt = datetime.fromtimestamp(exp, tz=UTC)
                await revoke_token(jti, expire_dt)
        except UnauthorizedError:
            pass  # token already invalid/expired — nothing to revoke
    response.delete_cookie(
        key=settings.ACCESS_TOKEN_COOKIE_NAME,
        path="/",
        samesite=settings.ACCESS_TOKEN_COOKIE_SAMESITE,
    )
