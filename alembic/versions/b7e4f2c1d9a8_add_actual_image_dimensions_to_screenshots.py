"""add actual image dimensions to screenshots

Revision ID: b7e4f2c1d9a8
Revises: c8d4e6f1a2b3
Create Date: 2026-03-19 16:40:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = "b7e4f2c1d9a8"
down_revision: Union[str, None] = "c8d4e6f1a2b3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("screenshots", sa.Column("image_width", sa.Integer(), nullable=True))
    op.add_column("screenshots", sa.Column("image_height", sa.Integer(), nullable=True))


def downgrade() -> None:
    op.drop_column("screenshots", "image_height")
    op.drop_column("screenshots", "image_width")
