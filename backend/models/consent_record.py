from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class ConsentRecord(Base):
    __tablename__ = "consent_records"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False)
    email_hash: Mapped[str] = mapped_column(String, nullable=False)
    consent_version: Mapped[str] = mapped_column(String, nullable=False)
    consented_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    ip_address: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    user_agent: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    # Who gave this consent. The buyer consents through a web form; the
    # parent consents by speaking, in a WhatsApp voice note. They are
    # different people with different rights under the DPDP Act, and the
    # parent is the data principal — the one whose voice and life history
    # this is. Defaults to "buyer" because every record written before this
    # column existed came from the onboarding form.
    principal: Mapped[str] = mapped_column(
        String, nullable=False, server_default="buyer"
    )

    # The Turn carrying the spoken agreement, for a parent record. A web
    # form leaves an IP and a user agent; a voice note leaves neither, and
    # this is the equivalent evidence — the audio and transcript of them
    # saying yes. ON DELETE SET NULL so erasing a user's turns cannot
    # cascade into destroying the consent audit trail.
    evidence_ref: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("turns.id", ondelete="SET NULL"),
        nullable=True,
    )
