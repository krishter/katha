from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import DateTime, Integer, String, Text, Time, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class UserProfileModel(Base):
    __tablename__ = "user_profiles"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String, nullable=False)
    whatsapp_number: Mapped[str] = mapped_column(String, nullable=False)
    preferred_language: Mapped[str] = mapped_column(
        String, nullable=False, default="hi-IN"
    )
    onboarding_context: Mapped[str] = mapped_column(Text, nullable=False, default="")
    family_whatsapp_number: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    scheduled_time: Mapped[time] = mapped_column(Time, nullable=False)
    timezone: Mapped[str] = mapped_column(
        String, nullable=False, default="Asia/Kolkata"
    )
    # How many times the parent has been asked for consent. Two unclear
    # answers and Katha stops asking — see core.parent_consent. Not derived
    # from consent_records, because an unclear answer writes no record.
    parent_consent_asks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
