"""record when the no-reply follow-up was sent, so it is sent once

Revision ID: a7d3e91c4b62
Revises: d5b8c1f3e207
Create Date: 2026-10-04 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a7d3e91c4b62"
down_revision: Union[str, Sequence[str], None] = "d5b8c1f3e207"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # _send_followups had no way to record that it had already nudged a
    # session, so its query stayed true and the scheduler re-sent every
    # minute: 423 messages in 72 hours against one number, ~210/day, until
    # the 4-hour stale sweep closed the session.
    op.add_column(
        "sessions",
        sa.Column("followup_sent_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Backfill every session that is already closed. Without this, any
    # historical session that still matched the old predicate would be
    # eligible for a nudge the first time the fixed scheduler runs — the
    # fix would announce itself with one last burst. Closed sessions can
    # never legitimately need a follow-up, so stamping them is safe.
    op.execute(
        """
        UPDATE sessions
           SET followup_sent_at = COALESCE(ended_at, started_at)
         WHERE status <> 'active'
           AND followup_sent_at IS NULL
        """
    )


def downgrade() -> None:
    op.drop_column("sessions", "followup_sent_at")
