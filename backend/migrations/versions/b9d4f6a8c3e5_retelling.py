"""Telling a part again, or setting it aside

Revision ID: b9d4f6a8c3e5
Revises: a8c3e5f7b2d4

docs/specs/STORIES.md §6c: with no operator review, a correction Katha
cannot write is put to her. She tells that part again or sets the story
aside. Cards and Stories both record that they need her, and how many
rewrites have failed; a Story keeps the turns she added; "set_aside"
becomes a reason to retire a Story.
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "b9d4f6a8c3e5"
down_revision = "a8c3e5f7b2d4"
branch_labels = None
depends_on = None

OLD_REASONS = ("merged", "operator", "declined_twice", "fidelity")
NEW_REASONS = (*OLD_REASONS, "set_aside")


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _reasons(values):
    op.drop_constraint("ck_stories_retired_reason", "stories", type_="check")
    op.create_check_constraint(
        "ck_stories_retired_reason",
        "stories",
        "retired_reason IS NULL OR " + _in("retired_reason", values),
    )


def upgrade() -> None:
    for table in ("stories", "conversation_cards"):
        op.add_column(
            table,
            sa.Column(
                "needs_retelling", sa.Boolean(), nullable=False, server_default="false"
            ),
        )
        op.add_column(
            table,
            sa.Column(
                "failed_rewrites", sa.Integer(), nullable=False, server_default="0"
            ),
        )
    op.add_column(
        "stories",
        sa.Column(
            "added_source_turn_ids",
            postgresql.JSONB(),
            nullable=False,
            server_default="[]",
        ),
    )
    _reasons(NEW_REASONS)


def downgrade() -> None:
    op.execute(
        "UPDATE stories SET retired_reason = 'operator' "
        "WHERE retired_reason = 'set_aside'"
    )
    _reasons(OLD_REASONS)
    op.drop_column("stories", "added_source_turn_ids")
    for table in ("conversation_cards", "stories"):
        op.drop_column(table, "failed_rewrites")
        op.drop_column(table, "needs_retelling")
