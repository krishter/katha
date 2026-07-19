"""add whatsapp columns and user_profiles table

Revision ID: c7f2a1b3d8e5
Revises: a3c1f8e2d940
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "c7f2a1b3d8e5"
down_revision: Union[str, Sequence[str], None] = "a3c1f8e2d940"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Add WhatsApp columns to sessions table
    op.add_column("sessions", sa.Column("whatsapp_number", sa.String(), nullable=True))
    op.add_column("sessions", sa.Column("scheduled_time", sa.Time(), nullable=True))
    op.add_column(
        "sessions",
        sa.Column("last_user_message_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "sessions",
        sa.Column("session_open_message_id", sa.String(), nullable=True),
    )

    # Create user_profiles table
    op.create_table(
        "user_profiles",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(), nullable=False, unique=True),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("whatsapp_number", sa.String(), nullable=False),
        sa.Column(
            "preferred_language", sa.String(), nullable=False, server_default="hi-IN"
        ),
        sa.Column("onboarding_context", sa.Text(), nullable=False, server_default=""),
        sa.Column("scheduled_time", sa.Time(), nullable=False),
        sa.Column(
            "timezone",
            sa.String(),
            nullable=False,
            server_default="Asia/Kolkata",
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("user_profiles")
    op.drop_column("sessions", "session_open_message_id")
    op.drop_column("sessions", "last_user_message_at")
    op.drop_column("sessions", "scheduled_time")
    op.drop_column("sessions", "whatsapp_number")
