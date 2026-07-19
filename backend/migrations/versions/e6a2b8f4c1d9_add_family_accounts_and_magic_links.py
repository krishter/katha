"""add family_accounts and magic_link_tokens tables

Revision ID: e6a2b8f4c1d9
Revises: d1e4f9a7c2b6
Create Date: 2026-07-19 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "e6a2b8f4c1d9"
down_revision: Union[str, Sequence[str], None] = "d1e4f9a7c2b6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "family_accounts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(), nullable=False, unique=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    op.create_table(
        "magic_link_tokens",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("email", sa.String(), nullable=False),
        sa.Column("token", sa.String(), nullable=False, unique=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "used", sa.Boolean(), nullable=False, server_default=sa.text("false")
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )
    op.create_index(
        "idx_magic_link_token",
        "magic_link_tokens",
        ["token"],
        unique=False,
        postgresql_where=sa.text("used = false"),
    )


def downgrade() -> None:
    op.drop_index("idx_magic_link_token", table_name="magic_link_tokens")
    op.drop_table("magic_link_tokens")
    op.drop_table("family_accounts")
