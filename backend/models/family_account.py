from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import Boolean, DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class FamilyAccount(Base):
    __tablename__ = "family_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    email: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    plan: Mapped[str] = mapped_column(String, nullable=False, default="free")
    upgraded_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    onboarding_complete: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    magic_link_last_requested_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    # Conversation cards (docs/specs/CONVERSATION_CARDS.md §8.2, §11).
    # What the child is called and what they call her ("Priya", "Amma"):
    # the card credits questions to the child by name and speaks of her as
    # the child does.
    child_name: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    parent_title: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # The child's own timezone and usual call times, [{"dow": 0-6 (Monday
    # = 0), "time": "HH:MM"}]: a card is sent a few hours before a call.
    # NRI children are not in the parent's timezone.
    timezone: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    call_slots: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
    card_email_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )
    # The code the child sends from their own WhatsApp to prove the family
    # number is theirs (core/family_number.py). Hashed; one live code at a
    # time.
    family_confirm_code_hash: Mapped[Optional[str]] = mapped_column(
        String, nullable=True
    )
    family_confirm_code_expires_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
