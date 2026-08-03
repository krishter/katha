"""add turns table

Revision ID: a1b2c3d4e5f6
Revises: f3c7d5e9a1b2
Create Date: 2026-08-02 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB, UUID

revision: str = "a1b2c3d4e5f6"
down_revision: Union[str, Sequence[str], None] = "f3c7d5e9a1b2"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "turns",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("turn_number", sa.Integer(), nullable=False),
        sa.Column("inbound_message_sid", sa.String(), nullable=True, unique=True),
        sa.Column("transcript", sa.Text(), nullable=False),
        sa.Column("detected_language", sa.String(), nullable=True),
        sa.Column("response_text", sa.Text(), nullable=False),
        sa.Column(
            "extraction_json", JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column("input_tokens", sa.Integer(), nullable=True),
        sa.Column("output_tokens", sa.Integer(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index("ix_turns_user_id", "turns", ["user_id"])
    op.create_index("ix_turns_session_id", "turns", ["session_id"])


def downgrade() -> None:
    op.drop_index("ix_turns_session_id", table_name="turns")
    op.drop_index("ix_turns_user_id", table_name="turns")
    op.drop_table("turns")
