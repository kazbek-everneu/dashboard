"""add capture report to screenshots

What the capture pipeline learned about each screenshot: the emulated device
type, the capture warnings (a JSON list, empty for a clean capture) and the
capture metadata (a versioned JSON object). Existing rows get NULL device type
and metadata and an empty warnings list.

Revision ID: 8e1643923a2e
Revises: 48e39fa30667
Create Date: 2026-10-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "8e1643923a2e"
down_revision: str | None = "48e39fa30667"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "screenshots", sa.Column("device_type", sa.String(length=16), nullable=True)
    )
    op.add_column(
        "screenshots",
        sa.Column(
            "warnings",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.add_column(
        "screenshots", sa.Column("capture_meta", postgresql.JSONB(), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("screenshots", "capture_meta")
    op.drop_column("screenshots", "warnings")
    op.drop_column("screenshots", "device_type")
