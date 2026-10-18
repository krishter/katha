from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import CheckConstraint, DateTime, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base

CHANNELS = ("whatsapp", "email")
KINDS = ("question", "reaction")
SCREENINGS = ("ok", "sensitive", "correction", "reaction", "other")
STATUSES = (
    "pending",
    "held",
    "released",
    "dropped",
    "asked",
    "expired",
    "not_relayed",
)


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class FamilyQuestion(Base):
    """
    Something the child sent back about a card: a question for her, or a
    reaction (docs/specs/CONVERSATION_CARDS.md §10).

    A question is raised in her next session, credited to the child by
    name. Her answer is never sent straight back: it becomes ordinary
    conversation, and only reaches the child as a later card she approves.
    """

    __tablename__ = "family_questions"
    __table_args__ = (
        CheckConstraint(_in("channel", CHANNELS), name="ck_family_questions_channel"),
        CheckConstraint(_in("kind", KINDS), name="ck_family_questions_kind"),
        CheckConstraint(
            "screening IS NULL OR " + _in("screening", SCREENINGS),
            name="ck_family_questions_screening",
        ),
        CheckConstraint(_in("status", STATUSES), name="ck_family_questions_status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    card_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    channel: Mapped[str] = mapped_column(String, nullable=False)
    kind: Mapped[str] = mapped_column(String, nullable=False, default="question")
    raw_text: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    # A voice-note question, as transcribed. Empty for typed ones.
    transcript: Mapped[str] = mapped_column(Text, nullable=False, server_default="")
    screening: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[str] = mapped_column(String, nullable=False, default="pending")
    asked_in_session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    asked_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    @property
    def text(self) -> str:
        """What the child asked, whichever way they asked it."""
        return (self.transcript or self.raw_text or "").strip()
