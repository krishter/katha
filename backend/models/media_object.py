from __future__ import annotations

import uuid
from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from models.db import Base


class MediaObject(Base):
    """
    Every object Katha puts in the bucket for a known user
    (PRE_PILOT_FIX_PLAN P6, M-08), written by media/storage.upload_media
    from the usage-ledger scope. The typed key columns (turn audio, card
    images, the session opener) only ever covered the paths someone
    remembered to record; the consent welcome and other system voice notes
    were uploaded and never found again. Erasure deletes everything listed
    here as well.
    """

    __tablename__ = "media_objects"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    user_id: Mapped[str] = mapped_column(String, nullable=False, index=True)
    s3_key: Mapped[str] = mapped_column(String, nullable=False)
    # The key's first path segment: "audio", "cards", …
    kind: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
