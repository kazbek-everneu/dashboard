"""add override_browsers to tasks

Revision ID: c2e4d6f8a0b2
Revises: a1f9c3e72b05
Create Date: 2026-03-09 13:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "c2e4d6f8a0b2"
down_revision: str | None = "a1f9c3e72b05"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("override_browsers", postgresql.JSONB(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "override_browsers")
