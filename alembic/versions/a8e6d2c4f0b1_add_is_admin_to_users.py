"""add is_admin to users

Revision ID: a8e6d2c4f0b1
Revises: c3f1a8d4e6b2
Create Date: 2026-05-10 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a8e6d2c4f0b1"
down_revision: str | None = "c3f1a8d4e6b2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )
    op.execute(
        """
        UPDATE users
        SET is_admin = true
        WHERE id = (
            SELECT id
            FROM users
            ORDER BY created_at ASC, id ASC
            LIMIT 1
        )
        """
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
