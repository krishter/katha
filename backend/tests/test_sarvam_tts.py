import base64
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.sarvam_tts import synthesize

# Minimal valid WAV header (44 bytes) as fixture audio
_FAKE_WAV = b"RIFF$\x00\x00\x00WAVEfmt " + b"\x00" * 36
_FAKE_WAV_B64 = base64.b64encode(_FAKE_WAV).decode()


@pytest.fixture
def mock_tts_response():
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "request_id": "test-request-id",
        "audios": [_FAKE_WAV_B64],
    }
    return response


async def test_synthesize_returns_bytes(mock_tts_response):
    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client

        result = await synthesize("Hello, how are you?", "en-IN")

    assert isinstance(result, bytes)
    assert len(result) > 0


async def test_synthesize_decodes_base64(mock_tts_response):
    """Result must be decoded WAV bytes, not the raw base64 string."""
    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client

        result = await synthesize("Hello", "en-IN")

    assert result == _FAKE_WAV
    # Must not be the raw base64 string bytes
    assert result != _FAKE_WAV_B64.encode()


async def test_synthesize_raises_on_http_error():
    error_response = MagicMock()
    error_response.status_code = 422
    error_response.text = "Unprocessable Entity"

    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=error_response)
        mock_client_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Sarvam TTS error 422"):
            await synthesize("Hello", "en-IN")


async def test_synthesize_truncates_long_text(mock_tts_response):
    long_text = "a" * 3000

    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client

        result = await synthesize(long_text, "en-IN")

    # Should succeed (not raise) and return bytes
    assert isinstance(result, bytes)
    # Verify the truncated text was sent (check via call args)
    call_kwargs = mock_client.post.call_args.kwargs
    assert len(call_kwargs["json"]["text"]) == 2500
