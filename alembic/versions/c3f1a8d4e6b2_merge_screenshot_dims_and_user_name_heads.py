"""merge screenshot dims and user name heads

Revision ID: c3f1a8d4e6b2
Revises: d9b7c4e1a2f3, b7e4f2c1d9a8
Create Date: 2026-03-19 17:05:00.000000
"""

from typing import Sequence, Union


revision: str = "c3f1a8d4e6b2"
down_revision: Union[str, Sequence[str], None] = ("d9b7c4e1a2f3", "b7e4f2c1d9a8")
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
