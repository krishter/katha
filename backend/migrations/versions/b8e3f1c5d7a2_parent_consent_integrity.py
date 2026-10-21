"""Parent consent integrity: confirmation state, consent bound to the
number, one profile per number, a confirmed family number

Revision ID: b8e3f1c5d7a2
Revises: c3d8f1a6e2b4

docs/PRE_PILOT_FIX_PLAN.md P3 (M-01, M-03).

- user_profiles.parent_consent_state: "asking", "confirming" (she said
  yes once and has been asked to confirm) or "halted". Halted used to be
  derived from parent_consent_asks >= 2, which halted her the moment the
  re-ask was sent, so her answer to it was never read. Profiles that
  rule had already halted stay halted.
- consent_records.whatsapp_number: the number a parent record was given
  from. Backfilled from the profile's current number.
- A unique index on user_profiles.whatsapp_number. The migration stops,
  naming the user_ids, if any number is already shared.
- user_profiles.family_number_confirmed_at, and the hashed code the child
  sends to confirm it (on family_accounts). Families set up before this
  migration are marked confirmed (owner's decision, 2026-10-21): their
  cards already reach those numbers, and stopping them would be a change
  no one asked for. Every family set up from now on confirms.
"""

import sqlalchemy as sa
from alembic import op

revision = "b8e3f1c5d7a2"
down_revision = "c3d8f1a6e2b4"
branch_labels = None
depends_on = None


def duplicate_numbers(bind) -> dict[str, list[str]]:
    """{number: [user_id, ...]} for every number more than one profile
    holds. Separate so a test can run it against seeded duplicates."""
    rows = bind.execute(
        sa.text(
            "SELECT whatsapp_number, array_agg(user_id ORDER BY user_id) "
            "FROM user_profiles GROUP BY whatsapp_number HAVING count(*) > 1"
        )
    ).all()
    return {number: list(user_ids) for number, user_ids in rows}


def upgrade() -> None:
    bind = op.get_bind()

    duplicates = duplicate_numbers(bind)
    if duplicates:
        # Never pick a winner silently: which profile is really hers is a
        # question for a person, and the losing profile holds a life story.
        listed = "; ".join(
            f"{len(ids)} profiles: {', '.join(ids)}" for ids in duplicates.values()
        )
        raise RuntimeError(
            "user_profiles.whatsapp_number is shared and cannot be made unique. "
            f"Resolve these by hand, then re-run: {listed}"
        )

    op.add_column(
        "user_profiles",
        sa.Column(
            "parent_consent_state",
            sa.String(),
            nullable=False,
            server_default="asking",
        ),
    )
    op.create_check_constraint(
        "ck_user_profiles_parent_consent_state",
        "user_profiles",
        "parent_consent_state IN ('asking', 'confirming', 'halted')",
    )
    halted = bind.execute(
        sa.text(
            "UPDATE user_profiles p SET parent_consent_state = 'halted' "
            "WHERE p.parent_consent_asks >= 2 AND NOT EXISTS ("
            "  SELECT 1 FROM consent_records c WHERE c.user_id = p.user_id "
            "  AND c.principal = 'parent' "
            "  AND c.consent_version NOT LIKE 'share-%')"
        )
    ).rowcount
    print(f"parent_consent_state: {halted} profile(s) kept halted")

    op.add_column(
        "user_profiles",
        sa.Column(
            "family_number_confirmed_at", sa.DateTime(timezone=True), nullable=True
        ),
    )
    grandfathered = bind.execute(
        sa.text(
            "UPDATE user_profiles SET family_number_confirmed_at = now() "
            "WHERE family_whatsapp_number IS NOT NULL"
        )
    ).rowcount
    print(
        f"family_number_confirmed_at: {grandfathered} existing family "
        "number(s) marked confirmed"
    )
    op.add_column(
        "family_accounts",
        sa.Column("family_confirm_code_hash", sa.String(), nullable=True),
    )
    op.add_column(
        "family_accounts",
        sa.Column(
            "family_confirm_code_expires_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.add_column(
        "consent_records",
        sa.Column("whatsapp_number", sa.String(), nullable=True),
    )
    backfilled = bind.execute(
        sa.text(
            "UPDATE consent_records c SET whatsapp_number = p.whatsapp_number "
            "FROM user_profiles p WHERE c.user_id = p.user_id "
            "AND c.principal = 'parent' AND c.whatsapp_number IS NULL"
        )
    ).rowcount
    print(f"consent_records.whatsapp_number: backfilled {backfilled} parent record(s)")

    op.create_index(
        "uq_user_profiles_whatsapp_number",
        "user_profiles",
        ["whatsapp_number"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index("uq_user_profiles_whatsapp_number", table_name="user_profiles")
    op.drop_column("consent_records", "whatsapp_number")
    op.drop_column("family_accounts", "family_confirm_code_expires_at")
    op.drop_column("family_accounts", "family_confirm_code_hash")
    op.drop_column("user_profiles", "family_number_confirmed_at")
    op.drop_constraint(
        "ck_user_profiles_parent_consent_state", "user_profiles", type_="check"
    )
    op.drop_column("user_profiles", "parent_consent_state")
