import logging
from dataclasses import dataclass

from adapters import llm, sarvam_stt, sarvam_tts
from adapters.llm import Message

logger = logging.getLogger(__name__)


@dataclass
class TurnResult:
    response_audio: bytes
    response_text: str
    transcript: str
    detected_language: str


async def process_voice_turn(audio_bytes: bytes) -> TurnResult:
    """
    Full pipeline: audio → STT → LLM → TTS → audio
    Phase 1: no session context, no system prompt, no extraction.
    """
    logger.info("STT: transcribing audio (%d bytes)", len(audio_bytes))
    stt_result = await sarvam_stt.transcribe(audio_bytes)
    logger.info(
        "STT: transcript=%r language=%s probability=%.2f",
        stt_result.transcript,
        stt_result.language_code,
        stt_result.language_probability,
    )

    messages = [Message(role="user", content=stt_result.transcript)]
    logger.info("LLM: sending %d message(s)", len(messages))
    llm_response = await llm.chat(messages)
    logger.info(
        "LLM: response=%r input_tokens=%d output_tokens=%d",
        llm_response.content[:80],
        llm_response.input_tokens,
        llm_response.output_tokens,
    )

    logger.info("TTS: synthesizing response in %s", stt_result.language_code)
    audio_out = await sarvam_tts.synthesize(
        llm_response.content,
        language_code=stt_result.language_code,
    )
    logger.info("TTS: produced %d bytes of audio", len(audio_out))

    return TurnResult(
        response_audio=audio_out,
        response_text=llm_response.content,
        transcript=stt_result.transcript,
        detected_language=stt_result.language_code,
    )
