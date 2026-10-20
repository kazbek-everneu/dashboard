"""store timestamps as timestamptz

Every timestamp column was a naive ``timestamp`` holding UTC. They become
``timestamptz``; the existing values are read as UTC, so no instant changes.

Revision ID: 7b5eef65c218
Revises: 6a6c82b255d2
Create Date: 2026-10-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "7b5eef65c218"
down_revision: str | None = "6a6c82b255d2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# (table, column, nullable)
_COLUMNS: list[tuple[str, str, bool]] = [
    ("users", "created_at", False),
    ("users", "password_changed_at", False),
    ("password_reset_tokens", "expires_at", False),
    ("password_reset_tokens", "used_at", True),
    ("password_reset_tokens", "created_at", False),
    ("projects", "created_at", False),
    ("sitemap_urls", "last_fetched_at", False),
    ("tasks", "created_at", False),
    ("tasks", "started_at", True),
    ("tasks", "completed_at", True),
    ("screenshots", "created_at", False),
]


def upgrade() -> None:
    # Pin the session zone so the conversion never depends on the server's.
    op.execute("SET LOCAL timezone = 'UTC'")
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(timezone=True),
            existing_type=sa.DateTime(),
            existing_nullable=nullable,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )


def downgrade() -> None:
    op.execute("SET LOCAL timezone = 'UTC'")
    for table, column, nullable in _COLUMNS:
        op.alter_column(
            table,
            column,
            type_=sa.DateTime(),
            existing_type=sa.DateTime(timezone=True),
            existing_nullable=nullable,
            postgresql_using=f"{column} AT TIME ZONE 'UTC'",
        )
