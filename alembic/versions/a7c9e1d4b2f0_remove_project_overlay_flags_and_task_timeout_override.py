"""remove project overlay flags and task timeout override

Revision ID: a7c9e1d4b2f0
Revises: f1a2b3c4d5e6
Create Date: 2026-03-14 12:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "a7c9e1d4b2f0"
down_revision: Union[str, None] = "f1a2b3c4d5e6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_column("tasks", "override_timeout")
    op.drop_column("project_configs", "hide_overlays")
    op.drop_column("project_configs", "scroll_to_lazy_load")


def downgrade() -> None:
    op.add_column(
        "project_configs",
        sa.Column(
            "scroll_to_lazy_load",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "project_configs",
        sa.Column(
            "hide_overlays",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "tasks",
        sa.Column("override_timeout", sa.Integer(), nullable=True),
    )
