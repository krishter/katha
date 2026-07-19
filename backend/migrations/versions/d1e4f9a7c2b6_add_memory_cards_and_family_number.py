"""add memory_cards table and family_whatsapp_number column

Revision ID: d1e4f9a7c2b6
Revises: c7f2a1b3d8e5
Create Date: 2026-07-18 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "d1e4f9a7c2b6"
down_revision: Union[str, Sequence[str], None] = "c7f2a1b3d8e5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("family_whatsapp_number", sa.String(), nullable=True),
    )

    op.create_table(
        "memory_cards",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "story_atom_id",
            UUID(as_uuid=True),
            sa.ForeignKey("story_atoms.id"),
            nullable=True,
        ),
        sa.Column("verbatim_quote", sa.Text(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("image_s3_key", sa.String(), nullable=False),
        sa.Column("image_public_url", sa.String(), nullable=False),
        sa.Column("delivered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("twilio_message_sid", sa.String(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("memory_cards")
    op.drop_column("user_profiles", "family_whatsapp_number")
