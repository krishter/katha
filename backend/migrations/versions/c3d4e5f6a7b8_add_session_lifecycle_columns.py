"""add session lifecycle columns

Revision ID: c3d4e5f6a7b8
Revises: b2c3d4e5f6a7
Create Date: 2026-08-02 00:00:02.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c3d4e5f6a7b8"
down_revision: Union[str, Sequence[str], None] = "b2c3d4e5f6a7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "sessions",
        sa.Column("status", sa.String(), nullable=False, server_default="active"),
    )
    op.add_column(
        "sessions",
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("ended_reason", sa.String(), nullable=True),
    )
    op.create_index("ix_sessions_status", "sessions", ["status"])


def downgrade() -> None:
    op.drop_index("ix_sessions_status", table_name="sessions")
    op.drop_column("sessions", "ended_reason")
    op.drop_column("sessions", "ended_at")
    op.drop_column("sessions", "status")
