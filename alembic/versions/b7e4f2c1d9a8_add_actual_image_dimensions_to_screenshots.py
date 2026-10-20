"""add actual image dimensions to screenshots

Revision ID: b7e4f2c1d9a8
Revises: c8d4e6f1a2b3
Create Date: 2026-03-19 16:40:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "b7e4f2c1d9a8"
down_revision: str | None = "c8d4e6f1a2b3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("screenshots", sa.Column("image_width", sa.Integer(), nullable=True))
    op.add_column("screenshots", sa.Column("image_height", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("screenshots", "image_height")
    op.drop_column("screenshots", "image_width")
