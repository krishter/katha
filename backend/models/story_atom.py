from __future__ import annotations

import uuid
from datetime import datetime
from typing import List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class StoryAtom(Base):
    __tablename__ = "story_atoms"

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
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    title: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    narrative: Mapped[str] = mapped_column(Text, nullable=False)
    who: Mapped[List[str]] = mapped_column(ARRAY(Text), nullable=False, default=list)
    what: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    when_approx: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    where_approx: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    why: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    completeness_score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    verbatim_quote: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    open_threads: Mapped[List[str]] = mapped_column(
        ARRAY(Text), nullable=False, default=list
    )
    audio_timestamp_start: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    audio_timestamp_end: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    embedding: Mapped[Optional[List[float]]] = mapped_column(
        Vector(1536), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
