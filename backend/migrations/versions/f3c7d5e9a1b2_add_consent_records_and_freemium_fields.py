"""add consent_records table and freemium/onboarding fields

Revision ID: f3c7d5e9a1b2
Revises: e6a2b8f4c1d9
Create Date: 2026-07-25 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "f3c7d5e9a1b2"
down_revision: Union[str, Sequence[str], None] = "e6a2b8f4c1d9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "consent_records",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("email_hash", sa.String(), nullable=False),
        sa.Column("consent_version", sa.String(), nullable=False),
        sa.Column(
            "consented_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
        sa.Column("ip_address", sa.String(), nullable=True),
        sa.Column("user_agent", sa.String(), nullable=True),
    )

    op.add_column(
        "family_accounts",
        sa.Column("plan", sa.String(), nullable=False, server_default="free"),
    )
    op.add_column(
        "family_accounts",
        sa.Column("upgraded_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "family_accounts",
        sa.Column(
            "onboarding_complete",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("family_accounts", "onboarding_complete")
    op.drop_column("family_accounts", "upgraded_at")
    op.drop_column("family_accounts", "plan")
    op.drop_table("consent_records")
