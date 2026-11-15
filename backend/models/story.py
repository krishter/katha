from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base

# docs/specs/STORIES.md §4. A Thread is a row with status "thread"; promotion
# to a Story is a status change, so the incident keeps one id from the first
# telling to the last.
STATUSES = ("thread", "story", "retired")
# The operator's side (§2.2). Null while a thread.
REVIEW_STATUSES = ("operator_review", "passed")
# Her side (§2.3). Null while a thread.
APPROVAL_STATUSES = ("pending", "approved", "kept_private")

RETIRED_REASONS = ("merged", "operator", "declined_twice", "fidelity")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class Story(Base):
    """
    One incident she has told, part-told (a thread) or complete (a Story).

    A Story reaches the family only after she heard its read-back and said
    yes to that exact text. The CHECK constraint holds that in the database,
    as conversation_cards does, so no code path can show the family a Story
    she has not approved.
    """

    __tablename__ = "stories"
    __table_args__ = (
        CheckConstraint(_in("status", STATUSES), name="ck_stories_status"),
        CheckConstraint(
            "review_status IS NULL OR " + _in("review_status", REVIEW_STATUSES),
            name="ck_stories_review_status",
        ),
        CheckConstraint(
            "approval_status IS NULL OR " + _in("approval_status", APPROVAL_STATUSES),
            name="ck_stories_approval_status",
        ),
        CheckConstraint(
            "retired_reason IS NULL OR " + _in("retired_reason", RETIRED_REASONS),
            name="ck_stories_retired_reason",
        ),
        # A thread has nothing to review or approve yet.
        CheckConstraint(
            "status <> 'thread' OR (review_status IS NULL AND approval_status IS NULL)",
            name="ck_stories_thread_unreviewed",
        ),
        # COALESCE because a CHECK that evaluates to NULL passes: without it a
        # Story with no fingerprint at all would get through.
        CheckConstraint(
            "approval_status IS DISTINCT FROM 'approved' OR COALESCE("
            "review_status = 'passed' AND approved_hash = readback_hash, false)",
            name="ck_stories_parent_approved",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
    promoted_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    status: Mapped[str] = mapped_column(String, nullable=False, default="thread")
    retired_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    review_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approval_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # What the consolidation pass knows (consolidation/pipeline.Thread).
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    domain: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    when_approx: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    sort_year: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    arc: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    people: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    places: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    sessions_seen: Mapped[list[int]] = mapped_column(
        ARRAY(Integer), nullable=False, server_default="{}"
    )
    last_seen_session: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # The Story itself, English for MVP (proposal §5.2).
    body_first_person: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source_language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    quotes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    sentence_sources: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    removed_sentences: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    fidelity_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, server_default="1")
    approved_version: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    # What she hears, in her language. `readback_hash` fingerprints the body
    # and read-back as they stand; `approved_hash` is the one she said yes
    # to. Change either after her yes and the two differ.
    readback_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    readback_language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    readback_check: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )
    readback_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approved_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    readback_offers: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    approved_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    operator_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    operator_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    notes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")


class StoryVersion(Base):
    """A Story's earlier body and read-back, kept when a correction makes a
    new version (proposal §6.4). The family sees `approved_version`."""

    __tablename__ = "story_versions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    story_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("stories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    body_first_person: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    quotes: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    sentence_sources: Mapped[list] = mapped_column(
        JSONB, nullable=False, server_default="[]"
    )
    readback_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    approval_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
