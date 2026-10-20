"""rename default_browser to default_browsers (string → JSONB list)

Revision ID: a1f9c3e72b05
Revises: b3d5a8f2e1c4
Create Date: 2026-03-09 12:00:00.000000

"""
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "a1f9c3e72b05"
down_revision: str | None = "b3d5a8f2e1c4"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Add new JSONB column with a safe default
    op.add_column(
        "project_configs",
        sa.Column(
            "default_browsers",
            postgresql.JSONB(),
            nullable=False,
            server_default='["chromium"]',
        ),
    )
    # Migrate existing string values into a single-element JSON array
    op.execute(
        "UPDATE project_configs SET default_browsers = to_jsonb(ARRAY[default_browser])"
    )
    # Drop the old column
    op.drop_column("project_configs", "default_browser")


def downgrade() -> None:
    # Add the old string column back
    op.add_column(
        "project_configs",
        sa.Column("default_browser", sa.String(), nullable=False, server_default="chromium"),
    )
    # Restore string value from the first element of the array
    op.execute(
        "UPDATE project_configs SET default_browser = default_browsers->>0"
    )
    op.drop_column("project_configs", "default_browsers")
