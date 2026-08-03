"""add turn_id and embedding_failed to story_atoms

Revision ID: b2c3d4e5f6a7
Revises: a1b2c3d4e5f6
Create Date: 2026-08-02 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import UUID

revision: str = "b2c3d4e5f6a7"
down_revision: Union[str, Sequence[str], None] = "a1b2c3d4e5f6"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "story_atoms",
        sa.Column(
            "turn_id",
            UUID(as_uuid=True),
            sa.ForeignKey("turns.id", ondelete="SET NULL"),
            nullable=True,
        ),
    )
    op.create_index("ix_story_atoms_turn_id", "story_atoms", ["turn_id"])
    op.add_column(
        "story_atoms",
        sa.Column(
            "embedding_failed",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("story_atoms", "embedding_failed")
    op.drop_index("ix_story_atoms_turn_id", table_name="story_atoms")
    op.drop_column("story_atoms", "turn_id")
