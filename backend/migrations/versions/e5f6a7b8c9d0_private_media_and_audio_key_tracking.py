"""private media: track response_audio_s3_key, drop image_public_url

Revision ID: e5f6a7b8c9d0
Revises: d4e5f6a7b8c9
Create Date: 2026-08-05 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e5f6a7b8c9d0"
down_revision: Union[str, Sequence[str], None] = "d4e5f6a7b8c9"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "turns",
        sa.Column("response_audio_s3_key", sa.String(), nullable=True),
    )
    # A column named "public url" should not exist once objects are
    # private — presigned URLs are generated on demand from image_s3_key.
    op.drop_column("memory_cards", "image_public_url")


def downgrade() -> None:
    op.add_column(
        "memory_cards",
        sa.Column("image_public_url", sa.String(), nullable=False, server_default=""),
    )
    op.alter_column("memory_cards", "image_public_url", server_default=None)
    op.drop_column("turns", "response_audio_s3_key")
