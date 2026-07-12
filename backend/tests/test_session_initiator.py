from __future__ import annotations

import uuid
from datetime import datetime, time, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

# ── helpers ────────────────────────────────────────────────────────────────────


def _make_profile(hour: int, minute: int, number: str = "+919876543210") -> MagicMock:
    p = MagicMock()
    p.user_id = "user-test"
    p.name = "Subramaniam"
    p.whatsapp_number = number
    p.preferred_language = "ta-IN"
    p.scheduled_time = time(hour, minute)
    return p


def _make_session_state(session_id: str | None = None) -> MagicMock:
    s = MagicMock()
    s.session_id = session_id or str(uuid.uuid4())
    s.user_id = "user-test"
    s.domain = "childhood"
    return s


def _make_db_factory(profile=None, active_session=None, followup_rows=None):
    """Return an async context-manager factory that yields a mock db session."""
    mock_db = AsyncMock()

    # Scalar result for user_profiles query
    profiles = [profile] if profile else []
    profile_result = MagicMock()
    profile_result.scalars.return_value.all.return_value = profiles

    # Follow-up rows (Session, UserProfile tuples)
    followup_result = MagicMock()
    followup_result.all.return_value = followup_rows or []

    # Route execute calls by call count
    call_count = 0

    async def fake_execute(stmt, *args, **kwargs):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return profile_result
        return followup_result

    mock_db.execute = fake_execute
    mock_db.commit = AsyncMock()

    # Async context manager
    factory = MagicMock()
    factory.return_value.__aenter__ = AsyncMock(return_value=mock_db)
    factory.return_value.__aexit__ = AsyncMock(return_value=False)
    return factory, mock_db


# ── tests ──────────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initiate_sessions_sends_voice_note_to_scheduled_user():
    from scheduler.session_initiator import initiate_sessions

    now_ist_hour = datetime.now(timezone.utc).astimezone(
        __import__("pytz").timezone("Asia/Kolkata")
    )
    profile = _make_profile(now_ist_hour.hour, now_ist_hour.minute)
    state = _make_session_state()
    factory, mock_db = _make_db_factory(profile=profile)

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock(return_value="STUB_MSG_001")
    stub_adapter.send_text = AsyncMock(return_value="STUB_MSG_002")

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "scheduler.session_initiator.session_manager.start_session",
            new=AsyncMock(return_value=state),
        ),
        patch(
            "scheduler.session_initiator.sarvam_tts.synthesize",
            new=AsyncMock(return_value=b"fake-audio"),
        ),
    ):
        await initiate_sessions(factory)

    stub_adapter.send_voice_note.assert_called_once_with(
        profile.whatsapp_number, b"fake-audio", mime_type="audio/ogg"
    )


@pytest.mark.asyncio
async def test_initiate_sessions_skips_user_with_active_session():
    from scheduler.session_initiator import initiate_sessions

    now_ist = datetime.now(timezone.utc).astimezone(
        __import__("pytz").timezone("Asia/Kolkata")
    )
    profile = _make_profile(now_ist.hour, now_ist.minute)
    existing = _make_session_state()
    factory, _ = _make_db_factory(profile=profile)

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock()

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=existing),
        ),
    ):
        await initiate_sessions(factory)

    stub_adapter.send_voice_note.assert_not_called()


@pytest.mark.asyncio
async def test_initiate_sessions_no_users_due():
    from scheduler.session_initiator import initiate_sessions

    factory, _ = _make_db_factory(profile=None)

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock()
    stub_adapter.send_text = AsyncMock()

    with patch(
        "scheduler.session_initiator.get_whatsapp_adapter",
        return_value=stub_adapter,
    ):
        await initiate_sessions(factory)

    stub_adapter.send_voice_note.assert_not_called()
    stub_adapter.send_text.assert_not_called()


@pytest.mark.asyncio
async def test_followup_sent_for_non_responsive_session():
    from scheduler.session_initiator import initiate_sessions

    # No users due this minute
    factory, _ = _make_db_factory(
        profile=None,
        followup_rows=[
            (MagicMock(user_id="user-test"), _make_profile(8, 0))
        ],
    )

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock()
    stub_adapter.send_text = AsyncMock(return_value="STUB_MSG_003")

    with patch(
        "scheduler.session_initiator.get_whatsapp_adapter",
        return_value=stub_adapter,
    ):
        await initiate_sessions(factory)

    stub_adapter.send_text.assert_called_once()
    text_sent = stub_adapter.send_text.call_args.args[1]
    assert "no pressure" in text_sent.lower()


def test_create_scheduler_returns_scheduler():
    from scheduler.session_initiator import create_scheduler

    factory = MagicMock()
    scheduler = create_scheduler(factory)
    assert scheduler is not None
    # Verify the job was registered
    jobs = scheduler.get_jobs()
    assert any(j.id == "initiate_sessions" for j in jobs)
