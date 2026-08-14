"""track the session-open voice note's S3 key so deletion can reach it

Revision ID: c4a9e7b2f18d
Revises: f6a7b8c9d0e1
Create Date: 2026-08-14 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c4a9e7b2f18d"
down_revision: Union[str, Sequence[str], None] = "f6a7b8c9d0e1"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Session-open voice notes uploaded before this column existed have no
    # recorded key and stay unreachable by per-user deletion. They are
    # private objects, so this is a residency/retention gap rather than an
    # exposure, and it is not backfillable from the database — the key was
    # discarded at the call site. Any such objects predate the pilot and
    # should be swept manually from the bucket if the prefix is non-empty.
    op.add_column(
        "sessions",
        sa.Column("session_open_audio_s3_key", sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("sessions", "session_open_audio_s3_key")
