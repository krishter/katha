import base64
from unittest.mock import AsyncMock, patch

import pytest
from fastapi.testclient import TestClient

from main import app

client = TestClient(app)

_FAKE_WAV = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 36
_FAKE_WAV_B64 = base64.b64encode(_FAKE_WAV).decode()
_DUMMY_AUDIO = b"fake-ogg-audio-bytes"


@pytest.fixture(autouse=True)
def mock_all_adapters():
    """Patch all three adapters so no real API calls are made."""
    with (
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                return_value=type(
                    "TranscriptResult",
                    (),
                    {
                        "transcript": "नमस्ते",
                        "language_code": "hi-IN",
                        "language_probability": 0.97,
                    },
                )()
            ),
        ),
        patch(
            "core.orchestrator.llm.chat",
            new=AsyncMock(
                return_value=type(
                    "LLMResponse",
                    (),
                    {
                        "content": "Hello! I am Katha.",
                        "input_tokens": 10,
                        "output_tokens": 8,
                    },
                )()
            ),
        ),
        patch(
            "core.orchestrator.sarvam_tts.synthesize",
            new=AsyncMock(return_value=_FAKE_WAV),
        ),
    ):
        yield


def test_conversation_turn_returns_200():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
    )
    assert response.status_code == 200


def test_conversation_turn_content_type_wav():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
    )
    assert response.headers["content-type"] == "audio/wav"


def test_conversation_turn_x_transcript_header():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
    )
    assert response.headers.get("x-transcript", "") != ""


def test_conversation_turn_returns_audio_bytes():
    response = client.post(
        "/conversation/turn",
        files={"audio": ("test.ogg", _DUMMY_AUDIO, "audio/ogg")},
    )
    assert response.content == _FAKE_WAV
