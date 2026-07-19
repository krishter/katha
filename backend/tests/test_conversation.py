from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app
from models.db import get_db


async def _override_get_db():
    yield AsyncMock()


app.dependency_overrides[get_db] = _override_get_db

client = TestClient(app)

_FAKE_WAV = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 36
_DUMMY_AUDIO = b"fake-ogg-audio-bytes"


def _make_turn_result(**overrides):
    """Build a minimal TurnResult-like object."""
    from core.orchestrator import TurnResult
    from core.session_manager import SessionState

    defaults = dict(
        response_audio=_FAKE_WAV,
        response_text="Hello! How can I help you?",
        extraction_json={},
        transcript="नमस्ते",
        detected_language="hi-IN",
        session_state=SessionState(
            session_id="test-session-id",
            user_id="user-1",
            session_number=1,
            domain="childhood",
            exchange_count=1,
            energy_signal="high",
            goal_met=False,
            session_end_suggested=False,
        ),
        crisis_detected=False,
    )
    defaults.update(overrides)
    return TurnResult(**defaults)


@pytest.fixture(autouse=True)
def mock_orchestrator():
    """Mock process_voice_turn so no real adapters or DB are called."""
    with patch(
        "api.routes.conversation.orchestrator.process_voice_turn",
        new=AsyncMock(return_value=_make_turn_result()),
    ):
        yield


def test_conversation_turn_returns_200():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
        data={
            "session_id": "test-session-id",
            "user_name": "Subramaniam",
            "preferred_language": "hi-IN",
            "onboarding_context": "",
        },
    )
    assert response.status_code == 200


def test_conversation_turn_content_type_wav():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
        data={
            "session_id": "test-session-id",
            "user_name": "Subramaniam",
            "preferred_language": "hi-IN",
            "onboarding_context": "",
        },
    )
    assert response.headers["content-type"] == "audio/wav"


def test_conversation_turn_x_transcript_header():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
        data={
            "session_id": "test-session-id",
            "user_name": "Subramaniam",
            "preferred_language": "hi-IN",
            "onboarding_context": "",
        },
    )
    assert response.headers.get("x-transcript", "") != ""


def test_conversation_turn_returns_audio_bytes():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
        data={
            "session_id": "test-session-id",
            "user_name": "Subramaniam",
            "preferred_language": "hi-IN",
            "onboarding_context": "",
        },
    )
    assert response.content == _FAKE_WAV


def test_close_session_schedules_close_and_process_session():
    """The /close endpoint should trigger the full post-session pipeline
    (extraction + memory card generation/delivery), not just extraction."""
    with patch(
        "api.routes.conversation.orchestrator.close_and_process_session",
        new=AsyncMock(),
    ) as mock_close:
        response = client.post(
            "/conversation/close",
            data={
                "session_id": "test-session-id",
                "extraction_json_str": "{}",
                "transcript": "some transcript",
            },
        )

    assert response.status_code == 200
    assert response.json()["status"] == "closed"
    mock_close.assert_called_once()
    call_args = mock_close.call_args.args
    assert call_args[0] == "test-session-id"
    assert call_args[1] == "some transcript"
