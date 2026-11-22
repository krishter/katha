"""Embedding model tag and user_id index on story_atoms

Revision ID: c7a2e9d4f1b3
Revises: c3e7a9d5f1b8

docs/specs/SEMANTIC_RETRIEVAL.md §5.4-5.5 (D4). `embedding_model` records
which model and size made each vector, and retrieval filters on it so two
embedding spaces never mix. No dimension change: gemini-embedding-001 at
1536 fits the existing vector(1536) column.

Retrieval reads story_atoms by user_id on every session's first turn, and
nothing indexed it. No vector index: an exact scan is right at pilot scale.
"""

import sqlalchemy as sa
from alembic import op

revision = "c7a2e9d4f1b3"
down_revision = "c3e7a9d5f1b8"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("story_atoms", sa.Column("embedding_model", sa.Text(), nullable=True))
    op.create_index("ix_story_atoms_user_id", "story_atoms", ["user_id"])


def downgrade() -> None:
    op.drop_index("ix_story_atoms_user_id", table_name="story_atoms")
    op.drop_column("story_atoms", "embedding_model")
