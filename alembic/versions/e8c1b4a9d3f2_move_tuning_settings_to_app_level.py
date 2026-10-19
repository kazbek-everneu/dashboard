"""move tuning settings to app level

Adds nullable override columns to application_settings for values that
otherwise come from .env (workers/performance, security/sessions, email).
NULL means "inherit the .env default"; a set value overrides it at runtime
(see app.settings.service.resolve_setting / _ENV_FALLBACKS).

Revision ID: e8c1b4a9d3f2
Revises: d2a4c6e8f0b3
Create Date: 2026-05-31 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "e8c1b4a9d3f2"
down_revision: Union[str, None] = "d2a4c6e8f0b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# (column name, SQLAlchemy type). All nullable, no server_default.
_NEW_COLUMNS: tuple[tuple[str, sa.types.TypeEngine], ...] = (
    ("sem_limit", sa.Integer()),
    ("task_job_timeout_seconds", sa.Integer()),
    ("access_token_expire_minutes", sa.Integer()),
    ("password_reset_token_expire_minutes", sa.Integer()),
    ("password_reset_rate_limit_max", sa.Integer()),
    ("password_reset_rate_limit_window_seconds", sa.Integer()),
    ("smtp_host", sa.String()),
    ("smtp_port", sa.Integer()),
    ("smtp_from_email", sa.String()),
    ("smtp_use_tls", sa.Boolean()),
    ("frontend_base_url", sa.String()),
)


def upgrade() -> None:
    for name, col_type in _NEW_COLUMNS:
        op.add_column(
            "application_settings",
            sa.Column(name, col_type, nullable=True),
        )


def downgrade() -> None:
    for name, _col_type in reversed(_NEW_COLUMNS):
        op.drop_column("application_settings", name)
