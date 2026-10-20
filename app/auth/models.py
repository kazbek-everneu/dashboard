import uuid
from datetime import datetime

from pydantic import field_validator
from sqlmodel import Field, SQLModel

from app.core.time import UtcDateTime, known_timezones, utcnow


class User(SQLModel, table=True):
    __tablename__ = "users"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    name: str = Field(max_length=255)
    email: str = Field(unique=True, index=True, max_length=255)
    hashed_password: str
    is_active: bool = Field(default=True)
    is_admin: bool = Field(default=False)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)
    password_changed_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)
    # IANA time zone the UI shows times in; None = the browser's own zone.
    timezone: str | None = Field(default=None, max_length=64)


class PasswordResetToken(SQLModel, table=True):
    __tablename__ = "password_reset_tokens"

    id: uuid.UUID = Field(default_factory=uuid.uuid4, primary_key=True)
    user_id: uuid.UUID = Field(foreign_key="users.id", index=True)
    token_hash: str = Field(unique=True, index=True, max_length=64)
    expires_at: datetime = Field(sa_type=UtcDateTime)
    used_at: datetime | None = Field(default=None, sa_type=UtcDateTime)
    created_at: datetime = Field(default_factory=utcnow, sa_type=UtcDateTime)


class UserCreate(SQLModel):
    name: str
    email: str
    password: str


class UserAdminCreate(UserCreate):
    is_admin: bool = False


class UserUpdate(SQLModel):
    name: str | None = None
    password: str | None = None
    is_admin: bool | None = None
    # Sending null resets to "follow the browser"; omitting it keeps the value.
    timezone: str | None = None

    @field_validator("timezone")
    @classmethod
    def validate_timezone(cls, v: str | None) -> str | None:
        if v is not None and v not in known_timezones():
            raise ValueError(f"Unknown time zone: {v!r}")
        return v


class UserRead(SQLModel):
    id: uuid.UUID
    name: str
    email: str
    is_active: bool
    is_admin: bool
    created_at: datetime
    timezone: str | None = None


class UserListResponse(SQLModel):
    items: list[UserRead]
    total: int


class TokenResponse(SQLModel):
    access_token: str | None = None
    token_type: str = "bearer"


class PasswordResetRequest(SQLModel):
    email: str


class PasswordResetConfirm(SQLModel):
    token: str
    password: str = Field(min_length=8)


class PasswordResetResponse(SQLModel):
    message: str = (
        "If an account with that email exists, password reset instructions "
        "have been sent."
    )
