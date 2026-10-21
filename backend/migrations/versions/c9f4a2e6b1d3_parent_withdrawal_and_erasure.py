"""Parent withdrawal and one erasure: the 7-day hold, quiet after a no,
and an erasure log

Revision ID: c9f4a2e6b1d3
Revises: b8e3f1c5d7a2

docs/PRE_PILOT_FIX_PLAN.md P4 (M-02).

- user_profiles.erase_after: set when she withdraws; everything is erased
  when it passes, unless an operator clears it.
- user_profiles.last_decline_reply_at: Katha answers her at most once in
  30 days after a "no" or a withdrawal.
- erasure_log: one row per erasure, with the user id hashed. Nothing
  personal.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "c9f4a2e6b1d3"
down_revision = "b8e3f1c5d7a2"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("erase_after", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "user_profiles",
        sa.Column("last_decline_reply_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_table(
        "erasure_log",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "erased_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_id_hash", sa.String(), nullable=False),
        sa.Column("actor", sa.String(), nullable=False),
        sa.Column(
            "counts",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column(
            "failures",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
    )
    op.create_index("ix_erasure_log_user_id_hash", "erasure_log", ["user_id_hash"])


def downgrade() -> None:
    op.drop_index("ix_erasure_log_user_id_hash", table_name="erasure_log")
    op.drop_table("erasure_log")
    op.drop_column("user_profiles", "last_decline_reply_at")
    op.drop_column("user_profiles", "erase_after")
