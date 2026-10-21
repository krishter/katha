from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class Turn(Base):
    __tablename__ = "turns"
    __table_args__ = (
        CheckConstraint(
            "extraction_status IN ('pending', 'done', 'failed')",
            name="ck_turns_extraction_status",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id", ondelete="CASCADE"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    turn_number: Mapped[int] = mapped_column(Integer, nullable=False)
    inbound_message_sid: Mapped[Optional[str]] = mapped_column(
        String, unique=True, nullable=True
    )
    transcript: Mapped[str] = mapped_column(Text, nullable=False)
    detected_language: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    response_text: Mapped[str] = mapped_column(Text, nullable=False)
    extraction_json: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    input_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    output_tokens: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    response_audio_s3_key: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # She asked for this to stay between her and Katha
    # (CONVERSATION_CARDS.md §5.2). A private turn, and every atom drawn
    # from it, never reaches a card or anything the family reads.
    is_private: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    private_topic: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Whether this turn's extraction, and with it the check for "keep this
    # between us", has run (P5): pending | done | failed. Its atoms reach
    # the family only once it is done and the turn is not private, so a
    # request for privacy can never lose a race with the dashboard.
    extraction_status: Mapped[str] = mapped_column(
        String, nullable=False, server_default="pending"
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
