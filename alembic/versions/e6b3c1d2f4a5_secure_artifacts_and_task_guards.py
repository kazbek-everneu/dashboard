"""secure artifacts and task guards

Revision ID: e6b3c1d2f4a5
Revises: d4f7b2e9c1a3
Create Date: 2026-03-11 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "e6b3c1d2f4a5"
down_revision: str | None = "d4f7b2e9c1a3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screenshots",
        sa.Column("file_format", sa.String(length=10), nullable=False, server_default="jpeg"),
    )
    op.execute(
        """
        UPDATE screenshots
        SET file_format = CASE
            WHEN file_path LIKE '%.png' THEN 'png'
            ELSE 'jpeg'
        END
        """
    )
    op.create_index(
        "ux_tasks_active_per_project",
        "tasks",
        ["project_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('pending', 'running')"),
    )


def downgrade() -> None:
    op.drop_index("ux_tasks_active_per_project", table_name="tasks")
    op.drop_column("screenshots", "file_format")
