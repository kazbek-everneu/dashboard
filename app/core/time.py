"""UTC time handling.

Every timestamp is stored as ``timestamptz`` and handled in Python as an aware
UTC ``datetime``; the API serialises it with a ``Z`` suffix and the frontend
converts it to the viewer's time zone. Use :func:`utcnow` for "now" and
:class:`UtcDateTime` as the column type of every datetime model field.
"""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime:
    """The current time as an aware UTC datetime."""
    return datetime.now(UTC)


def as_utc(value: datetime) -> datetime:
    """Return *value* in UTC, reading a naive value as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


class UtcDateTime(TypeDecorator[datetime]):
    """A ``timestamptz`` column that only ever holds aware UTC datetimes.

    A naive value is taken to be UTC on the way in — asyncpg would otherwise
    read it in the process's local zone — and values come back in UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(
        self, value: datetime | None, dialect: Dialect
    ) -> datetime | None:
        return None if value is None else as_utc(value)

    def process_result_value(
        self, value: Any | None, dialect: Dialect
    ) -> datetime | None:
        return None if value is None else as_utc(value)
