"""Stories and threads

Revision ID: d2a7f4c9e1b6
Revises: b5e1f7c2a9d4

docs/specs/STORIES.md Stage 1: the stories and story_versions tables, each
atom's story_id, and the per-family stories_enabled switch (off).
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "d2a7f4c9e1b6"
down_revision = "b5e1f7c2a9d4"
branch_labels = None
depends_on = None


def _in(column, values):
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


def upgrade() -> None:
    uuid = postgresql.UUID(as_uuid=True)
    jsonb = postgresql.JSONB()
    now = sa.text("now()")
    op.create_table(
        "stories",
        sa.Column("id", uuid, primary_key=True),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=now, nullable=False
        ),
        sa.Column(
            "updated_at", sa.DateTime(timezone=True), server_default=now, nullable=False
        ),
        sa.Column("promoted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(), nullable=False),
        sa.Column("retired_reason", sa.String(), nullable=True),
        sa.Column("review_status", sa.String(), nullable=True),
        sa.Column("approval_status", sa.String(), nullable=True),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("domain", sa.String(), server_default="", nullable=False),
        sa.Column("when_approx", sa.Text(), server_default="", nullable=False),
        sa.Column("sort_year", sa.Integer(), nullable=True),
        sa.Column("arc", jsonb, server_default="{}", nullable=False),
        sa.Column(
            "people", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "places", postgresql.ARRAY(sa.Text()), server_default="{}", nullable=False
        ),
        sa.Column(
            "sessions_seen",
            postgresql.ARRAY(sa.Integer()),
            server_default="{}",
            nullable=False,
        ),
        sa.Column("last_seen_session", sa.Integer(), nullable=True),
        sa.Column("body_first_person", sa.Text(), nullable=True),
        sa.Column("source_language", sa.String(), nullable=True),
        sa.Column("quotes", jsonb, server_default="[]", nullable=False),
        sa.Column("sentence_sources", jsonb, server_default="[]", nullable=False),
        sa.Column("removed_sentences", jsonb, server_default="[]", nullable=False),
        sa.Column("fidelity_status", sa.String(), nullable=True),
        sa.Column("version", sa.Integer(), server_default="1", nullable=False),
        sa.Column("approved_version", sa.Integer(), nullable=True),
        sa.Column("readback_text", sa.Text(), server_default="", nullable=False),
        sa.Column("readback_language", sa.String(), nullable=True),
        sa.Column("readback_check", jsonb, server_default="{}", nullable=False),
        sa.Column("readback_hash", sa.String(), nullable=True),
        sa.Column("approved_hash", sa.String(), nullable=True),
        sa.Column("readback_offers", sa.Integer(), server_default="0", nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("operator_reason", sa.Text(), nullable=True),
        sa.Column("operator_reviewed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", jsonb, server_default="[]", nullable=False),
        sa.CheckConstraint(
            _in("status", ("thread", "story", "retired")), name="ck_stories_status"
        ),
        sa.CheckConstraint(
            "review_status IS NULL OR "
            + _in("review_status", ("operator_review", "passed")),
            name="ck_stories_review_status",
        ),
        sa.CheckConstraint(
            "approval_status IS NULL OR "
            + _in("approval_status", ("pending", "approved", "kept_private")),
            name="ck_stories_approval_status",
        ),
        sa.CheckConstraint(
            "retired_reason IS NULL OR "
            + _in(
                "retired_reason", ("merged", "operator", "declined_twice", "fidelity")
            ),
            name="ck_stories_retired_reason",
        ),
        sa.CheckConstraint(
            "status <> 'thread' OR (review_status IS NULL AND approval_status IS NULL)",
            name="ck_stories_thread_unreviewed",
        ),
        sa.CheckConstraint(
            "approval_status IS DISTINCT FROM 'approved' OR COALESCE("
            "review_status = 'passed' AND approved_hash = readback_hash, false)",
            name="ck_stories_parent_approved",
        ),
    )
    op.create_index("ix_stories_user_id", "stories", ["user_id"])

    op.create_table(
        "story_versions",
        sa.Column("id", uuid, primary_key=True),
        sa.Column(
            "story_id",
            uuid,
            sa.ForeignKey("stories.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("user_id", sa.String(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=now, nullable=False
        ),
        sa.Column("title", sa.Text(), server_default="", nullable=False),
        sa.Column("body_first_person", sa.Text(), nullable=True),
        sa.Column("quotes", jsonb, server_default="[]", nullable=False),
        sa.Column("sentence_sources", jsonb, server_default="[]", nullable=False),
        sa.Column("readback_text", sa.Text(), server_default="", nullable=False),
        sa.Column("approval_status", sa.String(), nullable=True),
    )
    op.create_index("ix_story_versions_story_id", "story_versions", ["story_id"])
    op.create_index("ix_story_versions_user_id", "story_versions", ["user_id"])

    op.add_column("story_atoms", sa.Column("story_id", uuid, nullable=True))
    op.create_foreign_key(
        "fk_story_atoms_story_id",
        "story_atoms",
        "stories",
        ["story_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("ix_story_atoms_story_id", "story_atoms", ["story_id"])

    op.add_column(
        "user_profiles",
        sa.Column(
            "stories_enabled", sa.Boolean(), server_default="false", nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_column("user_profiles", "stories_enabled")
    op.drop_index("ix_story_atoms_story_id", table_name="story_atoms")
    op.drop_constraint("fk_story_atoms_story_id", "story_atoms", type_="foreignkey")
    op.drop_column("story_atoms", "story_id")
    op.drop_table("story_versions")
    op.drop_table("stories")
