"""add story_atoms and facts tables

Revision ID: a3c1f8e2d940
Revises: 7bef0d9acaf5
Create Date: 2026-07-12 00:00:00.000000

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = "a3c1f8e2d940"
down_revision: Union[str, Sequence[str], None] = "7bef0d9acaf5"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")

    op.create_table(
        "story_atoms",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "session_id",
            UUID(as_uuid=True),
            sa.ForeignKey("sessions.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("domain", sa.String(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("narrative", sa.Text(), nullable=False),
        sa.Column("who", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("what", sa.Text(), nullable=True),
        sa.Column("when_approx", sa.Text(), nullable=True),
        sa.Column("where_approx", sa.Text(), nullable=True),
        sa.Column("why", sa.Text(), nullable=True),
        sa.Column(
            "completeness_score", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("verbatim_quote", sa.Text(), nullable=True),
        sa.Column(
            "open_threads", ARRAY(sa.Text()), nullable=False, server_default="{}"
        ),
        sa.Column("audio_timestamp_start", sa.Float(), nullable=True),
        sa.Column("audio_timestamp_end", sa.Float(), nullable=True),
        # Placeholder type — SQLAlchemy can't natively express pgvector's
        # `vector` type, so this column is immediately re-typed via raw DDL
        # below (with_variant() requires a type object, not a text clause,
        # so `sa.text("vector(1536)")` here would raise AttributeError).
        sa.Column("embedding", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )

    # Use raw DDL for the vector column — SQLAlchemy doesn't natively map it
    op.execute(
        "ALTER TABLE story_atoms ALTER COLUMN embedding TYPE vector(1536) "
        "USING embedding::vector(1536)"
    )

    op.create_table(
        "facts",
        sa.Column(
            "id",
            UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("user_id", sa.String(), nullable=False, unique=True),
        sa.Column(
            "structured_facts", JSONB(), nullable=False, server_default=sa.text("'{}'")
        ),
        sa.Column(
            "significant_people",
            JSONB(),
            nullable=False,
            server_default=sa.text("'[]'"),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("NOW()"),
        ),
    )


def downgrade() -> None:
    op.drop_table("facts")
    op.drop_table("story_atoms")
