from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class VoiceChange(Base):
    """
    One row each time an operator moves a parent to a different voice
    (scripts/set_voice.py; docs/specs/GEMINI_TTS.md §4.2). A parent's voice
    is otherwise fixed at setup, so this is the whole history of it.

    Holds voice identifiers and the operator's name, nothing a parent said.
    Deleted with her other rows by DELETE /user/{user_id}.
    """

    __tablename__ = "voice_changes"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    from_voice: Mapped[str] = mapped_column(String, nullable=False)
    to_voice: Mapped[str] = mapped_column(String, nullable=False)
    changed_by: Mapped[str] = mapped_column(String, nullable=False)
    reason: Mapped[str] = mapped_column(String, nullable=False, server_default="")
