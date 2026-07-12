import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core.orchestrator import TurnResult, process_voice_turn
from core.session_manager import SessionState
from prompts.system_prompt import UserProfile

# ── fixtures ──────────────────────────────────────────────────────────────────

_FAKE_WAV = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 36
_FAKE_WAV_B64 = base64.b64encode(_FAKE_WAV).decode()
_SESSION_ID = str(uuid.uuid4())

_USER_PROFILE = UserProfile(
    name="Subramaniam",
    preferred_language="ta-IN",
    onboarding_context="Grew up in Madurai.",
)

_SESSION_STATE = SessionState(
    session_id=_SESSION_ID,
    user_id="user-1",
    session_number=1,
    domain="childhood",
    exchange_count=0,
    energy_signal="high",
    goal_met=False,
    session_end_suggested=False,
)

_WELL_FORMED_LLM = (
    "<response>Good morning, Subramaniam ji! Tell me about the house you grew up in."
    "</response>\n"
    '<extraction>{"story_atoms":[],"named_entities":{},"themes":["childhood"],'
    '"energy_signal":"high","gaps_remaining":["house details"],'
    '"session_end_suggested":false}</extraction>'
)


def _make_db():
    return AsyncMock()


@pytest.fixture(autouse=True)
def mock_stt():
    stt_result = SimpleNamespace(
        transcript="I grew up in Madurai near the temple.",
        language_code="ta-IN",
        language_probability=0.95,
    )
    with patch(
        "core.orchestrator.sarvam_stt.transcribe",
        new=AsyncMock(return_value=stt_result),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_tts():
    with patch(
        "core.orchestrator.sarvam_tts.synthesize",
        new=AsyncMock(return_value=_FAKE_WAV),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_get_session():
    with patch(
        "core.orchestrator.session_manager.get_session",
        new=AsyncMock(return_value=_SESSION_STATE),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_update_session():
    updated = SessionState(
        session_id=_SESSION_ID,
        user_id="user-1",
        session_number=1,
        domain="childhood",
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    with patch(
        "core.orchestrator.session_manager.update_session",
        new=AsyncMock(return_value=updated),
    ):
        yield


@pytest.fixture(autouse=True)
def mock_prior_context():
    """Mock fact_store and vector_store so Phase 2 tests don't hit real DB/OpenAI."""
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
            new=AsyncMock(return_value=[]),
        ),
    ):
        yield


# ── tests ─────────────────────────────────────────────────────────────────────


async def test_normal_turn_returns_parsed_response_text():
    with patch(
        "core.orchestrator.llm.chat",
        new=AsyncMock(
            return_value=SimpleNamespace(
                content=_WELL_FORMED_LLM, input_tokens=200, output_tokens=80
            )
        ),
    ):
        result = await process_voice_turn(
            b"audio", _SESSION_ID, _USER_PROFILE, _make_db()
        )

    assert isinstance(result, TurnResult)
    assert "Subramaniam" in result.response_text
    assert result.crisis_detected is False


async def test_normal_turn_returns_extraction_json():
    with patch(
        "core.orchestrator.llm.chat",
        new=AsyncMock(
            return_value=SimpleNamespace(
                content=_WELL_FORMED_LLM, input_tokens=200, output_tokens=80
            )
        ),
    ):
        result = await process_voice_turn(
            b"audio", _SESSION_ID, _USER_PROFILE, _make_db()
        )

    assert isinstance(result.extraction_json, dict)
    assert "energy_signal" in result.extraction_json


async def test_crisis_keyword_skips_llm():
    """When transcript contains crisis keyword, LLM must NOT be called."""
    with patch(
        "core.orchestrator.sarvam_stt.transcribe",
        new=AsyncMock(
            return_value=SimpleNamespace(
                transcript="I want to end my life",
                language_code="en-IN",
                language_probability=0.99,
            )
        ),
    ):
        with patch("core.orchestrator.llm.chat") as mock_llm:
            result = await process_voice_turn(
                b"audio", _SESSION_ID, _USER_PROFILE, _make_db()
            )

    assert result.crisis_detected is True
    mock_llm.assert_not_called()
    assert "9152987821" in result.response_text


async def test_session_state_is_updated_after_turn():
    with patch(
        "core.orchestrator.llm.chat",
        new=AsyncMock(
            return_value=SimpleNamespace(
                content=_WELL_FORMED_LLM, input_tokens=200, output_tokens=80
            )
        ),
    ):
        with patch("core.orchestrator.session_manager.update_session") as mock_update:
            mock_update.return_value = AsyncMock(return_value=_SESSION_STATE)
            await process_voice_turn(b"audio", _SESSION_ID, _USER_PROFILE, _make_db())

    mock_update.assert_called_once()
