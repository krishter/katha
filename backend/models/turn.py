from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class Turn(Base):
    __tablename__ = "turns"

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
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
