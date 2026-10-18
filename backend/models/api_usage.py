from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Index, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class ApiUsage(Base):
    """
    One row per paid call, across Anthropic, Sarvam and Twilio
    (docs/specs/COST_TO_SERVE.md §5).

    Counts only. No text and no phone numbers ever land here: `units` holds
    token, second, character and message counts, and the identifying
    columns are internal ids and Twilio message SIDs. Costs are not stored —
    they are computed at read time from units and core/pricing.py, so a
    price change needs no backfill.

    `session_id` deliberately has no foreign key. DPDP deletion removes these
    rows by `user_id`, and a ledger row must never block, or be cascaded by,
    a session delete happening in some other order.
    """

    __tablename__ = "api_usage"
    __table_args__ = (Index("ix_api_usage_message_sid", "message_sid"),)

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), index=True
    )
    user_id: Mapped[Optional[str]] = mapped_column(String, nullable=True, index=True)
    session_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    turn_id: Mapped[Optional[uuid.UUID]] = mapped_column(
        UUID(as_uuid=True), nullable=True
    )
    vendor: Mapped[str] = mapped_column(String, nullable=False)
    operation: Mapped[str] = mapped_column(String, nullable=False)
    model: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    units: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    message_sid: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    status: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    error_code: Mapped[Optional[str]] = mapped_column(String, nullable=True)
