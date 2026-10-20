"""add name to users

Revision ID: d9b7c4e1a2f3
Revises: c8d4e6f1a2b3
Create Date: 2026-03-15 10:30:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d9b7c4e1a2f3"
down_revision: str | None = "c8d4e6f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column("name", sa.String(length=255), nullable=True),
    )
    op.execute("UPDATE users SET name = split_part(email, '@', 1) WHERE name IS NULL")
    op.alter_column("users", "name", existing_type=sa.String(length=255), nullable=False)


def downgrade() -> None:
    op.drop_column("users", "name")
