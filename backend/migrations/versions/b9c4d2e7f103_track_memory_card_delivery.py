"""Track whether a memory card was actually delivered

Revision ID: b9c4d2e7f103
Revises: a7d3e91c4b62

`delivered_at` was stamped when Twilio accepted the send, which says
nothing about whether Meta delivered it — a WhatsApp message outside the
24-hour window is accepted, given a SID, and rejected asynchronously. These
columns record what Twilio's status callback actually reports.

Existing rows keep `delivered_at` as it is. It is not backfilled to NULL:
the value is wrong in the sense that it meant "accepted", but rewriting
history to NULL would equally assert something unknown. `delivery_status`
stays NULL on those rows, which is how you tell them apart.
"""

import sqlalchemy as sa
from alembic import op

revision = "b9c4d2e7f103"
down_revision = "a7d3e91c4b62"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "memory_cards", sa.Column("delivery_status", sa.String(), nullable=True)
    )
    op.add_column(
        "memory_cards", sa.Column("delivery_error_code", sa.String(), nullable=True)
    )
    # The status callback finds the card by the SID Twilio gives back, so
    # this lookup happens on every delivery event.
    op.create_index(
        "ix_memory_cards_twilio_message_sid",
        "memory_cards",
        ["twilio_message_sid"],
    )


def downgrade() -> None:
    op.drop_index("ix_memory_cards_twilio_message_sid", table_name="memory_cards")
    op.drop_column("memory_cards", "delivery_error_code")
    op.drop_column("memory_cards", "delivery_status")
