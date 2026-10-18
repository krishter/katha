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


def _make_session_state(
    session_id: str | None = None, domain: str = "childhood"
) -> MagicMock:
    s = MagicMock()
    s.session_id = session_id or str(uuid.uuid4())
    s.user_id = "user-test"
    s.domain = domain
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


@pytest.fixture(autouse=True)
def opener_on(request, monkeypatch):
    """
    Most tests here exercise the daily opener, which is now the rollback
    path (SCHEDULED_OPENER_ENABLED=true). Tests of the default, where the
    parent starts every conversation, are marked `opener_off`.
    """
    enabled = request.node.get_closest_marker("opener_off") is None
    monkeypatch.setattr(
        "scheduler.session_initiator.settings.SCHEDULED_OPENER_ENABLED", enabled
    )
    # The opener converts WAV to OGG before sending; ffmpeg is not the point
    # of these tests.
    monkeypatch.setattr(
        "scheduler.session_initiator.convert_wav_to_ogg",
        AsyncMock(side_effect=lambda wav: b"OGG:" + wav),
    )


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
    stub_adapter.send_voice_note = AsyncMock(
        return_value=("STUB_MSG_001", "audio/stub-test.ogg")
    )
    stub_adapter.send_text = AsyncMock(return_value="STUB_MSG_002")

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=0),
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
            "scheduler.session_initiator.freemium.is_session_allowed",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "scheduler.session_initiator.parent_consent.has_granted",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "scheduler.session_initiator.tts.synthesize",
            new=AsyncMock(return_value=b"fake-audio"),
        ),
    ):
        await initiate_sessions(factory)

    # Converted to OGG before sending: the raw WAV used to go out labelled
    # audio/ogg, which WhatsApp cannot play.
    stub_adapter.send_voice_note.assert_called_once_with(
        profile.whatsapp_number, b"OGG:fake-audio", mime_type="audio/ogg"
    )


@pytest.mark.asyncio
async def test_initiate_sessions_uses_session_domain_not_hardcoded_childhood():
    """The opening voice note must reflect whatever domain start_session
    actually chose, not a hardcoded 'childhood' default."""
    from prompts.domains import get_domain
    from scheduler.session_initiator import initiate_sessions

    now_ist_hour = datetime.now(timezone.utc).astimezone(
        __import__("pytz").timezone("Asia/Kolkata")
    )
    profile = _make_profile(now_ist_hour.hour, now_ist_hour.minute)
    state = _make_session_state(domain="career")
    factory, mock_db = _make_db_factory(profile=profile)

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock(
        return_value=("STUB_MSG_001", "audio/stub-test.ogg")
    )
    stub_adapter.send_text = AsyncMock(return_value="STUB_MSG_002")

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=0),
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
            "scheduler.session_initiator.freemium.is_session_allowed",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "scheduler.session_initiator.parent_consent.has_granted",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "scheduler.session_initiator.tts.synthesize",
            new=AsyncMock(return_value=b"fake-audio"),
        ) as mock_synth,
    ):
        await initiate_sessions(factory)

    opening_text = mock_synth.call_args.args[0]
    assert get_domain("career").entry_prompt in opening_text


@pytest.mark.asyncio
async def test_initiate_sessions_sweeps_stale_sessions_first():
    from scheduler.session_initiator import initiate_sessions

    factory, _ = _make_db_factory(profile=None)

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock()
    stub_adapter.send_text = AsyncMock()

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=1),
        ) as mock_abandon,
    ):
        await initiate_sessions(factory)

    mock_abandon.assert_called_once()


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
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=0),
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

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=0),
        ),
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
        followup_rows=[(MagicMock(user_id="user-test"), _make_profile(8, 0))],
    )

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock()
    stub_adapter.send_text = AsyncMock(return_value="STUB_MSG_003")

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=0),
        ),
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


async def test_scheduler_does_not_initiate_without_parent_consent():
    """S3.2: the parent is the data principal, and her child's tick-box is
    not her consent (F-02). Until she has agreed, nothing is scheduled —
    which also matches what Meta permits, since Katha cannot open the
    conversation anyway (63049)."""
    from scheduler.session_initiator import initiate_sessions

    now_ist = datetime.now(timezone.utc).astimezone(
        __import__("pytz").timezone("Asia/Kolkata")
    )
    profile = _make_profile(now_ist.hour, now_ist.minute)
    factory, _ = _make_db_factory(profile=profile)

    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock(
        return_value=("STUB_MSG_001", "audio/stub-test.ogg")
    )
    stub_adapter.send_text = AsyncMock(return_value="STUB_MSG_002")

    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=0),
        ),
        patch(
            "scheduler.session_initiator.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "scheduler.session_initiator.session_manager.start_session",
            new=AsyncMock(),
        ) as mock_start,
        patch(
            "scheduler.session_initiator.parent_consent.has_granted",
            new=AsyncMock(return_value=False),
        ),
        patch(
            "scheduler.session_initiator.tts.synthesize",
            new=AsyncMock(return_value=b"fake-audio"),
        ),
    ):
        await initiate_sessions(factory)

    mock_start.assert_not_called()
    stub_adapter.send_voice_note.assert_not_called()


# ── the parent starts every conversation (COST_TO_SERVE.md C5) ────────────────


def _everything_patched(stack, stub_adapter, abandoned):

    mocks = {
        "abandon": AsyncMock(return_value=abandoned),
        "start": AsyncMock(),
        "tts": AsyncMock(return_value=b"wav"),
        "process": AsyncMock(return_value=len(abandoned)),
    }
    stack.enter_context(
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        )
    )
    stack.enter_context(
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=mocks["abandon"],
        )
    )
    stack.enter_context(
        patch(
            "scheduler.session_initiator.session_manager.start_session",
            new=mocks["start"],
        )
    )
    stack.enter_context(
        patch("scheduler.session_initiator.tts.synthesize", new=mocks["tts"])
    )
    stack.enter_context(
        patch(
            "scheduler.session_initiator.orchestrator.process_abandoned_sessions",
            new=mocks["process"],
        )
    )
    return mocks


@pytest.mark.opener_off
@pytest.mark.asyncio
async def test_by_default_the_scheduler_opens_nothing_and_sends_nothing():
    from contextlib import ExitStack

    from scheduler.session_initiator import initiate_sessions

    now = datetime.now(timezone.utc).astimezone(
        __import__("pytz").timezone("Asia/Kolkata")
    )
    # Someone is due this minute, and someone is unanswered for 30 minutes.
    factory, _ = _make_db_factory(
        profile=_make_profile(now.hour, now.minute),
        followup_rows=[(MagicMock(user_id="user-test"), _make_profile(8, 0))],
    )
    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock()
    stub_adapter.send_text = AsyncMock()

    with ExitStack() as stack:
        mocks = _everything_patched(stack, stub_adapter, abandoned=[])
        await initiate_sessions(factory)

    mocks["abandon"].assert_awaited_once()  # the sweep still runs
    mocks["start"].assert_not_awaited()  # no session started
    mocks["tts"].assert_not_awaited()  # nothing synthesised, nothing billed
    stub_adapter.send_voice_note.assert_not_called()
    stub_adapter.send_text.assert_not_called()  # no nudge either


@pytest.mark.opener_off
@pytest.mark.asyncio
async def test_by_default_abandoned_sessions_still_get_their_outputs():
    from contextlib import ExitStack

    from scheduler.session_initiator import initiate_sessions

    factory, _ = _make_db_factory()
    abandoned = [MagicMock(session_id="s1", user_id="u1", exchange_count=4)]
    with ExitStack() as stack:
        mocks = _everything_patched(stack, MagicMock(), abandoned=abandoned)
        await initiate_sessions(factory)

    mocks["process"].assert_awaited_once()
    assert mocks["process"].await_args.args[0] == abandoned


@pytest.mark.asyncio
async def test_rollback_opener_is_billed_as_tts_opener():
    from scheduler.session_initiator import initiate_sessions

    now = datetime.now(timezone.utc).astimezone(
        __import__("pytz").timezone("Asia/Kolkata")
    )
    factory, _ = _make_db_factory(profile=_make_profile(now.hour, now.minute))
    stub_adapter = MagicMock()
    stub_adapter.send_voice_note = AsyncMock(return_value=("SM1", "audio/k.ogg"))
    tts = AsyncMock(return_value=b"wav")
    with (
        patch(
            "scheduler.session_initiator.get_whatsapp_adapter",
            return_value=stub_adapter,
        ),
        patch(
            "scheduler.session_initiator.session_manager.abandon_stale_sessions",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "scheduler.session_initiator.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=None),
        ),
        patch(
            "scheduler.session_initiator.session_manager.start_session",
            new=AsyncMock(return_value=_make_session_state()),
        ),
        patch(
            "scheduler.session_initiator.freemium.is_session_allowed",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "scheduler.session_initiator.parent_consent.has_granted",
            new=AsyncMock(return_value=True),
        ),
        patch("scheduler.session_initiator.tts.synthesize", new=tts),
    ):
        await initiate_sessions(factory)

    assert tts.await_args.kwargs["purpose"] == "tts_opener"
