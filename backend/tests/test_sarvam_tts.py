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

        result = await synthesize("Hello, how are you?", "en-IN", purpose="tts_reply")

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

        result = await synthesize("Hello", "en-IN", purpose="tts_reply")

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
            await synthesize("Hello", "en-IN", purpose="tts_reply")


async def test_synthesize_truncates_long_text(mock_tts_response):
    long_text = "a" * 3000

    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client

        result = await synthesize(long_text, "en-IN", purpose="tts_reply")

    # Should succeed (not raise) and return bytes
    assert isinstance(result, bytes)
    # Verify the truncated text was sent (check via call args)
    call_kwargs = mock_client.post.call_args.kwargs
    assert len(call_kwargs["json"]["text"]) == 2500


# ── C6: model, voice and field selection (COST_TO_SERVE.md §10) ───────────────


async def _sent(monkeypatch, tts_model, language, mock_tts_response, **kwargs):
    monkeypatch.setattr("adapters.sarvam_tts.settings.TTS_MODEL", tts_model)
    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client
        await synthesize("नमस्ते", language, purpose="tts_reply", **kwargs)
    return mock_client.post.call_args.kwargs["json"]


async def test_v3_by_default_with_ritu_and_its_original_field(
    monkeypatch, mock_tts_response
):
    body = await _sent(monkeypatch, "bulbul:v3", "hi-IN", mock_tts_response)
    assert body["model"] == "bulbul:v3"
    assert body["speaker"] == "ritu"
    # Unchanged from what production has always sent.
    assert body["target_language_code"] == "hi-IN"
    assert "language_code" not in body


async def test_v4_flash_uses_the_per_language_voice_and_language_code(
    monkeypatch, mock_tts_response, usage_rows
):
    body = await _sent(monkeypatch, "bulbul:v4-flash", "hi-IN", mock_tts_response)
    assert body["model"] == "bulbul:v4-flash"
    assert body["speaker"] == "ritu_hi_conversation"
    assert body["language_code"] == "hi-IN"
    assert "target_language_code" not in body
    assert usage_rows[0]["model"] == "bulbul:v4-flash"


@pytest.mark.parametrize("language", ["ml-IN", "or-IN", "ta-IN", "pa-IN", "as-IN"])
async def test_v4_flash_falls_back_to_v3_for_an_unmapped_language(
    monkeypatch, mock_tts_response, usage_rows, language
):
    body = await _sent(monkeypatch, "bulbul:v4-flash", language, mock_tts_response)
    assert body["model"] == "bulbul:v3"
    assert body["speaker"] == "ritu"
    assert body["target_language_code"] == language
    # The ledger prices what was actually used.
    assert usage_rows[0]["model"] == "bulbul:v3"


def test_every_v4_voice_belongs_to_its_language():
    from media.voices import V4_FLASH_VOICES
    from prompts.system_prompt import SUPPORTED_LANGUAGES

    for language, voice in V4_FLASH_VOICES.items():
        assert language in SUPPORTED_LANGUAGES
        assert f"_{language.split('-')[0]}_" in voice, (language, voice)
