"""record which principal gave consent, and the turn evidencing it

Revision ID: d5b8c1f3e207
Revises: c4a9e7b2f18d
Create Date: 2026-08-30 00:00:01.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d5b8c1f3e207"
down_revision: Union[str, Sequence[str], None] = "c4a9e7b2f18d"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # server_default rather than a backfill: every existing row came from
    # the onboarding web form, so "buyer" is not a guess, it is what they
    # are. The default stays on the column so a record written without an
    # explicit principal is still attributable.
    op.add_column(
        "consent_records",
        sa.Column("principal", sa.String(), nullable=False, server_default="buyer"),
    )
    op.add_column(
        "consent_records",
        sa.Column("evidence_ref", sa.UUID(as_uuid=True), nullable=True),
    )
    # SET NULL, not CASCADE. Consent records are retained and anonymised on
    # erasure, never deleted (DPDP audit trail) — a cascade from turns would
    # quietly destroy the record of consent having been given.
    # The ask counter cannot be derived from consent_records, because an
    # unclear answer deliberately writes no record — so without this,
    # "asked once and got a shrug" is indistinguishable from "never asked".
    op.add_column(
        "user_profiles",
        sa.Column(
            "parent_consent_asks", sa.Integer(), nullable=False, server_default="0"
        ),
    )
    op.create_foreign_key(
        "consent_records_evidence_ref_fkey",
        "consent_records",
        "turns",
        ["evidence_ref"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint(
        "consent_records_evidence_ref_fkey", "consent_records", type_="foreignkey"
    )
    op.drop_column("user_profiles", "parent_consent_asks")
    op.drop_column("consent_records", "evidence_ref")
    op.drop_column("consent_records", "principal")
