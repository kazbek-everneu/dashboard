"""replace chromium with chrome

The chromium browser server was retired; branded Chrome is the default. Rewrites
"chromium" to "chrome" (deduplicated, order kept) in every project config's
default_browsers and per-viewport browsers, and in the browser overrides of
tasks that have not started yet. Finished tasks and screenshots keep their
history. The worker still aliases any remaining "chromium" to chrome.

Downgrade only restores the old server default — the rewritten rows are not
turned back into "chromium".

Revision ID: 6a6c82b255d2
Revises: a3d5f7b9c1e2
Create Date: 2026-10-19 00:00:00.000000
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "6a6c82b255d2"
down_revision: str | None = "a3d5f7b9c1e2"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_OLD = "chromium"
_NEW = "chrome"

project_configs = sa.table(
    "project_configs",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("default_browsers", postgresql.JSONB()),
    sa.column("viewports", postgresql.JSONB()),
)
tasks = sa.table(
    "tasks",
    sa.column("id", postgresql.UUID(as_uuid=True)),
    sa.column("status", sa.String()),
    sa.column("override_browser", sa.String()),
    sa.column("override_browsers", postgresql.JSONB()),
    sa.column("override_viewports", postgresql.JSONB()),
)


def _browsers(names: Any) -> Any:
    if not isinstance(names, list):
        return names
    return list(dict.fromkeys(_NEW if n == _OLD else n for n in names))


def _viewports(viewports: Any) -> Any:
    if not isinstance(viewports, list):
        return viewports
    return [
        {**vp, "browsers": _browsers(vp["browsers"])}
        if isinstance(vp, dict) and vp.get("browsers")
        else vp
        for vp in viewports
    ]


def _changed(old: dict[str, Any], new: dict[str, Any]) -> dict[str, Any]:
    # Only rewritten columns are written back, so an untouched SQL NULL in a
    # JSONB column never turns into a JSON null.
    return {k: v for k, v in new.items() if v != old[k]}


def upgrade() -> None:
    op.alter_column(
        "project_configs",
        "default_browsers",
        server_default=sa.text("'[\"chrome\"]'::jsonb"),
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )

    bind = op.get_bind()
    for row in bind.execute(sa.select(project_configs)).all():
        old = {"default_browsers": row.default_browsers, "viewports": row.viewports}
        new = {
            "default_browsers": _browsers(row.default_browsers),
            "viewports": _viewports(row.viewports),
        }
        if changed := _changed(old, new):
            bind.execute(
                project_configs.update()
                .where(project_configs.c.id == row.id)
                .values(**changed)
            )

    pending = sa.select(tasks).where(tasks.c.status == "pending")
    for row in bind.execute(pending).all():
        old = {
            "override_browser": row.override_browser,
            "override_browsers": row.override_browsers,
            "override_viewports": row.override_viewports,
        }
        new = {
            "override_browser": (
                _NEW if row.override_browser == _OLD else row.override_browser
            ),
            "override_browsers": _browsers(row.override_browsers),
            "override_viewports": _viewports(row.override_viewports),
        }
        if changed := _changed(old, new):
            bind.execute(tasks.update().where(tasks.c.id == row.id).values(**changed))


def downgrade() -> None:
    op.alter_column(
        "project_configs",
        "default_browsers",
        server_default=sa.text("'[\"chromium\"]'::jsonb"),
        existing_type=postgresql.JSONB(),
        existing_nullable=False,
    )
