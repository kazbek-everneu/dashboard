"""add unstable dom flag to screenshots

Revision ID: c8d4e6f1a2b3
Revises: a7c9e1d4b2f0
Create Date: 2026-03-14 13:10:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "c8d4e6f1a2b3"
down_revision: str | None = "a7c9e1d4b2f0"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screenshots",
        sa.Column(
            "captured_with_unstable_dom",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("screenshots", "captured_with_unstable_dom")
