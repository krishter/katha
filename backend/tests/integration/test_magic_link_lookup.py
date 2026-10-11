"""
Finding a family account by email — against a real database.

The lookup is a SQL expression (lower(email) = :email), so a mocked
session cannot tell whether it matches. What it has to survive is real
data: rows written before normalization existed hold whatever casing the
person typed, and a lookup that misses one is silently indistinguishable
from an address that was never registered, because send_magic_link is
required to report success either way.
"""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, patch

import pytest

from core import auth
from models.family_account import FamilyAccount

pytestmark = pytest.mark.asyncio


async def _account(db, email: str) -> FamilyAccount:
    account = FamilyAccount(
        email=email,
        user_id=f"user_{uuid.uuid4().hex}",
        plan="free",
        onboarding_complete=False,
    )
    db.add(account)
    await db.commit()
    return account


async def test_finds_an_account_stored_with_different_casing(real_db):
    """The row predates normalization; the person types their address the
    way they always do."""
    await _account(real_db, "Krishnaraj@Hotmail.com")

    found = await auth.find_account_by_email("krishnaraj@hotmail.com", real_db)

    assert found is not None
    assert found.email == "Krishnaraj@Hotmail.com"


async def test_finds_an_account_when_the_input_is_the_odd_one(real_db):
    await _account(real_db, "krishnaraj@hotmail.com")

    found = await auth.find_account_by_email("  KRISHNARAJ@Hotmail.com ", real_db)

    assert found is not None


async def test_unknown_address_is_still_not_found(real_db):
    await _account(real_db, "someone@katha.life")

    assert await auth.find_account_by_email("nobody@katha.life", real_db) is None


async def test_send_magic_link_reaches_a_differently_cased_account(real_db):
    """End to end over the real lookup: the mail goes to the address as
    stored, and the rate-limit stamp lands on the account row."""
    account = await _account(real_db, "Krishnaraj@Hotmail.com")

    with patch("core.auth.send_email_ses", new=AsyncMock()) as mock_send:
        sent = await auth.send_magic_link("krishnaraj@hotmail.com", real_db)

    assert sent is True
    assert mock_send.await_args.args[0] == "Krishnaraj@Hotmail.com"
    await real_db.refresh(account)
    assert account.magic_link_last_requested_at is not None
