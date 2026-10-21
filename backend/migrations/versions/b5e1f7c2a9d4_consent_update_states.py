"""Consent states for the 1.1 notice update

Revision ID: b5e1f7c2a9d4
Revises: a4d9e2b7c6f1

docs/PRE_PILOT_FIX_PLAN.md P10 (M-06). A parent who agreed to the 1.0
wording is asked once more: "updating", then "updating_reasked" after an
unclear answer.
"""

from alembic import op

revision = "b5e1f7c2a9d4"
down_revision = "a4d9e2b7c6f1"
branch_labels = None
depends_on = None

_OLD = "parent_consent_state IN ('asking', 'confirming', 'halted')"
_NEW = (
    "parent_consent_state IN "
    "('asking', 'confirming', 'halted', 'updating', 'updating_reasked')"
)


def upgrade() -> None:
    op.drop_constraint(
        "ck_user_profiles_parent_consent_state", "user_profiles", type_="check"
    )
    op.create_check_constraint(
        "ck_user_profiles_parent_consent_state", "user_profiles", _NEW
    )


def downgrade() -> None:
    op.execute(
        "UPDATE user_profiles SET parent_consent_state = 'asking' "
        "WHERE parent_consent_state IN ('updating', 'updating_reasked')"
    )
    op.drop_constraint(
        "ck_user_profiles_parent_consent_state", "user_profiles", type_="check"
    )
    op.create_check_constraint(
        "ck_user_profiles_parent_consent_state", "user_profiles", _OLD
    )
