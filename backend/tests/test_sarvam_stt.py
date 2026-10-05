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


# ── Long voice notes (production incident, 2026-10-05) ───────────────────────
#
# Sarvam's synchronous endpoint rejects anything over 30 seconds:
#   400 Audio duration exceeds the maximum limit of 30 seconds.
# The PRD expects 3-5 minute voice notes (6.2), so this was the normal case,
# not an edge case: an 89-second note came back as "I could not quite hear
# that. Can you send that again?" Long audio is now split at natural pauses
# and the chunk transcripts joined.


def _chunk_response(transcript: str, language: str, probability: float) -> MagicMock:
    response = MagicMock()
    response.status_code = 200
    response.json.return_value = {
        "transcript": transcript,
        "language_code": language,
        "language_probability": probability,
    }
    return response


async def test_transcribe_joins_chunk_transcripts_in_order():
    """Three pieces in, one coherent transcript out, order preserved."""
    responses = [
        _chunk_response("I grew up in a colony of row houses.", "en-IN", 0.91),
        _chunk_response("My grandfather lived with us.", "en-IN", 0.95),
        _chunk_response("The road was where we played.", "en-IN", 0.88),
    ]
    with (
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt",
            new=AsyncMock(return_value=[b"a", b"b", b"c"]),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient") as client_cls,
        patch(
            "adapters.sarvam_stt.post_with_retry",
            new=AsyncMock(side_effect=responses),
        ),
    ):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client

        result = await transcribe(b"long-audio")

    assert result.transcript == (
        "I grew up in a colony of row houses. "
        "My grandfather lived with us. "
        "The road was where we played."
    )


async def test_transcribe_reports_the_most_confident_language():
    """
    An opening "Haan..." must not decide the language for a five-minute
    answer — the language picked here is what the reply is spoken back in.
    """
    responses = [
        _chunk_response("Haan.", "hi-IN", 0.41),
        _chunk_response(
            "I grew up in Madurai, in a house near the temple.", "en-IN", 0.97
        ),
    ]
    with (
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt",
            new=AsyncMock(return_value=[b"a", b"b"]),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient") as client_cls,
        patch(
            "adapters.sarvam_stt.post_with_retry",
            new=AsyncMock(side_effect=responses),
        ),
    ):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client

        result = await transcribe(b"long-audio")

    assert result.language_code == "en-IN"
    assert result.language_probability == 0.97


async def test_a_failed_chunk_fails_the_turn():
    """
    A silently dropped middle is worse than a failed turn: she would be
    told her story was heard, and the hole is invisible until the family
    reads the archive. Better to ask her to resend.
    """
    with (
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt",
            new=AsyncMock(return_value=[b"a", b"b"]),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient") as client_cls,
        patch(
            "adapters.sarvam_stt.post_with_retry",
            new=AsyncMock(
                side_effect=[
                    _chunk_response("first half", "en-IN", 0.9),
                    MagicMock(status_code=500, text="upstream exploded"),
                ]
            ),
        ),
    ):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client

        with pytest.raises(RuntimeError, match="Sarvam STT error 500"):
            await transcribe(b"long-audio")


async def test_short_audio_still_makes_exactly_one_call(mock_stt_response):
    """No extra calls, no ffmpeg, for the common short reply."""
    with (
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt",
            new=AsyncMock(return_value=[b"short"]),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient") as client_cls,
        patch(
            "adapters.sarvam_stt.post_with_retry",
            new=AsyncMock(return_value=mock_stt_response),
        ) as post,
    ):
        client = AsyncMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client_cls.return_value = client

        result = await transcribe(b"short")

    assert post.await_count == 1
    assert result.transcript == "नमस्ते, आप कैसे हैं?"
