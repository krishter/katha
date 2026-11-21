"""Her answer to a Story's read-back

Revision ID: a8c3e5f7b2d4
Revises: d2a7f4c9e1b6

docs/specs/STORIES.md Stage 2: the answer she gave to a Story read back to
her, the turn it came in, and how many times she has corrected it. The same
evidence conversation_cards keeps for a card.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "a8c3e5f7b2d4"
down_revision = "d2a7f4c9e1b6"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("stories", sa.Column("parent_answer", sa.String(), nullable=True))
    op.add_column(
        "stories",
        sa.Column(
            "parent_answer_turn_id", postgresql.UUID(as_uuid=True), nullable=True
        ),
    )
    op.add_column(
        "stories",
        sa.Column("parent_answered_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        "stories",
        sa.Column("correction_round", sa.Integer(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    for column in (
        "correction_round",
        "parent_answered_at",
        "parent_answer_turn_id",
        "parent_answer",
    ):
        op.drop_column("stories", column)
