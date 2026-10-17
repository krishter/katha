"""Katha's persona gender per parent

Revision ID: e4b1c9a7d352
Revises: d9e2b7c4a815

The family chooses a female or male Katha at setup. With the parent's
language it picks the Sarvam model and voice (media/voices.py). Every
existing profile becomes "female", which is the voice they already hear.
"""

import sqlalchemy as sa
from alembic import op

revision = "e4b1c9a7d352"
down_revision = "d9e2b7c4a815"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user_profiles",
        sa.Column(
            "katha_gender", sa.String(), nullable=False, server_default="female"
        ),
    )
    op.create_check_constraint(
        "ck_user_profiles_katha_gender",
        "user_profiles",
        "katha_gender IN ('female', 'male')",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_user_profiles_katha_gender", "user_profiles", type_="check"
    )
    op.drop_column("user_profiles", "katha_gender")
