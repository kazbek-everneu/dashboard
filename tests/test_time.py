from datetime import UTC, datetime, timedelta, timezone

from sqlalchemy.dialects import postgresql

from app.core.time import UtcDateTime, as_utc, utcnow

_DIALECT = postgresql.dialect()


def test_utcnow_is_aware_utc() -> None:
    assert utcnow().tzinfo is UTC


def test_as_utc_reads_naive_values_as_utc() -> None:
    assert as_utc(datetime(2026, 1, 1, 12)) == datetime(2026, 1, 1, 12, tzinfo=UTC)


def test_as_utc_converts_other_zones() -> None:
    plus5 = datetime(2026, 1, 1, 17, tzinfo=timezone(timedelta(hours=5)))
    converted = as_utc(plus5)
    assert converted == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert converted.tzinfo is UTC


def test_utc_datetime_column_binds_and_reads_aware_utc() -> None:
    column_type = UtcDateTime()
    bound = column_type.process_bind_param(datetime(2026, 1, 1, 12), _DIALECT)
    assert bound == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert column_type.process_bind_param(None, _DIALECT) is None

    read = column_type.process_result_value(
        datetime(2026, 1, 1, 17, tzinfo=timezone(timedelta(hours=5))), _DIALECT
    )
    assert read == datetime(2026, 1, 1, 12, tzinfo=UTC)
    assert read.tzinfo is UTC


def test_utc_datetime_column_is_timestamptz() -> None:
    assert UtcDateTime().load_dialect_impl(_DIALECT).timezone is True
