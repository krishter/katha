from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class AlertSent(Base):
    """
    One row per operator alert sent (core/alerts.py, P9), so a rate-limited
    alert ("turn failures", "backup stale") goes out once per window rather
    than on every occurrence. Holds a key and a time; nothing about anyone.
    """

    __tablename__ = "alerts_sent"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    key: Mapped[str] = mapped_column(String, nullable=False, index=True)
    sent_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


class TurnFailure(Base):
    """
    One row each time a parent got a fallback reply instead of a real one
    (STT, LLM or anything else; P9). Counted to alert the operator when
    something is failing for everyone, or for one family twice running.
    Deleted with her other rows on erasure.
    """

    __tablename__ = "turn_failures"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    stage: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
