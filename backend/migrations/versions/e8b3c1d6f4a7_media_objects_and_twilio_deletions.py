"""Track every uploaded object, and queue Twilio deletions

Revision ID: e8b3c1d6f4a7
Revises: d2a7e5c3f9b8

docs/PRE_PILOT_FIX_PLAN.md P6 (M-05, M-08).

- media_objects: every object uploaded for a known user, so erasure can
  find the ones no typed column records (the consent welcome, system
  voice notes).
- twilio_deletions: messages and media still to be deleted from Twilio,
  retried until they are gone.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "e8b3c1d6f4a7"
down_revision = "d2a7e5c3f9b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "media_objects",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("s3_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_media_objects_user_id", "media_objects", ["user_id"])
    op.create_table(
        "twilio_deletions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("message_sid", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.String(), nullable=True),
        sa.Column("direction", sa.String(), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_twilio_deletions_user_id", "twilio_deletions", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_twilio_deletions_user_id", table_name="twilio_deletions")
    op.drop_table("twilio_deletions")
    op.drop_index("ix_media_objects_user_id", table_name="media_objects")
    op.drop_table("media_objects")
