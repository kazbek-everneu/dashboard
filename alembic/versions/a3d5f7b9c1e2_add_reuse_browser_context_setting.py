"""add reuse browser context setting

Adds the nullable reuse_browser_context override to application_settings.
NULL means "inherit REUSE_BROWSER_CONTEXT from .env" (see
app.settings.service.resolve_setting / _ENV_FALLBACKS).

Revision ID: a3d5f7b9c1e2
Revises: e8c1b4a9d3f2
Create Date: 2026-10-19 00:00:00.000000
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "a3d5f7b9c1e2"
down_revision: Union[str, None] = "e8c1b4a9d3f2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "application_settings",
        sa.Column("reuse_browser_context", sa.Boolean(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("application_settings", "reuse_browser_context")
