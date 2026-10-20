import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.security import HTTPAuthorizationCredentials

from app.auth.dependencies import get_current_user
from app.auth.models import (
    PasswordResetRequest,
    PasswordResetToken,
    User,
)
from app.auth.router import password_reset_request as password_reset_request_endpoint
from app.auth.service import (
    _password_reset_token_hash,
    _utcnow,
    check_password_reset_rate_limit,
    confirm_password_reset,
    create_access_token,
    hash_password,
    request_password_reset,
    verify_password,
)
from app.core.exceptions import BadRequestError, UnauthorizedError
from app.settings.models import ApplicationSettings


class _FakeResult:
    def __init__(self, values: list[object]) -> None:
        self._values = values

    def first(self) -> object | None:
        return self._values[0] if self._values else None

    def all(self) -> list[object]:
        return self._values


class _FakePasswordResetSession:
    def __init__(
        self,
        users: list[User] | None = None,
        reset_tokens: list[PasswordResetToken] | None = None,
    ) -> None:
        self.users = users or []
        self.reset_tokens = reset_tokens or []
        self.commit_count = 0

    async def exec(self, statement: object) -> _FakeResult:
        compiled = statement.compile()  # type: ignore[attr-defined]
        sql = str(compiled)
        params = compiled.params

        if "FROM users" in sql:
            email = str(next(iter(params.values()))).lower()
            return _FakeResult(
                [user for user in self.users if user.email.lower() == email]
            )

        if (
            "FROM password_reset_tokens" in sql
            and "WHERE password_reset_tokens.token_hash" in sql
        ):
            token_hash = str(next(iter(params.values())))
            return _FakeResult(
                [
                    reset_token
                    for reset_token in self.reset_tokens
                    if reset_token.token_hash == token_hash
                ]
            )

        if (
            "FROM password_reset_tokens" in sql
            and "WHERE password_reset_tokens.user_id" in sql
        ):
            user_id = next(iter(params.values()))
            tokens = [
                reset_token
                for reset_token in self.reset_tokens
                if reset_token.user_id == user_id
            ]
            if "used_at IS NULL" in sql:
                tokens = [
                    reset_token for reset_token in tokens if reset_token.used_at is None
                ]
            return _FakeResult(tokens)

        return _FakeResult([])

    async def get(self, model: type, key: object) -> object | None:
        if model is ApplicationSettings:
            # No overrides persisted in tests → effective values fall back to .env.
            return ApplicationSettings(id=key)
        assert model is User
        return next((user for user in self.users if user.id == key), None)

    def add(self, item: object) -> None:
        if isinstance(item, PasswordResetToken) and not any(
            existing.id == item.id for existing in self.reset_tokens
        ):
            self.reset_tokens.append(item)

    async def commit(self) -> None:
        self.commit_count += 1

    async def refresh(self, _item: object) -> None:
        return None


class _ExpiringUser:
    def __init__(self, email: str) -> None:
        self._id = uuid.uuid4()
        self._email = email
        self._is_active = True
        self._expired = False

    def expire(self) -> None:
        self._expired = True

    def _assert_not_expired(self, field: str) -> None:
        if self._expired:
            raise AssertionError(f"{field} was read after commit")

    @property
    def id(self) -> uuid.UUID:
        self._assert_not_expired("id")
        return self._id

    @property
    def email(self) -> str:
        self._assert_not_expired("email")
        return self._email

    @property
    def is_active(self) -> bool:
        self._assert_not_expired("is_active")
        return self._is_active


class _ExpiringPasswordResetSession(_FakePasswordResetSession):
    async def commit(self) -> None:
        await super().commit()
        for user in self.users:
            expire = getattr(user, "expire", None)
            if expire:
                expire()


class _FakeRedis:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}
        self.expirations: dict[str, int] = {}

    async def incr(self, key: str) -> int:
        self.counts[key] = self.counts.get(key, 0) + 1
        return self.counts[key]

    async def expire(self, key: str, ttl: int) -> None:
        self.expirations[key] = ttl


class _FakeRequest:
    class _Client:
        host = "203.0.113.9"

    client = _Client()


def _user(email: str = "user@example.com") -> User:
    return User(
        name="User",
        email=email,
        hashed_password=hash_password("old-password"),
        is_admin=False,
        password_changed_at=_utcnow() - timedelta(minutes=5),
    )


def _reset_token(
    user: User,
    raw_token: str,
    *,
    expires_at: datetime | None = None,
    used_at: datetime | None = None,
) -> PasswordResetToken:
    return PasswordResetToken(
        user_id=user.id,
        token_hash=_password_reset_token_hash(raw_token),
        expires_at=expires_at or (_utcnow() + timedelta(minutes=30)),
        used_at=used_at,
    )


@pytest.mark.asyncio
async def test_password_reset_request_endpoint_returns_generic_response() -> None:
    user = _user()
    session = _FakePasswordResetSession([user])

    with (
        patch(
            "app.auth.service.check_password_reset_rate_limit",
            AsyncMock(return_value=False),
        ),
        patch("app.auth.service.send_password_reset_email", AsyncMock()),
    ):
        existing = await password_reset_request_endpoint(
            PasswordResetRequest(email=user.email),
            _FakeRequest(),  # type: ignore[arg-type]
            session,  # type: ignore[arg-type]
        )
        unknown = await password_reset_request_endpoint(
            PasswordResetRequest(email="unknown@example.com"),
            _FakeRequest(),  # type: ignore[arg-type]
            session,  # type: ignore[arg-type]
        )

    assert existing.message == unknown.message


@pytest.mark.asyncio
async def test_password_reset_request_hashes_and_invalidates_previous() -> None:
    user = _user()
    session = _FakePasswordResetSession([user])
    send_email = AsyncMock()

    with (
        patch(
            "app.auth.service.check_password_reset_rate_limit",
            AsyncMock(return_value=False),
        ),
        patch("app.auth.service.send_password_reset_email", send_email),
    ):
        await request_password_reset(session, user.email, "203.0.113.9")  # type: ignore[arg-type]
        await request_password_reset(session, user.email, "203.0.113.9")  # type: ignore[arg-type]

    first_raw_token = send_email.await_args_list[0].args[1]
    second_raw_token = send_email.await_args_list[1].args[1]

    assert len(session.reset_tokens) == 2
    assert session.reset_tokens[0].used_at is not None
    assert session.reset_tokens[1].used_at is None
    assert session.reset_tokens[0].token_hash != first_raw_token
    assert session.reset_tokens[1].token_hash != second_raw_token
    assert len(session.reset_tokens[1].token_hash) == 64


@pytest.mark.asyncio
async def test_password_reset_request_does_not_read_user_after_commit() -> None:
    user = _ExpiringUser("user@example.com")
    session = _ExpiringPasswordResetSession([user])  # type: ignore[list-item]
    send_email = AsyncMock(return_value=True)

    with (
        patch(
            "app.auth.service.check_password_reset_rate_limit",
            AsyncMock(return_value=False),
        ),
        patch("app.auth.service.send_password_reset_email", send_email),
    ):
        await request_password_reset(
            session,  # type: ignore[arg-type]
            "user@example.com",
            "203.0.113.9",
        )

    send_email.assert_awaited_once()
    assert send_email.await_args.args[0] == "user@example.com"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("used_at", "expires_at"),
    [
        (None, _utcnow() - timedelta(seconds=1)),
        (_utcnow(), _utcnow() + timedelta(minutes=30)),
    ],
)
async def test_confirm_password_reset_rejects_expired_or_used_tokens(
    used_at: datetime | None,
    expires_at: datetime,
) -> None:
    user = _user()
    session = _FakePasswordResetSession(
        [user],
        [_reset_token(user, "raw-token", expires_at=expires_at, used_at=used_at)],
    )

    with pytest.raises(BadRequestError) as exc_info:
        await confirm_password_reset(session, "raw-token", "new-password")  # type: ignore[arg-type]

    assert exc_info.value.status_code == 400


@pytest.mark.asyncio
async def test_confirm_password_reset_updates_password_and_change_time() -> None:
    user = _user()
    previous_change_time = user.password_changed_at
    reset_token = _reset_token(user, "raw-token")
    session = _FakePasswordResetSession([user], [reset_token])

    await confirm_password_reset(session, "raw-token", "new-password")  # type: ignore[arg-type]

    assert verify_password("new-password", user.hashed_password)
    assert user.password_changed_at > previous_change_time
    assert reset_token.used_at is not None


@pytest.mark.asyncio
async def test_get_current_user_rejects_token_issued_before_password_change() -> None:
    user = _user()
    token = create_access_token(user.id)
    user.password_changed_at = datetime.now(UTC) + timedelta(
        seconds=10
    )
    session = _FakePasswordResetSession([user])
    credentials = HTTPAuthorizationCredentials(scheme="Bearer", credentials=token)

    with (
        patch("app.auth.dependencies.is_token_revoked", AsyncMock(return_value=False)),
        pytest.raises(UnauthorizedError) as exc_info,
    ):
        await get_current_user(
            credentials=credentials,
            access_token=None,
            session=session,  # type: ignore[arg-type]
        )

    assert exc_info.value.status_code == 401


@pytest.mark.asyncio
async def test_password_reset_rate_limit_blocks_after_limit() -> None:
    redis = _FakeRedis()

    with patch("app.core.redis.get_redis_pool", AsyncMock(return_value=redis)):
        results = [
            await check_password_reset_rate_limit("user@example.com", "203.0.113.9")
            for _ in range(6)
        ]

    assert results == [False, False, False, False, False, True]
    assert redis.expirations
