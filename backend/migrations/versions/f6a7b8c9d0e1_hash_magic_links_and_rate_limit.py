"""hash magic link tokens at rest, add rate-limit timestamp

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-08-05 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f6a7b8c9d0e1"
down_revision: Union[str, Sequence[str], None] = "e5f6a7b8c9d0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Existing plaintext tokens can't be retroactively hashed without the
    # raw value — and they're short-lived (MAGIC_LINK_EXPIRE_MINUTES), so
    # dropping any still-outstanding ones on deploy is an acceptable,
    # explicit tradeoff rather than silently leaving the column mismatched.
    op.execute("DELETE FROM magic_link_tokens")
    op.alter_column("magic_link_tokens", "token", new_column_name="token_hash")

    op.add_column(
        "family_accounts",
        sa.Column(
            "magic_link_last_requested_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )


def downgrade() -> None:
    op.drop_column("family_accounts", "magic_link_last_requested_at")
    op.alter_column("magic_link_tokens", "token_hash", new_column_name="token")
