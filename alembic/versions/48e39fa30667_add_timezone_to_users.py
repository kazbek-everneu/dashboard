"""add timezone to users

The IANA time zone a user wants times shown in; NULL follows the browser.

Revision ID: 48e39fa30667
Revises: 9b657a038395
Create Date: 2026-10-20 00:00:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "48e39fa30667"
down_revision: str | None = "9b657a038395"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "users", sa.Column("timezone", sa.String(length=64), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("users", "timezone")
