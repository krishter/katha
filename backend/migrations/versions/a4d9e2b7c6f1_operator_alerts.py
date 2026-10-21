"""Operator alerts: rate limiting and turn-failure counting

Revision ID: a4d9e2b7c6f1
Revises: f1c6d8a3b5e2

docs/PRE_PILOT_FIX_PLAN.md P9 (M-10).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a4d9e2b7c6f1"
down_revision = "f1c6d8a3b5e2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "alerts_sent",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("key", sa.String(), nullable=False),
        sa.Column(
            "sent_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_alerts_sent_key", "alerts_sent", ["key"])
    op.create_table(
        "turn_failures",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("stage", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_turn_failures_user_id", "turn_failures", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_turn_failures_user_id", table_name="turn_failures")
    op.drop_table("turn_failures")
    op.drop_index("ix_alerts_sent_key", table_name="alerts_sent")
    op.drop_table("alerts_sent")
