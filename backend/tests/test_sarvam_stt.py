from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.sarvam_stt import TranscriptResult, transcribe


@pytest.fixture
def mock_stt_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "request_id": "test-request-id",
        "transcript": "नमस्ते, आप कैसे हैं?",
        "language_code": "hi-IN",
        "language_probability": 0.97,
        "timestamps": {},
    }
    return response


async def test_transcribe_returns_transcript(mock_stt_response):
    with patch("adapters.sarvam_stt.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_stt_response)
        mock_client_cls.return_value = mock_client

        result = await transcribe(b"fake-audio-bytes")

    assert isinstance(result, TranscriptResult)
    assert result.transcript == "नमस्ते, आप कैसे हैं?"
    assert result.transcript != ""


async def test_transcribe_returns_valid_language_code(mock_stt_response):
    with patch("adapters.sarvam_stt.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_stt_response)
        mock_client_cls.return_value = mock_client

        result = await transcribe(b"fake-audio-bytes")

    # BCP-47 format: language-REGION (e.g. hi-IN, ta-IN, en-IN)
    assert "-" in result.language_code
    parts = result.language_code.split("-")
    assert len(parts) == 2
    assert parts[0].islower()
    assert parts[1].isupper()


async def test_transcribe_raises_on_http_error():
    error_response = MagicMock()
    error_response.status_code = 401
    error_response.text = "Unauthorized"

    with patch("adapters.sarvam_stt.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=error_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Sarvam STT error 401"):
            await transcribe(b"fake-audio-bytes")
