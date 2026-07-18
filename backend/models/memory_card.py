from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class MemoryCard(Base):
    __tablename__ = "memory_cards"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("sessions.id"),
        nullable=False,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    story_atom_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("story_atoms.id"),
        nullable=True,
    )
    verbatim_quote: Mapped[str] = mapped_column(Text, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    image_s3_key: Mapped[str] = mapped_column(String, nullable=False)
    image_public_url: Mapped[str] = mapped_column(String, nullable=False)
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    twilio_message_sid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
