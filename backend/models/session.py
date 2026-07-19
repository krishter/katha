import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import Boolean, DateTime, Integer, String, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class Session(Base):
    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    session_number: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    domain: Mapped[str] = mapped_column(String, nullable=False)
    exchange_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    energy_signal: Mapped[str] = mapped_column(String, nullable=False, default="high")
    goal_met: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    session_end_suggested: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scheduled_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    last_user_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    session_open_message_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )
