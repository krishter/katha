"""
Signed links in a card email (docs/specs/CONVERSATION_CARDS.md §8.3).

One token per card, valid 30 days, good only for that card's small page:
"I didn't know this!", "Ask her something", and turning card emails off.
No login, because the child reads this in an inbox, not the dashboard.

The key is derived from JWT_SECRET but is not it, so a card token can
never be presented as a dashboard session and a session token is never a
card token.
"""

from __future__ import annotations

import hashlib
import hmac
import uuid
from datetime import datetime, timedelta, timezone
from typing import Optional

from jose import JWTError, jwt

from config import settings

LIFETIME = timedelta(days=30)
_AUDIENCE = "katha-card"
_ALGORITHM = "HS256"


def _key() -> str:
    return hmac.new(
        settings.JWT_SECRET.encode("utf-8"), b"katha-card-links", hashlib.sha256
    ).hexdigest()


def make_token(card_id: uuid.UUID, now: Optional[datetime] = None) -> str:
    now = now or datetime.now(timezone.utc)
    return jwt.encode(
        {"card": str(card_id), "aud": _AUDIENCE, "exp": now + LIFETIME},
        _key(),
        algorithm=_ALGORITHM,
    )


def read_token(token: str) -> Optional[uuid.UUID]:
    """The card a token is for, or None if it is forged, for another
    purpose, or expired."""
    try:
        payload = jwt.decode(token, _key(), algorithms=[_ALGORITHM], audience=_AUDIENCE)
        return uuid.UUID(payload["card"])
    except (JWTError, KeyError, ValueError):
        return None


def page_url(token: str, action: str = "") -> str:
    base = f"{settings.PUBLIC_BASE_URL}/c/{token}"
    return f"{base}?a={action}" if action else base


def unsubscribe_url(token: str) -> str:
    return f"{settings.PUBLIC_BASE_URL}/c/{token}/unsubscribe"
