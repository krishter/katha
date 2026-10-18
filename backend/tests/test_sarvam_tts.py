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

        result = await synthesize(
            "Hello, how are you?", "en-IN", gender="female", purpose="tts_reply"
        )

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

        result = await synthesize(
            "Hello", "en-IN", gender="female", purpose="tts_reply"
        )

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
            await synthesize("Hello", "en-IN", gender="female", purpose="tts_reply")


async def test_synthesize_truncates_long_text(mock_tts_response):
    long_text = "a" * 3000

    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client

        result = await synthesize(
            long_text, "en-IN", gender="female", purpose="tts_reply"
        )

    # Should succeed (not raise) and return bytes
    assert isinstance(result, bytes)
    # Verify the truncated text was sent (check via call args)
    call_kwargs = mock_client.post.call_args.kwargs
    assert len(call_kwargs["json"]["text"]) == 2500


# ── C6: model and voice from language and persona (COST_TO_SERVE.md §10) ─────


async def _sent(
    monkeypatch, tts_model, language, mock_tts_response, gender="female", **kwargs
):
    monkeypatch.setattr("adapters.sarvam_tts.settings.TTS_MODEL", tts_model)
    with patch("adapters.sarvam_tts.httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.post = AsyncMock(return_value=mock_tts_response)
        mock_client_cls.return_value = mock_client
        await synthesize("नमस्ते", language, gender=gender, purpose="tts_reply", **kwargs)
    return mock_client.post.call_args.kwargs["json"]


@pytest.mark.parametrize(
    "gender,speaker", [("female", "ritu"), ("male", "shubh"), (None, "ritu")]
)
async def test_v3_rollback_puts_every_persona_on_v3_with_its_original_field(
    monkeypatch, mock_tts_response, gender, speaker
):
    body = await _sent(
        monkeypatch, "bulbul:v3", "hi-IN", mock_tts_response, gender=gender
    )
    assert body["model"] == "bulbul:v3"
    assert body["speaker"] == speaker
    # Unchanged from what production has always sent.
    assert body["target_language_code"] == "hi-IN"
    assert "language_code" not in body


@pytest.mark.parametrize(
    "language,gender,speaker",
    [
        ("hi-IN", "female", "ritu_hi_conversation"),
        ("hi-IN", "male", "aditya_hi_conversational"),
        ("en-IN", "female", "simran_en_conversation"),
        ("en-IN", "male", "dev_en_conversational"),
        ("bn-IN", "male", "arnab_bn_conversation"),
    ],
)
async def test_v4_flash_uses_the_persona_voice_for_the_language(
    monkeypatch, mock_tts_response, usage_rows, language, gender, speaker
):
    body = await _sent(
        monkeypatch, "bulbul:v4-flash", language, mock_tts_response, gender=gender
    )
    assert body["model"] == "bulbul:v4-flash"
    assert body["speaker"] == speaker
    assert body["language_code"] == language
    assert "target_language_code" not in body
    assert usage_rows[0]["model"] == "bulbul:v4-flash"


@pytest.mark.parametrize(
    "language,gender,speaker",
    [
        # No v4 voices yet.
        ("ml-IN", "female", "ritu"),
        ("ml-IN", "male", "shubh"),
        # Tamil's v4 voices are ads and narration only.
        ("ta-IN", "female", "ritu"),
        ("ta-IN", "male", "shubh"),
        # No conversation-style voice for this persona.
        ("te-IN", "male", "shubh"),
        ("pa-IN", "female", "ritu"),
    ],
)
async def test_a_persona_without_a_conversation_voice_stays_on_v3(
    monkeypatch, mock_tts_response, usage_rows, language, gender, speaker
):
    body = await _sent(
        monkeypatch, "bulbul:v4-flash", language, mock_tts_response, gender=gender
    )
    assert body["model"] == "bulbul:v3"
    assert body["speaker"] == speaker
    assert body["target_language_code"] == language
    # The ledger prices what was actually used.
    assert usage_rows[0]["model"] == "bulbul:v3"


async def test_odia_is_sent_with_the_code_sarvam_accepts(
    monkeypatch, mock_tts_response
):
    # Sarvam rejects "or-IN" with a 400 (checked live 2026-10-17).
    body = await _sent(monkeypatch, "bulbul:v4-flash", "or-IN", mock_tts_response)
    assert body["target_language_code"] == "od-IN"


def test_every_v4_voice_belongs_to_its_language_and_is_conversational():
    from media.voices import GENDERS, V4_FLASH_VOICES
    from prompts.system_prompt import SUPPORTED_LANGUAGES

    for (language, gender), voice in V4_FLASH_VOICES.items():
        assert language in SUPPORTED_LANGUAGES
        assert gender in GENDERS
        assert f"_{language.split('-')[0]}_" in voice, (language, voice)
        assert voice.endswith(("_conversation", "_conversational")), voice


def test_every_supported_language_has_a_voice_for_both_personas():
    from media.voices import GENDERS, voice_for
    from prompts.system_prompt import SUPPORTED_LANGUAGES

    for language in SUPPORTED_LANGUAGES:
        for gender in GENDERS:
            assert voice_for(language, gender).speaker


# ── Speech check and emoji (2026-10-17 listening results) ─────────────────────

from adapters.sarvam_tts import speakable  # noqa: E402
from tests.test_tts_check import make_wav  # noqa: E402

_GOOD = make_wav(("tone", 6))
_BROKEN = make_wav(("tone", 0.5), ("silence", 20), ("tone", 3))


def _replying(*clips):
    responses = []
    for clip in clips:
        response = MagicMock(status_code=200)
        response.json.return_value = {"audios": [base64.b64encode(clip).decode()]}
        responses.append(response)
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(side_effect=responses)
    return client


async def _speak(monkeypatch, tts_model, *clips, language="hi-IN"):
    monkeypatch.setattr("adapters.sarvam_tts.settings.TTS_MODEL", tts_model)
    client = _replying(*clips)
    with patch("adapters.sarvam_tts.httpx.AsyncClient", return_value=client):
        audio = await synthesize(
            "x" * 80, language, gender="female", purpose="tts_reply"
        )
    sent = [call.kwargs["json"] for call in client.post.call_args_list]
    return audio, sent


async def test_a_good_clip_is_sent_after_one_call(monkeypatch, usage_rows):
    audio, sent = await _speak(monkeypatch, "bulbul:v4-flash", _GOOD)
    assert audio == _GOOD
    assert len(sent) == 1


async def test_a_broken_v4_clip_is_synthesised_again(monkeypatch, usage_rows):
    audio, sent = await _speak(monkeypatch, "bulbul:v4-flash", _BROKEN, _GOOD)
    assert audio == _GOOD
    assert [body["model"] for body in sent] == ["bulbul:v4-flash"] * 2
    # Both attempts were paid for, so both are on the ledger.
    assert [row["model"] for row in usage_rows] == ["bulbul:v4-flash"] * 2


async def test_v4_failing_twice_falls_back_to_v3(monkeypatch, usage_rows):
    audio, sent = await _speak(monkeypatch, "bulbul:v4-flash", _BROKEN, _BROKEN, _GOOD)
    assert audio == _GOOD
    assert [body["model"] for body in sent] == [
        "bulbul:v4-flash",
        "bulbul:v4-flash",
        "bulbul:v3",
    ]
    assert sent[2]["speaker"] == "ritu"
    assert sent[2]["target_language_code"] == "hi-IN"
    assert [row["model"] for row in usage_rows][-1] == "bulbul:v3"


async def test_a_clip_failing_every_attempt_is_still_returned(monkeypatch, usage_rows):
    audio, sent = await _speak(monkeypatch, "bulbul:v3", _BROKEN, _BROKEN)
    assert audio == _BROKEN
    assert len(sent) == 2


async def test_emoji_never_reach_the_speech_engine(monkeypatch, usage_rows):
    monkeypatch.setattr("adapters.sarvam_tts.settings.TTS_MODEL", "bulbul:v3")
    client = _replying(_GOOD)
    with patch("adapters.sarvam_tts.httpx.AsyncClient", return_value=client):
        await synthesize(
            "दादाजी! 😊 उनका नाम क्या था?", "hi-IN", gender="female", purpose="tts_reply"
        )
    assert client.post.call_args.kwargs["json"]["text"] == "दादाजी! उनका नाम क्या था?"


def test_speakable_removes_emoji_sequences_and_keeps_indic_joiners():
    assert speakable("Hi 👍🏽 there 👨‍👩‍👧 ok") == "Hi there ok"
    assert speakable("so lovely ❤️.") == "so lovely."
    # ZWJ inside Devanagari shapes the letters and must survive.
    assert speakable("क्‍या") == "क्‍या"
    assert speakable("₹180 मिले") == "₹180 मिले"


def test_v4_flash_is_the_default():
    from config import Settings

    assert Settings.model_fields["TTS_MODEL"].default == "bulbul:v4-flash"
