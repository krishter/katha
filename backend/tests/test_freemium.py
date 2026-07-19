from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from core.freemium import (
    FREE_SESSION_LIMIT,
    is_session_allowed,
    send_upgrade_prompt,
)


def _make_db(plan: str | None, session_count: int):
    db = AsyncMock()

    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = plan
    count_result = MagicMock()
    count_result.scalar_one.return_value = session_count

    db.execute = AsyncMock(side_effect=[plan_result, count_result])
    return db


# ── is_session_allowed ───────────────────────────────────────────────────────


async def test_is_session_allowed_true_when_count_is_zero():
    db = _make_db(plan="free", session_count=0)
    assert await is_session_allowed("user-1", db) is True


async def test_is_session_allowed_true_when_count_is_nine():
    db = _make_db(plan="free", session_count=9)
    assert await is_session_allowed("user-1", db) is True


async def test_is_session_allowed_false_when_count_is_ten():
    db = _make_db(plan="free", session_count=FREE_SESSION_LIMIT)
    assert await is_session_allowed("user-1", db) is False


async def test_is_session_allowed_true_for_premium_plan_regardless_of_count():
    db = AsyncMock()
    plan_result = MagicMock()
    plan_result.scalar_one_or_none.return_value = "premium"
    db.execute = AsyncMock(return_value=plan_result)

    assert await is_session_allowed("user-1", db) is True


# ── send_upgrade_prompt ──────────────────────────────────────────────────────


async def test_send_upgrade_prompt_calls_ses():
    db = AsyncMock()

    account = MagicMock()
    account.email = "dev@katha.life"
    profile = MagicMock()
    profile.name = "Subramaniam"

    account_result = MagicMock()
    account_result.scalar_one_or_none.return_value = account
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = profile

    db.execute = AsyncMock(side_effect=[account_result, profile_result])

    with (
        patch("core.freemium._last_prompt_sent", {}),
        patch("core.freemium.send_email_ses") as mock_send,
    ):
        await send_upgrade_prompt("user-1", db)

    mock_send.assert_called_once()
    call_args = mock_send.call_args.args
    assert call_args[0] == "dev@katha.life"
    assert "Subramaniam" in call_args[1]


async def test_send_upgrade_prompt_suppressed_within_cooldown():
    from datetime import datetime, timezone

    db = AsyncMock()

    with (
        patch(
            "core.freemium._last_prompt_sent",
            {"user-1": datetime.now(timezone.utc)},
        ),
        patch("core.freemium.send_email_ses") as mock_send,
    ):
        await send_upgrade_prompt("user-1", db)

    mock_send.assert_not_called()
    db.execute.assert_not_called()


# ── start_session freemium gate ──────────────────────────────────────────────


async def test_start_session_raises_402_when_limit_reached():
    from core.session_manager import start_session

    db = AsyncMock()

    with (
        patch(
            "core.session_manager.freemium.is_session_allowed",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "core.session_manager.freemium.send_upgrade_prompt", new=AsyncMock()
        ) as mock_prompt,
        pytest.raises(HTTPException) as exc_info,
    ):
        await start_session("user-1", db)

    assert exc_info.value.status_code == 402
    mock_prompt.assert_called_once()
