import uuid
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

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


# ── process_voice_turn integration ────────────────────────────────────────────


def _make_turn_db() -> AsyncMock:
    """A db mock sufficient for _persist_turn's add/commit/refresh sequence."""
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


_FAKE_WAV = b"RIFF" + b"\x00" * 40


def _voice_turn_patches(stack: ExitStack, session_state, llm_content) -> AsyncMock:
    """Patch every external dependency of process_voice_turn except
    story_extractor.process_extraction, which the caller patches itself so
    it can assert on the call. Returns the mocked process_extraction."""
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
            "core.orchestrator.session_manager.update_session",
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
    return stack.enter_context(
        patch("core.orchestrator.story_extractor.process_extraction", new=AsyncMock())
    )


async def test_process_voice_turn_persists_turn_before_extraction():
    from core.orchestrator import process_voice_turn
    from models.turn import Turn
    from prompts.system_prompt import UserProfile

    profile = UserProfile(
        name="Subramaniam", preferred_language="ta-IN", onboarding_context=""
    )
    session_state = SessionState(
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        session_number=1,
        domain="childhood",
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    import json as _json

    llm_content = (
        "<response>Tell me more about that.</response>\n"
        f"<extraction>{_json.dumps(_EXTRACTION_JSON)}</extraction>"
    )
    db = _make_turn_db()

    with ExitStack() as stack:
        mock_extract = _voice_turn_patches(stack, session_state, llm_content)
        result = await process_voice_turn(
            b"audio", _SESSION_ID, profile, db, inbound_message_sid="SM123"
        )

    assert result.response_audio == _FAKE_WAV

    # A Turn row was persisted (turn_number derives from exchange_count + 1)
    added_turn = db.add.call_args.args[0]
    assert isinstance(added_turn, Turn)
    assert added_turn.turn_number == 2
    assert added_turn.inbound_message_sid == "SM123"
    assert added_turn.transcript == "I grew up in Madurai near the temple."
    db.commit.assert_called()

    # process_extraction was called with that same turn's id
    mock_extract.assert_called_once()
    call_args = mock_extract.call_args.args
    call_kwargs = mock_extract.call_args.kwargs
    assert call_args[0] == _EXTRACTION_JSON
    assert call_args[1] == _SESSION_ID
    assert call_args[2] == _USER_ID
    assert call_kwargs["turn_id"] == added_turn.id


async def test_process_voice_turn_persists_turn_even_if_tts_then_fails():
    """
    The turn (transcript + extraction) must already be committed by the time
    TTS runs, so a TTS/ffmpeg failure — or the process dying right there —
    can never lose the story the user just told (C1/P1).
    """
    from core.orchestrator import process_voice_turn
    from prompts.system_prompt import UserProfile

    profile = UserProfile(
        name="Subramaniam", preferred_language="ta-IN", onboarding_context=""
    )
    session_state = SessionState(
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        session_number=1,
        domain="childhood",
        exchange_count=0,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    import json as _json

    llm_content = (
        "<response>Tell me more.</response>\n"
        f"<extraction>{_json.dumps(_EXTRACTION_JSON)}</extraction>"
    )
    db = _make_turn_db()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, llm_content)
        stack.enter_context(
            patch(
                "core.orchestrator.sarvam_tts.synthesize",
                new=AsyncMock(side_effect=RuntimeError("Sarvam TTS is down")),
            )
        )
        try:
            await process_voice_turn(b"audio", _SESSION_ID, profile, db)
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected the TTS failure to propagate")

    # The turn was already committed before the TTS call that then failed.
    db.add.assert_called_once()
    db.commit.assert_called_once()


async def test_process_voice_turn_defaults_inbound_message_sid_to_none():
    from core.orchestrator import process_voice_turn
    from prompts.system_prompt import UserProfile

    profile = UserProfile(
        name="Subramaniam", preferred_language="ta-IN", onboarding_context=""
    )
    session_state = SessionState(
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        session_number=1,
        domain="childhood",
        exchange_count=0,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    import json as _json

    llm_content = (
        "<response>Tell me more.</response>\n"
        f"<extraction>{_json.dumps(_EXTRACTION_JSON)}</extraction>"
    )
    db = _make_turn_db()

    with ExitStack() as stack:
        _voice_turn_patches(stack, session_state, llm_content)
        await process_voice_turn(b"audio", _SESSION_ID, profile, db)

    added_turn = db.add.call_args.args[0]
    assert added_turn.inbound_message_sid is None
    assert added_turn.turn_number == 1
