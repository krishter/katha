from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base

# Every status a card can be in (docs/specs/CONVERSATION_CARDS.md §11).
# Which moves between them are allowed lives in cards/state.py.
STATUSES = (
    "draft",
    "operator_review",
    "rejected",
    "awaiting_parent",
    "correction",
    "approved",
    "kept_private",
    "expired",
    "withdrawn",
    "failed_fidelity",
    "scheduled",
    "sent",
)

# The one answer that lets a card leave for the family (§4.1).
PARENT_APPROVE = "approve"


class ConversationCard(Base):
    """
    One "Did you know?" card: a surprising detail from her life, a few
    lines in her words and one question for the child's next call.

    Nothing here reaches the family unless she said yes to this exact card
    after hearing it read (§4.1). The CHECK constraint holds that in the
    database too, so no code path, present or future, can schedule or send
    a card she has not approved.
    """

    __tablename__ = "conversation_cards"
    __table_args__ = (
        CheckConstraint(
            "status IN (" + ", ".join(f"'{s}'" for s in STATUSES) + ")",
            name="ck_conversation_cards_status",
        ),
        # COALESCE because a CHECK that evaluates to NULL passes: without
        # it, a card with no answer at all (parent_answer NULL) would get
        # through. Every comparison here is forced to a real false.
        CheckConstraint(
            "status NOT IN ('approved', 'scheduled', 'sent') OR COALESCE("
            f"parent_answer = '{PARENT_APPROVE}' "
            "AND approved_hash = readback_hash, false)",
            name="ck_conversation_cards_parent_approved",
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
    status: Mapped[str] = mapped_column(String, nullable=False, default="draft")

    # What it was made from. Atoms each have one source turn; the card is
    # written from the turns' raw transcripts, never the atom summaries.
    source_atom_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    source_turn_ids: Mapped[list[uuid.UUID]] = mapped_column(
        ARRAY(UUID(as_uuid=True)), nullable=False, server_default="{}"
    )
    source_cutoff: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    answers_question_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )

    # The card itself (§7.3), English for MVP.
    title: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    hook: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    excerpt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    original_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    original_quote_gloss: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    call_prompt: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    held_back_note: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    source_language: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # What she hears, in her language. `readback_hash` fingerprints the
    # card as it stands (readback text plus the English she is agreeing
    # to); `approved_hash` is the fingerprint she said yes to. If anything
    # changes after her yes, the two differ and the card cannot be sent.
    readback_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    readback_language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    readback_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approved_hash: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    fidelity_json: Mapped[dict] = mapped_column(
        JSONB, nullable=False, server_default="{}"
    )

    # Operator review (§7.6).
    operator_action: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    operator_edited: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    operator_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    operator_reviewed_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Her side (§6).
    readback_offers: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    correction_round: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    parent_answer: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    parent_answer_turn_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    parent_answered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Delivery (§8).
    scheduled_for: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    image_s3_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wa_message_sid: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    wa_delivery_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wa_error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    wa_delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    wa_skipped_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email_message_id: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    email_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # The child's side (§8.1, §8.3).
    child_reaction: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    child_reaction_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    child_reaction_channel: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Tagged by hand on the admin page during the pilot (§13): she brought
    # up, unprompted, a call with her child about this card.
    call_tag: Mapped[Optional[str]] = mapped_column(String, nullable=True)
