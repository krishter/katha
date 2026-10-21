"""Turn extraction status, so privacy fails closed

Revision ID: d2a7e5c3f9b8
Revises: c9f4a2e6b1d3

docs/PRE_PILOT_FIX_PLAN.md P5 (M-04). A turn's atoms reach the family only
once its extraction, which is where "keep this between us" is read, has
run. Turns that exist already are marked done: their extraction ran, or
never will, and hiding their atoms now would empty dashboards families
have already seen.
"""

import sqlalchemy as sa
from alembic import op

revision = "d2a7e5c3f9b8"
down_revision = "c9f4a2e6b1d3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "turns",
        sa.Column(
            "extraction_status",
            sa.String(),
            nullable=False,
            server_default="pending",
        ),
    )
    op.execute("UPDATE turns SET extraction_status = 'done'")
    op.create_check_constraint(
        "ck_turns_extraction_status",
        "turns",
        "extraction_status IN ('pending', 'done', 'failed')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_turns_extraction_status", "turns", type_="check")
    op.drop_column("turns", "extraction_status")
