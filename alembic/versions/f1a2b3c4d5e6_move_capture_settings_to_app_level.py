"""move capture settings to app level

Revision ID: f1a2b3c4d5e6
Revises: e6b3c1d2f4a5
Create Date: 2026-03-14 10:15:00.000000
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f1a2b3c4d5e6"
down_revision: str | None = "e6b3c1d2f4a5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

APPLICATION_SETTINGS_SINGLETON_ID = 1
DEFAULT_SCREENSHOT_FORMAT = "jpeg"
DEFAULT_SCREENSHOT_QUALITY = 85
SCREENSHOT_FORMAT_ENUM = sa.Enum(
    "jpeg",
    "png",
    name="screenshot_format_enum",
    native_enum=False,
)


def upgrade() -> None:
    op.create_table(
        "application_settings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "screenshot_format",
            SCREENSHOT_FORMAT_ENUM,
            nullable=False,
            server_default=DEFAULT_SCREENSHOT_FORMAT,
        ),
        sa.Column(
            "screenshot_quality",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_SCREENSHOT_QUALITY),
        ),
        sa.PrimaryKeyConstraint("id"),
    )

    op.execute(
        sa.text(
            """
            INSERT INTO application_settings (id, screenshot_format, screenshot_quality)
            VALUES (:id, :fmt, :quality)
            """
        ).bindparams(
            id=APPLICATION_SETTINGS_SINGLETON_ID,
            fmt=DEFAULT_SCREENSHOT_FORMAT,
            quality=DEFAULT_SCREENSHOT_QUALITY,
        )
    )

    op.drop_column("project_configs", "format")
    op.drop_column("project_configs", "quality")
    op.drop_column("project_configs", "full_page")
    op.drop_column("tasks", "override_format")
    op.drop_column("tasks", "override_quality")


def downgrade() -> None:
    op.add_column(
        "tasks",
        sa.Column("override_quality", sa.Integer(), nullable=True),
    )
    op.add_column(
        "tasks",
        sa.Column("override_format", sa.String(), nullable=True),
    )
    op.add_column(
        "project_configs",
        sa.Column(
            "full_page",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("true"),
        ),
    )
    op.add_column(
        "project_configs",
        sa.Column(
            "quality",
            sa.Integer(),
            nullable=False,
            server_default=str(DEFAULT_SCREENSHOT_QUALITY),
        ),
    )
    op.add_column(
        "project_configs",
        sa.Column(
            "format",
            sa.String(),
            nullable=False,
            server_default=DEFAULT_SCREENSHOT_FORMAT,
        ),
    )

    op.execute(
        sa.text(
            """
            UPDATE project_configs
            SET format = COALESCE(
                    (SELECT screenshot_format FROM application_settings WHERE id = :id),
                    :fmt
                ),
                quality = COALESCE(
                    (SELECT screenshot_quality FROM application_settings WHERE id = :id),
                    :quality
                ),
                full_page = true
            """
        ).bindparams(
            id=APPLICATION_SETTINGS_SINGLETON_ID,
            fmt=DEFAULT_SCREENSHOT_FORMAT,
            quality=DEFAULT_SCREENSHOT_QUALITY,
        )
    )

    op.drop_table("application_settings")
