"""A sensitive question from the child is hers to hear or decline

Revision ID: c3e7a9d5f1b8
Revises: b9d4f6a8c3e5

docs/specs/STORIES.md §6c: no one at Katha reads family content, so a
child's question screened "sensitive" is no longer released by an
operator. Katha asks her, without saying what it is, whether she would
like to hear it. family_questions counts those offers, and "declined"
records her no.
"""

import sqlalchemy as sa
from alembic import op

revision = "c3e7a9d5f1b8"
down_revision = "b9d4f6a8c3e5"
branch_labels = None
depends_on = None

OLD = ("pending", "held", "released", "dropped", "asked", "expired", "not_relayed")
NEW = (*OLD, "declined")


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def _statuses(values):
    op.drop_constraint("ck_family_questions_status", "family_questions", type_="check")
    op.create_check_constraint(
        "ck_family_questions_status", "family_questions", _in("status", values)
    )


def upgrade() -> None:
    op.add_column(
        "family_questions",
        sa.Column("parent_offers", sa.Integer(), nullable=False, server_default="0"),
    )
    _statuses(NEW)


def downgrade() -> None:
    op.execute(
        "UPDATE family_questions SET status = 'dropped' WHERE status = 'declined'"
    )
    _statuses(OLD)
    op.drop_column("family_questions", "parent_offers")
