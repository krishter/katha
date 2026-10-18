"""
Gemini 3.8 TTS: Katha's voice for new families since 2026-10-18
(docs/specs/GEMINI_TTS.md), and one side of the listening kit's A/B.

Production reaches this only through media/tts.py, with the parent's
pinned voice (Flash-Lite, D1). The kit calls it directly, on Flash by
default.

The request follows the Interactions API as documented on 2026-10-17
(https://ai.google.dev/gemini-api/docs/speech-generation):

- POST /v1beta/interactions, key in the x-goog-api-key header.
- `"store": false` on every request. The Interactions API keeps each
  interaction by default (https://ai.google.dev/gemini-api/docs/zdr), and a
  parent's story must not be kept at Google.
- The text is a verbatim transcript; delivery is steered by a
  `speech_metadata` annotation's `style`, not by instructions in the text.
- There is no language parameter: the model detects the language from the
  text. For a language whose script another Katha language shares
  (Marathi with Hindi, Assamese with Bengali), the style names the
  language, since that is the only lever there is.
- A unary request returns WAV by default. This one asks for raw 24 kHz
  16-bit mono PCM (audio/l16) and wraps it in a WAV header here, so the
  clip goes through convert_wav_to_ogg and media/tts_check.py exactly as a
  Sarvam clip does.
"""

from __future__ import annotations

import base64
import io
import logging
import time
import wave

import httpx

from adapters.retry import post_with_backoff
from adapters.sarvam_tts import speakable
from config import settings
from core import usage
from media import tts_check, voices

logger = logging.getLogger(__name__)

# The kit's default. Production speaks on voices.GEMINI_TTS_MODEL.
MODEL = "gemini-3.8-flash-tts"
# Same API, voices and style; two thirds of Flash's audio price.
LITE_MODEL = voices.GEMINI_TTS_MODEL
MODELS = (MODEL, LITE_MODEL)
_URL = "https://generativelanguage.googleapis.com/v1beta/interactions"
SAMPLE_RATE = 24_000
_SAMPLE_WIDTH = 2  # 16-bit

# The one delivery style, as heard in the A/B. Google advises a short style
# string, reused unchanged across turns, over long instructions.
STYLE = (
    "unhurried, warm and gentle, talking with an elderly parent; "
    "calm and kind, not energetic, not salesy"
)

# Languages whose script another Katha language shares, so the model could
# say them as the other one. Named in the style; no other language is, so
# what the A/B listeners heard is what those parents hear.
_LANGUAGE_HINTS: dict[str, str] = {"mr-IN": "Marathi", "as-IN": "Assamese"}

# Sarvam's cap, kept so a runaway reply cannot become a minutes-long voice
# note. Gemini itself accepts 8,192 input tokens.
_MAX_TEXT_CHARS = 2500

# Unary, not streamed: a whole reply's audio comes back in one response.
# Generous against the few seconds a clip takes; replace with a measured
# figure after the first production week (GEMINI_TTS.md §6 stage 2).
_TIMEOUT_SECONDS = 45.0

VOICES = voices.GEMINI_VOICES


def style_for(language_code: str) -> str:
    hint = _LANGUAGE_HINTS.get(language_code)
    return f"{STYLE}; speaking {hint}" if hint else STYLE


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


def request_body(text: str, voice: str, style: str, model: str = MODEL) -> dict:
    return {
        "model": model,
        "store": False,
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


async def _request(
    text: str, voice: str, style: str, purpose: str, model: str
) -> bytes:
    """One billed call. Returns WAV bytes."""
    if not settings.GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY is not set in backend/.env")
    started = time.monotonic()
    async with httpx.AsyncClient(timeout=_TIMEOUT_SECONDS) as client:
        response = await post_with_backoff(
            client,
            _URL,
            headers={
                "x-goog-api-key": settings.GEMINI_API_KEY,
                "Content-Type": "application/json",
            },
            json=request_body(text, voice, style, model),
        )
    if response.status_code != 200:
        raise RuntimeError(
            f"Gemini TTS error {response.status_code}: {response.text[:500]}"
        )
    audio = _audio_from(response.json())
    wav = audio if audio[:4] == b"RIFF" else pcm_to_wav(audio)
    seconds = wav_seconds(wav)
    logger.info(
        "Gemini TTS: %s %s, %d chars -> %.1f s of audio in %.2f s",
        model,
        voice,
        len(text),
        seconds,
        time.monotonic() - started,
    )
    # Google bills audio by the second and text by the token; the ledger
    # keeps the counts and core/pricing.py turns them into rupees.
    await usage.record(
        "google",
        purpose,
        {
            "audio_seconds": round(seconds, 3),
            "input_characters": len(text) + len(style),
        },
        model=model,
    )
    return wav


async def synthesize(
    text: str,
    language_code: str,
    *,
    gender: str | None,
    purpose: str,
    style: str | None = None,
    model: str = MODEL,
    voice: str | None = None,
) -> bytes:
    """
    Speak `text` in Gemini. Returns WAV bytes.

    `voice` is a pinned speaker (media/tts.py passes the parent's); without
    it the persona's voice from `gender` is used. `style` defaults to
    style_for(language_code).

    Emoji are removed and the text capped, as for Sarvam. A clip
    media/tts_check.py rejects is synthesised again once; if that fails too
    it is returned anyway with an error logged, as Sarvam's last attempt
    is. An HTTP error after the retries raises, and media/tts.py then says
    the reply in the persona's Sarvam voice (D3).
    """
    if model not in MODELS:
        raise ValueError(f"unknown Gemini TTS model {model!r}")
    voice = voice or VOICES[voices.normalise_gender(gender)]
    style = style if style is not None else style_for(language_code)
    text = speakable(text)
    if len(text) > _MAX_TEXT_CHARS:
        logger.warning(
            "Gemini TTS input truncated from %d to %d chars",
            len(text),
            _MAX_TEXT_CHARS,
        )
        text = text[:_MAX_TEXT_CHARS]
    wav = b""
    for attempt in (1, 2):
        wav = await _request(text, voice, style, purpose, model)
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
