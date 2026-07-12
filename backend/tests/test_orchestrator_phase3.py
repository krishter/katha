import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

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


async def test_run_post_session_calls_process_extraction_then_entity_extractor():
    db = _make_db()
    transcript = "I grew up in Madurai near the temple."

    with (
        patch(
            "core.orchestrator.story_extractor.process_extraction",
            new=AsyncMock(),
        ) as mock_extract,
        patch(
            "core.orchestrator.entity_extractor.extract_entities",
            new=AsyncMock(),
        ) as mock_entities,
    ):
        await run_post_session(_EXTRACTION_JSON, transcript, _SESSION_ID, _USER_ID, db)

    mock_extract.assert_called_once_with(_EXTRACTION_JSON, _SESSION_ID, _USER_ID, db)
    mock_entities.assert_called_once_with(transcript, _USER_ID, db)


async def test_run_post_session_sequential_order():
    """Entity extractor is called after story extractor (sequential, not concurrent)."""
    db = _make_db()
    call_order = []

    async def mock_extract(*a, **kw):
        call_order.append("extract")

    async def mock_entities(*a, **kw):
        call_order.append("entities")

    with (
        patch(
            "core.orchestrator.story_extractor.process_extraction",
            side_effect=mock_extract,
        ),
        patch(
            "core.orchestrator.entity_extractor.extract_entities",
            side_effect=mock_entities,
        ),
    ):
        await run_post_session(
            _EXTRACTION_JSON, "transcript", _SESSION_ID, _USER_ID, db
        )

    assert call_order == ["extract", "entities"]


async def test_run_post_session_does_not_raise_on_exception():
    """Exceptions must be swallowed (logged), not propagated."""
    db = _make_db()

    with (
        patch(
            "core.orchestrator.story_extractor.process_extraction",
            new=AsyncMock(side_effect=RuntimeError("DB error")),
        ),
        patch(
            "core.orchestrator.entity_extractor.extract_entities",
            new=AsyncMock(),
        ),
    ):
        # Should not raise
        await run_post_session(
            _EXTRACTION_JSON, "transcript", _SESSION_ID, _USER_ID, db
        )


# ── process_voice_turn integration ────────────────────────────────────────────


async def test_voice_turn_schedules_background_task_not_awaits():
    """
    When session_end_suggested=True, the voice turn must schedule
    run_post_session as a background task, not await it directly.
    """
    from core.orchestrator import process_voice_turn
    from prompts.system_prompt import UserProfile

    profile = UserProfile(
        name="Subramaniam",
        preferred_language="ta-IN",
        onboarding_context="",
    )
    session_state = SessionState(
        session_id=_SESSION_ID,
        user_id=_USER_ID,
        session_number=1,
        domain="childhood",
        exchange_count=6,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    _FAKE_WAV = b"RIFF" + b"\x00" * 40
    extraction_with_end = {**_EXTRACTION_JSON, "session_end_suggested": True}
    import json as _json

    llm_content = (
        "<response>Wonderful, let us talk tomorrow.</response>\n"
        f"<extraction>{_json.dumps(extraction_with_end)}</extraction>"
    )

    bg = BackgroundTasks()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=session_state),
        ),
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    transcript="I am tired now.",
                    language_code="ta-IN",
                    language_probability=0.9,
                )
            ),
        ),
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=100, output_tokens=50
                )
            ),
        ),
        patch(
            "core.orchestrator.sarvam_tts.synthesize",
            new=AsyncMock(return_value=_FAKE_WAV),
        ),
        patch(
            "core.orchestrator.session_manager.update_session",
            new=AsyncMock(return_value=session_state),
        ),
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
            new=AsyncMock(return_value=[]),
        ),
    ):
        result = await process_voice_turn(
            b"audio", _SESSION_ID, profile, _make_db(), bg
        )

    # Voice turn returned — background task was added, not awaited
    assert result.response_audio == _FAKE_WAV
    assert len(bg.tasks) == 1  # exactly one background task scheduled
