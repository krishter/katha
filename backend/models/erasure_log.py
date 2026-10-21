from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class ErasureLog(Base):
    """
    One row per erasure (core/erasure.py; PRE_PILOT_FIX_PLAN P4). The proof
    that an erasure ran, by whom and with what result, kept after everything
    it describes is gone.

    Holds nothing personal: the user id only as a SHA-256 hash, so the row
    can be matched to a known id on request but names no one by itself.
    """

    __tablename__ = "erasure_log"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    erased_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    user_id_hash: Mapped[str] = mapped_column(String, nullable=False, index=True)
    # "family" (the dashboard), "operator:<name>" (scripts/erase_user.py) or
    # "scheduler:withdrawal-hold".
    actor: Mapped[str] = mapped_column(String, nullable=False)
    # {"turns": 12, "s3_objects": 9, ...}: rows and objects removed.
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False, server_default="{}")
    # ["s3:cards/….png", "table:facts"]: what could not be removed.
    failures: Mapped[list] = mapped_column(JSONB, nullable=False, server_default="[]")
