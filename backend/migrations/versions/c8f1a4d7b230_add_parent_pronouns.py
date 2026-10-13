"""Record the storyteller's pronouns

Revision ID: c8f1a4d7b230
Revises: b9c4d2e7f103

Chained onto the memory-card delivery migration rather than branching
beside it: two revisions sharing a parent give main two alembic heads, and
`alembic upgrade head` then refuses to run — which breaks the deploy, not
just the migration.

There was no field for this. A family that wrote "Amma — she/her" into the
free-text "A little about them" box had it reach the prompt for exactly one
turn: onboarding_context is rendered only when nothing else is known about
the user, so the moment the first fact or open thread was extracted it
dropped out of the prompt permanently and Katha was left inferring gender.

Defaults to empty, which means NOT STATED. It is deliberately not
backfilled from anything — not from the name, not from the free-text
context — because a guess recorded as a fact is worse than a blank, and
the prompt treats a blank as "do not use gendered forms".
"""

import sqlalchemy as sa
from alembic import op

revision = "c8f1a4d7b230"
down_revision = "b9c4d2e7f103"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("pronouns", sa.String(), nullable=False, server_default=""),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "pronouns")
