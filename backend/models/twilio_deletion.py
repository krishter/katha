from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class TwilioDeletion(Base):
    """
    A message, and its media, that Katha still has to delete from Twilio
    (PRE_PILOT_FIX_PLAN P6, D8; core/twilio_cleanup.py). Twilio keeps every
    voice note she sends, and every one Katha sends her, until told
    otherwise; that copy sat outside every erasure. Each message is queued
    when it is received or sent, deleted as soon as it is safe to, and
    retried by the scheduler until it is gone. The row is removed when the
    delete succeeds.

    user_id becomes "DELETED" when her data is erased and a Twilio delete
    is still outstanding, so the retry can go on without naming her.
    """

    __tablename__ = "twilio_deletions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    message_sid: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    direction: Mapped[str] = mapped_column(String, nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default="0")
    last_error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
