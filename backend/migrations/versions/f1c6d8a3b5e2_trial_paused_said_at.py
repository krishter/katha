"""When she was last told her free conversations are paused

Revision ID: f1c6d8a3b5e2
Revises: e8b3c1d6f4a7

docs/PRE_PILOT_FIX_PLAN.md P8 (M-13, D7). At the free limit she used to
get silence; now she hears a warm pause message, at most once a week.
"""

import sqlalchemy as sa
from alembic import op

revision = "f1c6d8a3b5e2"
down_revision = "e8b3c1d6f4a7"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column("trial_paused_said_at", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "trial_paused_said_at")
