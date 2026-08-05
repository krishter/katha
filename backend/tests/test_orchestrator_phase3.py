import json
import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import BackgroundTasks

from core.orchestrator import build_prior_context, run_post_session
from core.session_manager import SessionState
from prompts.system_prompt import PriorContext

_SESSION_ID = str(uuid.uuid4())
_USER_ID = "user-1"
_DOMAIN = "childhood"

_EXTRACTION_JSON = {
    "story_atoms": [{"narrative": "Test story", "domain": "childhood"}],
    "significant_people": [],
    "themes": ["childhood"],
    "energy_signal": "high",
    "gaps_remaining": [],
    "session_end_suggested": False,
}
_EXTRACTION_LLM_CONTENT = f"<extraction>{json.dumps(_EXTRACTION_JSON)}</extraction>"


def _make_db():
    return AsyncMock()


# ── build_prior_context ────────────────────────────────────────────────────────


async def test_build_prior_context_calls_get_facts():
    db = _make_db()
    with (
        patch(
            "core.orchestrator.fact_store.get_facts",
            new=AsyncMock(return_value={"birth_year": 1948}),
        ) as mock_get_facts,
        patch(
            "core.orchestrator.vector_store.retrieve_relevant",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "core.orchestrator.fact_store.get_significant_people",
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await build_prior_context(_USER_ID, _DOMAIN, db)

    mock_get_facts.assert_called_once_with(_USER_ID, db)
    assert result.facts == {"birth_year": 1948}


async def test_build_prior_context_calls_retrieve_relevant():
    db = _make_db()
    with (
        patch(
            "core.orchestrator.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "core.orchestrator.vector_store.retrieve_relevant",
            new=AsyncMock(return_value=[]),
        ) as mock_retrieve,
        patch(
            "core.orchestrator.fact_store.get_significant_people",
            new=AsyncMock(return_value=[]),
        ),
    ):
        await build_prior_context(_USER_ID, _DOMAIN, db)

    mock_retrieve.assert_called_once_with(_USER_ID, _DOMAIN, top_k=5, db=db)


async def test_build_prior_context_includes_significant_people():
    db = _make_db()
    people = [{"name": "Mr. Iyer", "relationship": "teacher", "resolved": False}]
    with (
        patch(
            "core.orchestrator.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "core.orchestrator.vector_store.retrieve_relevant",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "core.orchestrator.fact_store.get_significant_people",
            new=AsyncMock(return_value=people),
        ),
    ):
        result = await build_prior_context(_USER_ID, _DOMAIN, db)

    assert result.significant_people == people
    assert isinstance(result, PriorContext)


# ── run_post_session ───────────────────────────────────────────────────────────
#
# Story atoms are persisted per-turn now (see process_voice_turn tests below).
# run_post_session's only remaining job is entity extraction over the full,
# concatenated session transcript read back from the turns table.


def _make_transcript_db(transcripts: list[str]) -> AsyncMock:
    db = AsyncMock()
    result = MagicMock()
    result.scalars.return_value.all.return_value = transcripts
    db.execute = AsyncMock(return_value=result)
    return db


async def test_run_post_session_loads_transcript_from_turns_table():
    db = _make_transcript_db(["First turn.", "Second turn."])

    with patch(
        "core.orchestrator.entity_extractor.extract_entities",
        new=AsyncMock(),
    ) as mock_entities:
        await run_post_session(_SESSION_ID, _USER_ID, db)

    mock_entities.assert_called_once_with("First turn.\nSecond turn.", _USER_ID, db)


async def test_run_post_session_does_not_call_process_extraction():
    """Story atoms are already persisted per-turn — run_post_session must not
    re-run extraction over the session."""
    db = _make_transcript_db(["Some transcript."])

    with (
        patch(
            "core.orchestrator.story_extractor.process_extraction",
            new=AsyncMock(),
        ) as mock_extract,
        patch(
            "core.orchestrator.entity_extractor.extract_entities",
            new=AsyncMock(),
        ),
    ):
        await run_post_session(_SESSION_ID, _USER_ID, db)

    mock_extract.assert_not_called()


async def test_run_post_session_does_not_raise_on_exception():
    """Exceptions must be swallowed (logged), not propagated."""
    db = _make_transcript_db(["Some transcript."])

    with patch(
        "core.orchestrator.entity_extractor.extract_entities",
        new=AsyncMock(side_effect=RuntimeError("DB error")),
    ):
        # Should not raise
        await run_post_session(_SESSION_ID, _USER_ID, db)


# ── set_turn_audio_key ─────────────────────────────────────────────────────────


async def test_set_turn_audio_key_updates_the_row():
    from core.orchestrator import set_turn_audio_key

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(id=turn_id, response_audio_s3_key=None)
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = turn_row
    db.execute = AsyncMock(return_value=result)

    await set_turn_audio_key(turn_id, "audio/katha-abc123.ogg", db)

    assert turn_row.response_audio_s3_key == "audio/katha-abc123.ogg"
    db.commit.assert_called_once()


async def test_set_turn_audio_key_no_op_when_turn_not_found():
    from core.orchestrator import set_turn_audio_key

    db = AsyncMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)

    # Should not raise even though the turn doesn't exist.
    await set_turn_audio_key(uuid.uuid4(), "audio/x.ogg", db)
    db.commit.assert_not_called()


# ── process_voice_turn integration ────────────────────────────────────────────


def _make_turn_db() -> AsyncMock:
    """
    A db mock sufficient for _persist_turn's add/commit/refresh sequence.
    db.execute defaults to "no prior turn found" (.first() -> None), which
    is what _load_last_turn_messages expects for a first-in-session turn.
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    no_prior_turn = MagicMock()
    no_prior_turn.first.return_value = None
    db.execute = AsyncMock(return_value=no_prior_turn)
    return db


_FAKE_WAV = b"RIFF" + b"\x00" * 40
_DIALOGUE_ONLY_LLM = "<response>Tell me more about that.</response>"


def _voice_turn_patches(stack: ExitStack, session_state, llm_content) -> None:
    """
    Patch every external dependency of process_voice_turn for the
    successful-turn path. record_turn returns session_state unchanged
    (exchange_count bookkeeping isn't the point of these tests).
    """
    stack.enter_context(
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=session_state),
        )
    )
    stack.enter_context(
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    transcript="I grew up in Madurai near the temple.",
                    language_code="ta-IN",
                    language_probability=0.9,
                )
            ),
        )
    )
    stack.enter_context(
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=100, output_tokens=50
                )
            ),
        )
    )
    stack.enter_context(
        patch(
            "core.orchestrator.sarvam_tts.synthesize",
            new=AsyncMock(return_value=_FAKE_WAV),
        )
    )
    stack.enter_context(
        patch(
            "core.orchestrator.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda audio_bytes: audio_bytes),
        )
    )
    stack.enter_context(
        patch(
            "core.orchestrator.session_manager.record_turn",
            new=AsyncMock(return_value=session_state),
        )
    )
    stack.enter_context(
        patch("core.orchestrator.fact_store.get_facts", new=AsyncMock(return_value={}))
    )
    stack.enter_context(
        patch(
            "core.orchestrator.vector_store.retrieve_relevant",
            new=AsyncMock(return_value=[]),
        )
    )
    stack.enter_context(
        patch(
            "core.orchestrator.fact_store.get_significant_people",
            new=AsyncMock(return_value=[]),
        )
    )


def _make_profile():
    from prompts.system_prompt import UserProfile

    return UserProfile(
        name="Subramaniam", preferred_language="ta-IN", onboarding_context=""
    )


def _make_session_state(**overrides):
    defaults = dict(
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        session_number=1,
        domain="childhood",
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    defaults.update(overrides)
    return SessionState(**defaults)


async def test_process_voice_turn_includes_last_turn_in_dialogue_call():
    """
    Regression guard (WS2.1 eval, TC-08): the dialogue call must see the
    immediately preceding turn's raw exchange directly, not rely solely on
    the deferred extraction pipeline for continuity — a story that unfolds
    over two turns would otherwise lose context if extraction for the
    prior turn hadn't finished by the time this one arrives.
    """
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=1)
    db = _make_turn_db()
    prior_turn_result = MagicMock()
    prior_turn_result.first.return_value = (
        "There was a man who made the best sweets in our street.",
        "That sounds wonderful — what did he sell?",
    )
    db.execute = AsyncMock(return_value=prior_turn_result)

    mock_llm = AsyncMock(
        return_value=SimpleNamespace(
            content=_DIALOGUE_ONLY_LLM, input_tokens=100, output_tokens=50
        )
    )

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        stack.enter_context(patch("core.orchestrator.llm.chat", new=mock_llm))
        await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    sent_messages = mock_llm.call_args.args[0]
    assert sent_messages[0].role == "user"
    assert "best sweets" in sent_messages[0].content
    assert sent_messages[1].role == "assistant"
    assert "what did he sell" in sent_messages[1].content
    # The current turn's transcript is still the last message.
    assert sent_messages[-1].content == "I grew up in Madurai near the temple."


async def test_process_voice_turn_persists_turn_with_placeholder_extraction():
    """
    extraction_json on the persisted Turn starts as the empty placeholder —
    real structured extraction is a separate, deferred call (see
    run_extraction_for_turn tests below), not run synchronously here.
    """
    from core.orchestrator import _EMPTY_EXTRACTION, process_voice_turn
    from models.turn import Turn

    session_state = _make_session_state(exchange_count=1)
    db = _make_turn_db()

    with (
        ExitStack() as stack,
        patch(
            "core.orchestrator.story_extractor.process_extraction", new=AsyncMock()
        ) as mock_extract,
    ):
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        result = await process_voice_turn(
            b"audio", _SESSION_ID, _make_profile(), db, inbound_message_sid="SM123"
        )

    assert result.response_audio == _FAKE_WAV
    assert result.extraction_json == _EMPTY_EXTRACTION

    added_turn = db.add.call_args.args[0]
    assert isinstance(added_turn, Turn)
    assert added_turn.turn_number == session_state.exchange_count
    assert added_turn.inbound_message_sid == "SM123"
    assert added_turn.transcript == "I grew up in Madurai near the temple."
    assert added_turn.extraction_json == _EMPTY_EXTRACTION
    db.commit.assert_called()

    # Structured extraction is NOT run synchronously in process_voice_turn.
    mock_extract.assert_not_called()


async def test_process_voice_turn_degrades_to_text_when_tts_fails():
    """
    A TTS/ffmpeg failure must degrade the channel (send text), not the
    response, and must never lose the turn already committed — the
    opposite of raising past the caller (P2: never silence).
    """
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        stack.enter_context(
            patch(
                "core.orchestrator.sarvam_tts.synthesize",
                new=AsyncMock(side_effect=RuntimeError("Sarvam TTS is down")),
            )
        )
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.response_mime_type == "text/plain"
    assert result.response_text  # the words still went out, just as text

    # The turn was already committed before the TTS call that then failed.
    db.add.assert_called_once()
    db.commit.assert_called_once()


async def test_process_voice_turn_degrades_to_text_when_audio_conversion_fails():
    """Same as the TTS-failure case, but the failure is specifically in the
    WAV->OGG ffmpeg conversion step, not TTS itself — both must degrade the
    same way (send text, never lose the already-committed turn)."""
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        stack.enter_context(
            patch(
                "core.orchestrator.convert_wav_to_ogg",
                new=AsyncMock(side_effect=RuntimeError("ffmpeg conversion failed")),
            )
        )
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.response_mime_type == "text/plain"
    assert result.response_text
    db.add.assert_called_once()
    db.commit.assert_called_once()


async def test_process_voice_turn_defaults_inbound_message_sid_to_none():
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    added_turn = db.add.call_args.args[0]
    assert added_turn.inbound_message_sid is None


async def test_process_voice_turn_schedules_extraction_as_background_task():
    from core.orchestrator import process_voice_turn, run_extraction_for_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()
    bg = BackgroundTasks()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        await process_voice_turn(
            b"audio", _SESSION_ID, _make_profile(), db, background_tasks=bg
        )

    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is run_extraction_for_turn


async def test_process_voice_turn_no_bg_tasks_does_not_schedule_extraction():
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, _DIALOGUE_ONLY_LLM)
        # background_tasks defaults to None — must not raise, must not
        # attempt to schedule anything.
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.response_audio == _FAKE_WAV


# ── process_voice_turn: failure fallbacks (never silence) ─────────────────────


async def test_process_voice_turn_stt_failure_returns_fallback_audio():
    from core.fallback_audio import FailureStage
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=session_state),
        ),
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(side_effect=RuntimeError("Sarvam STT is down")),
        ),
        patch(
            "core.orchestrator.get_fallback_audio",
            return_value=b"pre-synthesized-stt-fallback",
        ) as mock_get_audio,
    ):
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.response_audio == b"pre-synthesized-stt-fallback"
    assert result.response_mime_type == "audio/ogg"
    mock_get_audio.assert_called_once_with(FailureStage.STT, "ta-IN")
    # No turn is persisted for a total STT failure — nothing was said.
    db.add.assert_not_called()


async def test_process_voice_turn_stt_failure_degrades_to_text_if_no_fallback_audio():
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=session_state),
        ),
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(side_effect=RuntimeError("Sarvam STT is down")),
        ),
        patch("core.orchestrator.get_fallback_audio", return_value=None),
    ):
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.response_mime_type == "text/plain"
    assert result.response_text


async def test_process_voice_turn_llm_failure_returns_fallback_audio():
    from core.fallback_audio import FailureStage
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=session_state),
        ),
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    transcript="Tell me about the old days.",
                    language_code="hi-IN",
                    language_probability=0.9,
                )
            ),
        ),
        patch("core.orchestrator.fact_store.get_facts", new=AsyncMock(return_value={})),
        patch(
            "core.orchestrator.vector_store.retrieve_relevant",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "core.orchestrator.fact_store.get_significant_people",
            new=AsyncMock(return_value=[]),
        ),
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(side_effect=RuntimeError("Anthropic API error")),
        ),
        patch(
            "core.orchestrator.get_fallback_audio",
            return_value=b"pre-synthesized-llm-fallback",
        ) as mock_get_audio,
    ):
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.response_audio == b"pre-synthesized-llm-fallback"
    mock_get_audio.assert_called_once_with(FailureStage.LLM, "hi-IN")
    db.add.assert_not_called()


# ── process_voice_turn: crisis check on Katha's own response ──────────────────


async def test_process_voice_turn_overrides_crisis_language_in_own_response():
    from core.orchestrator import process_voice_turn

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()
    bad_llm_output = (
        "<response>Maybe you would be better off dead if things are that hard."
        "</response>"
    )

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, bad_llm_output)
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    from models.turn import Turn

    assert result.crisis_detected is True
    assert "9152987821" in result.response_text
    added_turn = next(
        c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], Turn)
    )
    assert "9152987821" in added_turn.response_text


async def test_process_voice_turn_logs_crisis_event_for_own_response():
    from core.orchestrator import process_voice_turn
    from models.crisis_event import CrisisEvent

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()
    bad_llm_output = "<response>You'd be better off dead.</response>"

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, bad_llm_output)
        await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    crisis_events = [
        c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], CrisisEvent)
    ]
    assert len(crisis_events) == 1
    assert crisis_events[0].source == "assistant_response"


async def test_process_voice_turn_logs_crisis_event_for_user_transcript():
    from core.orchestrator import process_voice_turn
    from models.crisis_event import CrisisEvent

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=session_state),
        ),
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    transcript="I want to end my life",
                    language_code="en-IN",
                    language_probability=0.9,
                )
            ),
        ),
        patch(
            "core.orchestrator.sarvam_tts.synthesize",
            new=AsyncMock(return_value=_FAKE_WAV),
        ),
        patch(
            "core.orchestrator.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda audio_bytes: audio_bytes),
        ),
    ):
        result = await process_voice_turn(b"audio", _SESSION_ID, _make_profile(), db)

    assert result.crisis_detected is True
    crisis_events = [
        c.args[0] for c in db.add.call_args_list if isinstance(c.args[0], CrisisEvent)
    ]
    assert len(crisis_events) == 1
    assert crisis_events[0].source == "user_transcript"
    assert crisis_events[0].turn_id is None  # no turn exists yet at this stage


# ── run_extraction_for_turn ────────────────────────────────────────────────────


def _make_extraction_db(turn_row=None) -> AsyncMock:
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = turn_row
    db.execute = AsyncMock(return_value=result)
    return db


async def test_run_extraction_for_turn_persists_atoms_with_turn_id():
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=100, output_tokens=50
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state()

    llm_content = _EXTRACTION_LLM_CONTENT

    with (
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=300, output_tokens=150
                )
            ),
        ),
        patch(
            "core.orchestrator.story_extractor.process_extraction", new=AsyncMock()
        ) as mock_extract,
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=session_state),
        ),
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "I grew up in Madurai.",
            "Tell me more.",
            db,
        )

    mock_extract.assert_called_once()
    assert mock_extract.call_args.args[0] == _EXTRACTION_JSON
    assert mock_extract.call_args.kwargs["turn_id"] == turn_id
    # The turn row's extraction_json and tokens are updated after extraction.
    assert turn_row.extraction_json == _EXTRACTION_JSON


async def test_run_extraction_for_turn_uses_extraction_max_tokens():
    """The extraction call gets a 2000-token budget — it is off the critical
    path and latency-tolerant, unlike the 300-token dialogue call."""
    from core.orchestrator import _EXTRACTION_MAX_TOKENS, run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state()
    mock_llm = AsyncMock(
        return_value=SimpleNamespace(
            content=_EXTRACTION_LLM_CONTENT, input_tokens=10, output_tokens=5
        )
    )

    with (
        patch("core.orchestrator.llm.chat", new=mock_llm),
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock()),
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=session_state),
        ),
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    assert mock_llm.call_args.kwargs["max_tokens"] == _EXTRACTION_MAX_TOKENS


async def test_long_detailed_story_produces_complete_reply_and_extraction():
    """
    Regression for C5: a long, detailed story must never truncate the
    dialogue reply (max_tokens=300 is sized for the reply alone now, not
    reply+extraction together) or the extraction call (max_tokens=2000,
    a separate call). Neither call shares the other's token budget.
    """
    from core.orchestrator import process_voice_turn, run_extraction_for_turn

    long_story = (
        "I grew up in a small house near the river in Madurai. "
        "My father ran a shop selling brass vessels, and every morning "
        "the street would fill with the smell of filter coffee and jasmine. "
    ) * 20
    assert len(long_story.split()) > 400

    session_state = _make_session_state(exchange_count=0)
    db = _make_turn_db()

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "core.orchestrator.session_manager.get_session",
                new=AsyncMock(return_value=session_state),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.sarvam_stt.transcribe",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        transcript=long_story,
                        language_code="ta-IN",
                        language_probability=0.9,
                    )
                ),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.llm.chat",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        content=_DIALOGUE_ONLY_LLM, input_tokens=500, output_tokens=60
                    )
                ),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.sarvam_tts.synthesize",
                new=AsyncMock(return_value=_FAKE_WAV),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.convert_wav_to_ogg",
                new=AsyncMock(side_effect=lambda audio_bytes: audio_bytes),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.session_manager.record_turn",
                new=AsyncMock(return_value=session_state),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.fact_store.get_facts", new=AsyncMock(return_value={})
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.vector_store.retrieve_relevant",
                new=AsyncMock(return_value=[]),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.fact_store.get_significant_people",
                new=AsyncMock(return_value=[]),
            )
        )
        bg = BackgroundTasks()
        result = await process_voice_turn(
            b"audio", _SESSION_ID, _make_profile(), db, background_tasks=bg
        )

    # The dialogue reply is complete — not rejected as malformed.
    assert result.response_mime_type != "text/plain" or result.response_text
    assert result.response_text == "Tell me more about that."

    # Extraction is scheduled, off the critical path, with its own 2000-
    # token budget — large enough for a detailed story's full extraction.
    assert len(bg.tasks) == 1
    assert bg.tasks[0].func is run_extraction_for_turn


async def test_run_extraction_for_turn_retries_once_on_malformed_response():
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state()

    valid_content = _EXTRACTION_LLM_CONTENT
    mock_llm = AsyncMock(
        side_effect=[
            SimpleNamespace(
                content="not valid at all", input_tokens=10, output_tokens=5
            ),
            SimpleNamespace(content=valid_content, input_tokens=20, output_tokens=10),
        ]
    )

    with (
        patch("core.orchestrator.llm.chat", new=mock_llm),
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock()),
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=session_state),
        ),
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    assert mock_llm.call_count == 2
    assert turn_row.extraction_json == _EXTRACTION_JSON


async def test_run_extraction_for_turn_gives_up_after_two_malformed_responses():
    from core.orchestrator import _EMPTY_EXTRACTION, run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state()

    mock_llm = AsyncMock(
        return_value=SimpleNamespace(
            content="still not valid", input_tokens=10, output_tokens=5
        )
    )

    with (
        patch("core.orchestrator.llm.chat", new=mock_llm),
        patch(
            "core.orchestrator.story_extractor.process_extraction", new=AsyncMock()
        ) as mock_extract,
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=session_state),
        ),
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    assert mock_llm.call_count == 2
    mock_extract.assert_called_once_with(
        _EMPTY_EXTRACTION, _SESSION_ID, _USER_ID, db, turn_id=turn_id
    )


async def test_run_extraction_for_turn_triggers_close_when_session_should_end():
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state()
    ended_state = _make_session_state(session_end_suggested=True)

    llm_content = _EXTRACTION_LLM_CONTENT

    with (
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock()),
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=ended_state),
        ),
        patch(
            "core.orchestrator.close_and_process_session", new=AsyncMock()
        ) as mock_close,
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    mock_close.assert_called_once_with(_SESSION_ID, db)


async def test_run_extraction_for_turn_defers_close_when_goal_just_met():
    """
    Regression (WS5 pilot rehearsal): if goal_met flips false->true on
    this turn, the dialogue reply already sent was generated before this
    extraction ran and never got a chance to be a closing message —
    closing must defer to the next turn.
    """
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state(goal_met=False)
    newly_met_state = _make_session_state(goal_met=True, session_end_suggested=False)

    with (
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=_EXTRACTION_LLM_CONTENT, input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock()),
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=newly_met_state),
        ),
        patch(
            "core.orchestrator.close_and_process_session", new=AsyncMock()
        ) as mock_close,
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    mock_close.assert_not_called()


async def test_run_extraction_for_turn_closes_when_goal_was_already_met():
    """The turn after goal_met first flipped true: the dialogue call for
    this turn already knew, and closing may proceed."""
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state(goal_met=True)
    still_met_state = _make_session_state(goal_met=True, session_end_suggested=False)

    with (
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=_EXTRACTION_LLM_CONTENT, input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock()),
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=still_met_state),
        ),
        patch(
            "core.orchestrator.close_and_process_session", new=AsyncMock()
        ) as mock_close,
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    mock_close.assert_called_once_with(_SESSION_ID, db)


async def test_run_extraction_for_turn_does_not_close_when_session_continues():
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    turn_row = SimpleNamespace(
        id=turn_id, extraction_json={}, input_tokens=0, output_tokens=0
    )
    db = _make_extraction_db(turn_row=turn_row)
    session_state = _make_session_state()

    llm_content = _EXTRACTION_LLM_CONTENT

    with (
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock()),
        patch(
            "core.orchestrator.session_manager.apply_extraction",
            new=AsyncMock(return_value=session_state),
        ),
        patch(
            "core.orchestrator.close_and_process_session", new=AsyncMock()
        ) as mock_close,
    ):
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )

    mock_close.assert_not_called()


async def test_run_extraction_for_turn_does_not_raise_on_exception():
    from core.orchestrator import run_extraction_for_turn

    turn_id = uuid.uuid4()
    db = _make_extraction_db()
    session_state = _make_session_state()

    with patch(
        "core.orchestrator.llm.chat",
        new=AsyncMock(side_effect=RuntimeError("Anthropic is down")),
    ):
        # Should not raise — the reply was already sent.
        await run_extraction_for_turn(
            turn_id,
            _SESSION_ID,
            session_state,
            _make_profile(),
            PriorContext(),
            "transcript",
            "response",
            db,
        )
