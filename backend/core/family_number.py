"""
Proving the family's WhatsApp number belongs to the family (P3, D3).

The child types their own number into the setup form, and memory cards,
with her words on them, go to whatever was typed. A slip of one digit
sends a stranger an elderly woman's life story. So nothing of hers goes
to that number on WhatsApp until the child sends Katha a code from it:
the done screen (and the dashboard, until it is done) carries a wa.me
link to Katha's number prefilled with "CONFIRM <code>". Tapping it from
the right phone is the proof. Email is unaffected; the address was
already proven by the magic link.

The code is stored hashed, one live code per family, for seven days.
"""

from __future__ import annotations

import hashlib
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Optional
from urllib.parse import quote

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel

CODE_LENGTH = 6
CODE_TTL = timedelta(days=7)
# No 0/O or 1/I: the child may read it aloud or retype it.
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
_CONFIRM_RE = re.compile(r"^\s*CONFIRM\s+([A-Z0-9]{6})\s*[.!]?\s*$", re.IGNORECASE)

CONFIRMED_TEXT = (
    "Thank you, this number is confirmed. Katha will send memory cards "
    "here on WhatsApp from now on."
)
NOT_CONFIRMED_TEXT = (
    "That code did not match this number, or it has expired. Please open "
    "the confirm link on your Katha dashboard again, on this phone."
)


def _hash(code: str) -> str:
    return hashlib.sha256(code.upper().encode()).hexdigest()


def issue_code(account: FamilyAccount, now: Optional[datetime] = None) -> str:
    """A fresh code for this family, replacing any earlier one. The caller
    commits."""
    now = now or datetime.now(timezone.utc)
    code = "".join(secrets.choice(_ALPHABET) for _ in range(CODE_LENGTH))
    account.family_confirm_code_hash = _hash(code)
    account.family_confirm_code_expires_at = now + CODE_TTL
    return code


def confirm_link(code: str) -> str:
    """wa.me link to Katha's number, prefilled with the code."""
    sender = settings.TWILIO_WHATSAPP_NUMBER.replace("whatsapp:", "").lstrip("+")
    if not sender:
        return ""
    return f"https://wa.me/{sender}?text={quote(f'CONFIRM {code}')}"


def parse(body: str) -> Optional[str]:
    """The code in a "CONFIRM <code>" message, or None."""
    match = _CONFIRM_RE.match(body or "")
    return match.group(1).upper() if match else None


def is_confirmed(profile) -> bool:
    return getattr(profile, "family_number_confirmed_at", None) is not None


async def confirm(
    from_number: str,
    code: str,
    db: AsyncSession,
    now: Optional[datetime] = None,
) -> Optional[UserProfileModel]:
    """
    Mark the family number confirmed if `code` is the live code of a
    family whose number is `from_number`. Returns the parent's profile, or
    None. The code is used up either way it matches, so it cannot be
    replayed.
    """
    now = now or datetime.now(timezone.utc)
    rows = await db.execute(
        select(UserProfileModel, FamilyAccount)
        .join(FamilyAccount, FamilyAccount.user_id == UserProfileModel.user_id)
        .where(UserProfileModel.family_whatsapp_number == from_number)
        .where(FamilyAccount.family_confirm_code_hash.is_not(None))
    )
    presented = _hash(code)
    for profile, account in rows.all():
        if not secrets.compare_digest(account.family_confirm_code_hash, presented):
            continue
        if (
            account.family_confirm_code_expires_at is None
            or account.family_confirm_code_expires_at < now
        ):
            return None
        profile.family_number_confirmed_at = now
        account.family_confirm_code_hash = None
        account.family_confirm_code_expires_at = None
        await db.commit()
        return profile

    # Twilio retries a webhook it thinks failed, and the code is used up
    # by then. A number already confirmed is told so, not told it failed.
    confirmed = await db.execute(
        select(UserProfileModel)
        .where(UserProfileModel.family_whatsapp_number == from_number)
        .where(UserProfileModel.family_number_confirmed_at.is_not(None))
    )
    return confirmed.scalars().first()
