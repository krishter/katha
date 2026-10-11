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
    # Set only when Twilio reports the message actually reached the
    # handset. It used to be stamped the moment Twilio ACCEPTED the send,
    # which is a different thing entirely: WhatsApp messages outside the
    # 24-hour window are accepted, assigned a SID, and then rejected by
    # Meta asynchronously. Every memory card was recorded as delivered
    # while none of them arrived.
    delivered_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Twilio's last word on this message: queued / sent / delivered /
    # read / undelivered / failed. Nullable for rows written before
    # delivery was tracked at all.
    delivery_status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # Twilio/Meta error code when it failed. 63016 means "outside the
    # 24-hour window and not a template", which is the one this column
    # was added to make visible.
    delivery_error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    twilio_message_sid: Mapped[Optional[str]] = mapped_column(
        String, nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
