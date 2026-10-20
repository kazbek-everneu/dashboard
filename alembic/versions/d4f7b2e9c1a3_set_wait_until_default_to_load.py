"""set wait_until default to load

Revision ID: d4f7b2e9c1a3
Revises: c2e4d6f8a0b2
Create Date: 2026-03-10 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d4f7b2e9c1a3"
down_revision: str | None = "c2e4d6f8a0b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Update the column default for new rows
    op.alter_column(
        "project_configs",
        "wait_until",
        server_default=sa.text("'load'"),
        existing_type=sa.String(),
        existing_nullable=False,
    )
    # Migrate existing rows that still have the old "networkidle" default
    op.execute(
        "UPDATE project_configs SET wait_until = 'load' WHERE wait_until = 'networkidle'"
    )


def downgrade() -> None:
    op.alter_column(
        "project_configs",
        "wait_until",
        server_default=sa.text("'networkidle'"),
        existing_type=sa.String(),
        existing_nullable=False,
    )
    op.execute(
        "UPDATE project_configs SET wait_until = 'networkidle' WHERE wait_until = 'load'"
    )
