"""Usage ledger, per-session Layer 3 snapshot, last inbound time

Revision ID: d9e2b7c4a815
Revises: c8f1a4d7b230

docs/specs/COST_TO_SERVE.md §13. One revision for the three schema changes
that spec needs:

- api_usage: one row per paid call (Anthropic, Sarvam, Twilio). Counts
  only, never text or phone numbers. session_id has no foreign key on
  purpose: deletion removes these rows by user_id, and a ledger row must
  not block or be cascaded by a session delete.
- sessions.prior_context: Layer 3 as built on the session's first turn,
  reused for its remaining turns (C2).
- user_profiles.last_inbound_at: when she last messaged, which decides
  whether Meta's 24-hour window is open. Backfilled from the latest
  sessions.last_user_message_at, which is the same moment as far as the
  existing data can say.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d9e2b7c4a815"
down_revision = "c8f1a4d7b230"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "api_usage",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("session_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("turn_id", postgresql.UUID(as_uuid=True), nullable=True),
        sa.Column("vendor", sa.String(), nullable=False),
        sa.Column("operation", sa.String(), nullable=False),
        sa.Column("model", sa.String(), nullable=True),
        sa.Column(
            "units",
            postgresql.JSONB(),
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("message_sid", sa.String(), nullable=True),
        sa.Column("status", sa.String(), nullable=True),
        sa.Column("error_code", sa.String(), nullable=True),
    )
    op.create_index("ix_api_usage_created_at", "api_usage", ["created_at"])
    op.create_index("ix_api_usage_user_id", "api_usage", ["user_id"])
    op.create_index("ix_api_usage_message_sid", "api_usage", ["message_sid"])

    op.add_column(
        "sessions",
        sa.Column("prior_context", postgresql.JSONB(), nullable=True),
    )

    op.add_column(
        "user_profiles",
        sa.Column("last_inbound_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.execute(
        """
        UPDATE user_profiles AS p
        SET last_inbound_at = s.latest
        FROM (
            SELECT user_id, max(last_user_message_at) AS latest
            FROM sessions
            WHERE last_user_message_at IS NOT NULL
            GROUP BY user_id
        ) AS s
        WHERE s.user_id = p.user_id
        """
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "last_inbound_at")
    op.drop_column("sessions", "prior_context")
    op.drop_index("ix_api_usage_message_sid", table_name="api_usage")
    op.drop_index("ix_api_usage_user_id", table_name="api_usage")
    op.drop_index("ix_api_usage_created_at", table_name="api_usage")
    op.drop_table("api_usage")
