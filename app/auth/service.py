import hashlib
import hmac
import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from jose import JWTError, jwt
from passlib.context import CryptContext
from sqlmodel import func, select
from sqlmodel.ext.asyncio.session import AsyncSession

from app.auth.email import send_password_reset_email
from app.auth.models import PasswordResetToken, User, UserCreate
from app.core.config import settings
from app.core.exceptions import BadRequestError, ConflictError, UnauthorizedError
from app.settings.service import get_effective_settings

logger = logging.getLogger(__name__)

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

ALGORITHM = "HS256"
PASSWORD_RESET_INVALID_DETAIL = "Password reset link is invalid or has expired"


def hash_password(password: str) -> str:
    return _pwd_context.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd_context.verify(plain, hashed)


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def create_access_token(user_id: uuid.UUID, expire_minutes: int | None = None) -> str:
    minutes = (
        expire_minutes
        if expire_minutes is not None
        else settings.ACCESS_TOKEN_EXPIRE_MINUTES
    )
    jti = str(uuid.uuid4())
    issued_at = datetime.now(UTC)
    expire = issued_at + timedelta(minutes=minutes)
    payload = {
        "sub": str(user_id),
        "exp": expire,
        "iat": issued_at.timestamp(),
        "jti": jti,
    }
    return jwt.encode(payload, settings.SECRET_KEY, algorithm=ALGORITHM)


def decode_access_token_payload(token: str) -> dict:
    """Decode and validate a JWT, returning the full payload dict.

    Raises UnauthorizedError on any validation failure.
    """
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[ALGORITHM])
        if payload.get("sub") is None:
            raise UnauthorizedError()
        return payload
    except (JWTError, ValueError) as exc:
        logger.debug("Token decode failed: %s", exc)
        raise UnauthorizedError() from exc


def decode_access_token(token: str) -> uuid.UUID:
    payload = decode_access_token_payload(token)
    try:
        return uuid.UUID(payload["sub"])
    except (KeyError, ValueError) as exc:
        raise UnauthorizedError() from exc


def is_access_token_stale_for_user(payload: dict, user: User) -> bool:
    changed_at = user.password_changed_at
    if not changed_at:
        return False

    try:
        issued_at = float(payload["iat"])
    except (KeyError, TypeError, ValueError):
        return True

    return issued_at < _as_utc(changed_at).timestamp()


async def revoke_token(jti: str, expire_dt: datetime) -> None:
    """Add the token's jti to the Redis blacklist until it naturally expires."""
    from app.core.redis import get_redis_pool

    ttl = max(1, int((expire_dt - datetime.now(UTC)).total_seconds()))
    redis = await get_redis_pool()
    await redis.setex(f"revoked_jti:{jti}", ttl, "1")


async def is_token_revoked(jti: str) -> bool:
    """Return True if the token has been explicitly revoked (e.g. via logout)."""
    from app.core.redis import get_redis_pool

    redis = await get_redis_pool()
    return bool(await redis.exists(f"revoked_jti:{jti}"))


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _password_reset_token_hash(token: str) -> str:
    return hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        token.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def _rate_limit_key(kind: str, value: str) -> str:
    digest = hmac.new(
        settings.SECRET_KEY.encode("utf-8"),
        value.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return f"password_reset_rate:{kind}:{digest}"


async def check_password_reset_rate_limit(
    email: str,
    client_host: str,
    *,
    max_attempts: int | None = None,
    window_seconds: int | None = None,
) -> bool:
    from app.core.redis import get_redis_pool

    if max_attempts is None:
        max_attempts = settings.PASSWORD_RESET_RATE_LIMIT_MAX
    if window_seconds is None:
        window_seconds = settings.PASSWORD_RESET_RATE_LIMIT_WINDOW_SECONDS

    keys = [
        _rate_limit_key("email", _normalize_email(email)),
        _rate_limit_key("ip", client_host or "unknown"),
    ]

    try:
        redis = await get_redis_pool()
        limited = False
        for key in keys:
            count = await redis.incr(key)
            if count == 1:
                await redis.expire(key, window_seconds)
            if count > max_attempts:
                limited = True
        return limited
    except Exception as exc:
        logger.warning("Password reset rate limit unavailable: %s", exc)
        return False


async def _mark_unused_password_reset_tokens_used(
    session: AsyncSession,
    user_id: uuid.UUID,
    used_at: datetime,
) -> None:
    result = await session.exec(
        select(PasswordResetToken).where(
            PasswordResetToken.user_id == user_id,
            PasswordResetToken.used_at.is_(None),  # type: ignore[union-attr]
        )
    )
    for reset_token in result.all():
        reset_token.used_at = used_at
        session.add(reset_token)


async def _create_password_reset_token(
    session: AsyncSession,
    user: User,
    *,
    token_expire_minutes: int | None = None,
) -> str:
    if token_expire_minutes is None:
        token_expire_minutes = settings.PASSWORD_RESET_TOKEN_EXPIRE_MINUTES
    now = _utcnow()
    await _mark_unused_password_reset_tokens_used(session, user.id, now)

    token = secrets.token_urlsafe(32)
    reset_token = PasswordResetToken(
        user_id=user.id,
        token_hash=_password_reset_token_hash(token),
        expires_at=now + timedelta(minutes=token_expire_minutes),
    )
    session.add(reset_token)
    await session.commit()
    return token


async def request_password_reset(
    session: AsyncSession,
    email: str,
    client_host: str,
) -> None:
    effective = await get_effective_settings(session)
    if await check_password_reset_rate_limit(
        email,
        client_host,
        max_attempts=effective["password_reset_rate_limit_max"],
        window_seconds=effective["password_reset_rate_limit_window_seconds"],
    ):
        logger.info("Password reset request rate limited")
        return

    normalized_email = _normalize_email(email)
    result = await session.exec(
        select(User).where(func.lower(User.email) == normalized_email)
    )
    user = result.first()
    if not user or not user.is_active:
        return

    user_id = user.id
    user_email = user.email
    token = await _create_password_reset_token(
        session,
        user,
        token_expire_minutes=effective["password_reset_token_expire_minutes"],
    )
    try:
        email_sent = await send_password_reset_email(user_email, token, effective)
    except Exception as exc:
        logger.error(
            "Password reset email send failed for user id=%s: %s",
            user_id,
            exc,
        )
        return

    if email_sent:
        logger.info("Password reset email sent for user id=%s", str(user_id))


async def confirm_password_reset(
    session: AsyncSession,
    token: str,
    password: str,
) -> None:
    token_hash = _password_reset_token_hash(token)
    result = await session.exec(
        select(PasswordResetToken).where(PasswordResetToken.token_hash == token_hash)
    )
    reset_token = result.first()
    now = _utcnow()

    if (
        not reset_token
        or reset_token.used_at is not None
        or reset_token.expires_at < now
    ):
        raise BadRequestError(PASSWORD_RESET_INVALID_DETAIL)

    user = await session.get(User, reset_token.user_id)
    if not user or not user.is_active:
        reset_token.used_at = now
        session.add(reset_token)
        await session.commit()
        raise BadRequestError(PASSWORD_RESET_INVALID_DETAIL)

    await _mark_unused_password_reset_tokens_used(session, user.id, now)
    user.hashed_password = hash_password(password)
    user.password_changed_at = now
    reset_token.used_at = now
    session.add(user)
    session.add(reset_token)
    await session.commit()


async def create_user_account(
    session: AsyncSession,
    data: UserCreate,
    *,
    is_admin: bool,
) -> User:
    """Create a user. Only admins (``POST /users``) and the CLI call this —
    there is no public self-registration."""
    existing = await session.exec(select(User).where(User.email == data.email))
    if existing.first():
        raise ConflictError("Email already registered")

    user = User(
        name=data.name.strip(),
        email=data.email,
        hashed_password=hash_password(data.password),
        is_admin=is_admin,
        password_changed_at=_utcnow(),
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)
    logger.info("Created user id=%s is_admin=%s", str(user.id), user.is_admin)
    return user


async def authenticate_user(
    session: AsyncSession, email: str, password: str
) -> User:
    result = await session.exec(select(User).where(User.email == email))
    user = result.first()
    if not user or not verify_password(password, user.hashed_password):
        raise UnauthorizedError("Invalid email or password")
    if not user.is_active:
        raise UnauthorizedError("Account is inactive")
    return user


async def get_user_by_id(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id)
    if not user:
        raise UnauthorizedError()
    if not user.is_active:
        raise UnauthorizedError("Account is inactive")
    return user
