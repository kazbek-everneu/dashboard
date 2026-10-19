"""add worker job id to tasks

Revision ID: d2a4c6e8f0b3
Revises: b6f0a8d2c9e1
Create Date: 2026-05-12 00:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "d2a4c6e8f0b3"
down_revision: str | None = "b6f0a8d2c9e1"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("worker_job_id", sa.String(length=128), nullable=True),
    )
    op.create_index("ix_tasks_worker_job_id", "tasks", ["worker_job_id"])


def downgrade() -> None:
    op.drop_index("ix_tasks_worker_job_id", table_name="tasks")
    op.drop_column("tasks", "worker_job_id")
