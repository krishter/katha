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
    status: Mapped[str] = mapped_column(String, nullable=False, default="active")
    ended_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    ended_reason: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    whatsapp_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scheduled_time: Mapped[Optional[time]] = mapped_column(Time, nullable=True)
    last_user_message_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    session_open_message_id: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    # The opening voice note has no Turn row to hang its audio key on, so
    # without this column the object is private but unenumerable, and
    # DELETE /user/{user_id} cannot reach it. Mirrors
    # turns.response_audio_s3_key.
    session_open_audio_s3_key: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    # When the 30-minute no-reply nudge was sent, so it is sent once rather
    # than once per scheduler tick. Without this the follow-up query stayed
    # true after sending and re-matched every minute: 423 messages in 72
    # hours on 2026-10-02..04, ~210/day until the 4-hour stale sweep closed
    # the session. Nullable: NULL means "not yet nudged".
    followup_sent_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
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
