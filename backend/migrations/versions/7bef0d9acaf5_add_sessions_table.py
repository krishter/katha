"""add sessions table

Revision ID: 7bef0d9acaf5
Revises: b4b2ac2e071c
Create Date: 2026-07-11 22:35:23.383276

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

# revision identifiers, used by Alembic.
revision: str = "7bef0d9acaf5"
down_revision: Union[str, Sequence[str], None] = "b4b2ac2e071c"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')
    op.create_table(
        "sessions",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("session_number", sa.Integer(), nullable=False, server_default="1"),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("exchange_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("energy_signal", sa.String(), nullable=False, server_default="high"),
        sa.Column("goal_met", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column(
            "session_end_suggested",
            sa.Boolean(),
            nullable=False,
            server_default="false",
        ),
        sa.Column(
            "started_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("sessions")
