"""add heartbeat_at to tasks

The worker beats this column while it runs a task; a running task without a
recent beat has lost its worker and is failed by the reaper.

Revision ID: 9b657a038395
Revises: 7b5eef65c218
Create Date: 2026-10-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "9b657a038395"
down_revision: str | None = "7b5eef65c218"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("heartbeat_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("tasks", "heartbeat_at")
