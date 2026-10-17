"""
Gemini 3.8 Flash TTS, for the operator listening kit's blind A/B only
(scripts/listening_kit.py abtest --compare sarvam-gemini;
docs/proposals/tts-strategy.md §5).

Production never calls this: nothing outside the kit imports it, and
TTS_MODEL and media/voices.py know nothing about Google. Only synthetic A/B
text goes to Google, never anything a parent said.

The request follows the Interactions API as documented on 2026-10-17
(https://ai.google.dev/gemini-api/docs/speech-generation):

- POST /v1beta/interactions, key in the x-goog-api-key header.
- The text is a verbatim transcript; delivery is steered by a
  `speech_metadata` annotation's `style`, not by instructions in the text.
- There is no language parameter: the model detects the language from the
  text. `language_code` is accepted for symmetry with the Sarvam adapter
  and is only logged.
- A unary request returns WAV by default. This one asks for raw 24 kHz
  16-bit mono PCM (audio/l16) and wraps it in a WAV header here, so the
  clip goes through convert_wav_to_ogg and media/tts_check.py exactly as a
  Sarvam clip does.
"""

from __future__ import annotations

import base64
import io
import logging
import wave

import httpx

from adapters.retry import post_with_retry
from adapters.sarvam_tts import speakable
from config import settings
from core import usage
from media import tts_check, voices

logger = logging.getLogger(__name__)

MODEL = "gemini-3.8-flash-tts"
_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
SAMPLE_RATE = 24_000
_SAMPLE_WIDTH = 2  # 16-bit

# The one delivery style for the A/B. Google advises a short style string,
# reused unchanged across turns, over long instructions.
STYLE = (
    "unhurried, warm and gentle, talking with an elderly parent; "
    "calm and kind, not energetic, not salesy"
)

# One prebuilt voice per persona, the same for every language. Gender from
# Google Cloud's Chirp 3 HD list, which shares these names
# (https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd); the
# descriptor in brackets is the Gemini docs' own.
# - Sulafat (Warm): the only voice of the 30 described as warm.
# - Achird (Friendly): no male voice is described as warm or gentle;
#   friendly is the nearest, and avoids the Firm, Excitable and Upbeat ones.
VOICES: dict[str, str] = {"female": "Sulafat", "male": "Achird"}


def pcm_to_wav(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> bytes:
    """Wrap raw 16-bit mono PCM in a WAV header."""
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(_SAMPLE_WIDTH)
        wav.setframerate(sample_rate)
        wav.writeframes(pcm[: len(pcm) - len(pcm) % _SAMPLE_WIDTH])
    return buffer.getvalue()


def wav_seconds(wav_bytes: bytes) -> float:
    with wave.open(io.BytesIO(wav_bytes)) as wav:
        return wav.getnframes() / wav.getframerate()


def request_body(text: str, voice: str, style: str) -> dict:
    return {
        "model": MODEL,
        "input": [
            {
                "type": "user_input",
                "content": [
                    {
                        "type": "text",
                        "text": text,
                        "annotations": [{"type": "speech_metadata", "style": style}],
                    }
                ],
            }
        ],
        "response_format": {
            "type": "audio",
            "mime_type": "audio/l16",
            "sample_rate": SAMPLE_RATE,
        },
        "generation_config": {"speech_config": [{"voice": voice}]},
    }


def _audio_from(body: dict) -> bytes:
    """The last audio part of the model's output, decoded."""
    parts = [
        part
        for step in body.get("steps") or []
        if step.get("type") == "model_output"
        for part in step.get("content") or []
        if part.get("type") == "audio"
    ]
    if not parts:
        raise RuntimeError("Gemini TTS returned no audio")
    return base64.b64decode(parts[-1]["data"])


async def _request(text: str, voice: str, style: str, purpose: str) -> bytes:
    """One billed call. Returns WAV bytes."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set in backend/.env")
    async with httpx.AsyncClient(timeout=60.0) as client:
        response = await post_with_retry(
            client,
            _URL,
            headers={
                "x-goog-api-key": settings.GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json=request_body(text, voice, style),
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini TTS error {response.status_code}: {response.text[:500]}"
        )
    audio = _audio_from(response.json())
    wav = audio if audio[:4] == b"RIFF" else pcm_to_wav(audio)
    # Google bills audio by the second and text by the token; the ledger
    # keeps the counts and core/pricing.py turns them into rupees.
    await usage.record(
        "google",
        purpose,
        {
            "audio_seconds": round(wav_seconds(wav), 3),
            "input_characters": len(text) + len(style),
        },
        model=MODEL,
    )
    return wav


async def synthesize(
    text: str,
    language_code: str,
    *,
    gender: str | None,
    purpose: str,
    style: str = STYLE,
) -> bytes:
    """
    Speak `text` in the persona's Gemini voice. Returns WAV bytes.

    Emoji are removed, as for Sarvam. A clip media/tts_check.py rejects is
    synthesised again once; if that fails too it is returned anyway with an
    error logged, so a listener hears what Gemini produced.
    """
    voice = VOICES[voices.normalise_gender(gender)]
    text = speakable(text)
    wav = b""
    for attempt in (1, 2):
        wav = await _request(text, voice, style, purpose)
        problem = tts_check.speech_problem(wav, text)
        if problem is None:
            return wav
        log = logger.error if attempt == 2 else logger.warning
        log(
            "Gemini TTS clip rejected (%s, %s, attempt %d of 2): %s",
            voice,
            language_code,
            attempt,
            problem,
        )
    return wav
