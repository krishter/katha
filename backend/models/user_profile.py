from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Optional

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    String,
    Text,
    Time,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class UserProfileModel(Base):
    __tablename__ = "user_profiles"
    __table_args__ = (
        CheckConstraint(
            "katha_gender IN ('female', 'male')",
            name="ck_user_profiles_katha_gender",
        ),
        CheckConstraint(
            "share_preference IN "
            "('not_asked', 'share_with_approval', 'unsure', 'never')",
            name="ck_user_profiles_share_preference",
        ),
    )

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
    # How the storyteller is referred to, e.g. "she/her". Free text rather
    # than an enum: the wizard offers the three common sets and an "other"
    # box, and a person's own words about themselves should not be forced
    # into a closed list.
    #
    # Empty means NOT STATED, which is different from unknown-and-guessable.
    # Layer 1 is explicit that an empty value means avoid gendered forms
    # rather than infer from the name — guessing from a name is exactly the
    # mistake that misgenders someone, and the name is the one signal that
    # looks reliable and is not.
    pronouns: Mapped[str] = mapped_column(String, nullable=False, server_default="")
    # Katha's persona for this parent, "female" or "male", chosen by the
    # family at setup. With preferred_language it picks the Sarvam model and
    # voice (media/voices.py) and the gendered forms Katha uses for itself.
    katha_gender: Mapped[str] = mapped_column(
        String, nullable=False, server_default="female"
    )
    # The voice she hears, for life: "vendor/model/speaker", e.g.
    # "google/gemini-3.8-flash-lite-tts/Sulafat" (docs/specs/GEMINI_TTS.md
    # §4.2). Set from language and persona when the profile is created, and
    # again at onboarding if the family changes either. Only
    # scripts/set_voice.py changes it after that, recording who did.
    tts_voice: Mapped[str] = mapped_column(
        String, nullable=False, default=lambda context: _default_tts_voice(context)
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

    # When she last messaged Katha. Meta allows free-form messages only
    # within 24 hours of this, so it is what decides whether a send needs an
    # approved template.
    last_inbound_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    # Conversation cards (docs/specs/CONVERSATION_CARDS.md). Cards run for
    # a parent only when CONVERSATION_CARDS_ENABLED is on AND this is true;
    # an operator turns it on one family at a time (scripts/cards.py).
    conversation_cards_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="false"
    )
    # Her own answer to "shall I share some of these with your child?"
    # (§5.1): not_asked | share_with_approval | unsure | never. No card is
    # drafted unless it is share_with_approval.
    share_preference: Mapped[str] = mapped_column(
        String, nullable=False, server_default="not_asked"
    )
    share_preference_asks: Mapped[int] = mapped_column(
        Integer, nullable=False, server_default="0"
    )
    # Topics she asked to keep between herself and Katha (§5.2). Card
    # selection must not pick an atom on any of them.
    private_topics: Mapped[list[str]] = mapped_column(
        ARRAY(Text), nullable=False, server_default="{}"
    )
    # The child replied "stop" on WhatsApp (§10.2): cards then go by email
    # only.
    card_whatsapp_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default="true"
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )


def _default_tts_voice(context) -> str:
    """A new profile's pin, from its language and persona under the settings
    of the day (media/tts.py default_voice)."""
    from media import tts

    params = context.get_current_parameters()
    return tts.default_voice(
        params.get("preferred_language") or "hi-IN",
        params.get("katha_gender") or "female",
    ).pin
