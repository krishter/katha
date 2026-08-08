from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select

from adapters import llm, sarvam_stt, sarvam_tts
from adapters.llm import Message
from adapters.whatsapp_stub import get_whatsapp_adapter
from core import conversation_policy, session_manager
from core.session_manager import SessionState
from extraction import entity_extractor, story_extractor
from media import storage
from memory import fact_store, vector_store
from memory_cards import generator as memory_card_generator
from memory_cards.generator import MemoryCardResult
from models.memory_card import MemoryCard
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import PriorContext, UserProfile, build_system_prompt

logger = logging.getLogger(__name__)

_RESPONSE_RE = re.compile(r"<response>(.*?)</response>", re.DOTALL)
_EXTRACTION_RE = re.compile(r"<extraction>(.*?)</extraction>", re.DOTALL)

_EMPTY_EXTRACTION: dict = {
    "story_atoms": [],
    "named_entities": {},
    "significant_people": [],
    "themes": [],
    "energy_signal": "high",
    "gaps_remaining": [],
    "session_end_suggested": False,
}


@dataclass
class TurnResult:
    response_audio: bytes
    response_text: str
    extraction_json: dict
    transcript: str
    detected_language: str
    session_state: SessionState
    crisis_detected: bool
    response_mime_type: str = field(default="audio/x-wav")


def _parse_llm_output(raw: str) -> tuple[str, dict]:
    """Extract <response> text and <extraction> JSON from raw LLM output."""
    response_match = _RESPONSE_RE.search(raw)
    extraction_match = _EXTRACTION_RE.search(raw)

    response_text = response_match.group(1).strip() if response_match else raw.strip()

    extraction_json = dict(_EMPTY_EXTRACTION)
    if extraction_match:
        try:
            extraction_json = json.loads(extraction_match.group(1).strip())
        except json.JSONDecodeError:
            logger.warning("Failed to parse extraction JSON; using empty defaults")

    return response_text, extraction_json


def _extract_open_threads(recent_atoms: list) -> list[str]:
    """Aggregate open_threads from a list of StoryAtom objects."""
    threads: list[str] = []
    seen: set[str] = set()
    for atom in recent_atoms:
        for thread in getattr(atom, "open_threads", None) or []:
            if thread not in seen:
                threads.append(thread)
                seen.add(thread)
    return threads


async def build_prior_context(user_id: str, domain: str, db) -> PriorContext:
    """Query fact store and vector store to build real prior context."""
    facts = await fact_store.get_facts(user_id, db)
    recent_atoms = await vector_store.retrieve_relevant(user_id, domain, top_k=5, db=db)
    significant_people = await fact_store.get_significant_people(user_id, db)
    open_threads = _extract_open_threads(recent_atoms)
    return PriorContext(
        facts=facts,
        recent_stories=[
            getattr(a, "verbatim_quote", None)
            or (a.narrative[:200] if hasattr(a, "narrative") else "")
            for a in recent_atoms
        ],
        open_threads=open_threads,
        significant_people=significant_people,
    )


async def _load_session_transcript(session_id: str, db) -> str:
    """Concatenate every turn's transcript for this session, in order."""
    result = await db.execute(
        select(Turn.transcript)
        .where(Turn.session_id == uuid.UUID(session_id))
        .order_by(Turn.turn_number)
    )
    return "\n".join(result.scalars().all())


async def run_post_session(session_id: str, user_id: str, db) -> None:
    """
    Background task triggered after session close. Story atoms were already
    persisted per-turn as they happened (see process_voice_turn); this only
    handles the work that genuinely needs the whole session: entity
    extraction over the full concatenated transcript.
    Logs exceptions — never raises (voice turns were already delivered).
    """
    try:
        transcript = await _load_session_transcript(session_id, db)
        await entity_extractor.extract_entities(transcript, user_id, db)
    except Exception:
        logger.exception(
            "Post-session entity extraction failed for session %s", session_id
        )


async def convert_wav_to_ogg(wav_bytes: bytes) -> bytes:
    """
    Convert WAV bytes to OGG/Opus using ffmpeg subprocess.
    Required because Sarvam TTS returns WAV but WhatsApp expects OGG/Opus.
    ffmpeg must be available in the environment (installed via apt / Docker).
    """
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-c:a",
        "libopus",
        "-f",
        "ogg",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=wav_bytes)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg conversion failed: {stderr.decode()[:200]}")
    return stdout


async def get_user_profile(user_id: str, db) -> Optional[UserProfileModel]:
    """Load the full user profile row, including family_whatsapp_number."""
    result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    return result.scalar_one_or_none()


async def save_memory_card(
    session_id: str,
    user_id: str,
    card_result: MemoryCardResult,
    s3_key: str,
    public_url: str,
    message_sid: Optional[str],
    db,
) -> None:
    """Persist a MemoryCard row for a generated and delivered card."""
    card = MemoryCard(
        session_id=uuid.UUID(session_id),
        user_id=user_id,
        story_atom_id=(
            uuid.UUID(card_result.story_atom_id) if card_result.story_atom_id else None
        ),
        verbatim_quote=card_result.verbatim_quote,
        domain=card_result.domain,
        image_s3_key=s3_key,
        image_public_url=public_url,
        delivered_at=datetime.now(timezone.utc) if message_sid else None,
        twilio_message_sid=message_sid,
    )
    db.add(card)
    await db.commit()


async def _generate_and_deliver_memory_card(session_id: str, user_id: str, db) -> None:
    """
    Generate a memory card from the session's best story atom and deliver
    it to the adult child's WhatsApp. No-ops (with a warning log) if there's
    no usable quote or no family_whatsapp_number on file — not every session
    produces a card-worthy quote.
    """
    user_profile = await get_user_profile(user_id, db)
    if user_profile is None:
        logger.warning("No user_profile found for %s — skipping memory card", user_id)
        return

    card_result = await memory_card_generator.generate_memory_card(
        session_id, user_id, user_profile.name, db
    )
    if not card_result:
        logger.warning(
            "No story atoms with quotes in session %s — no card generated", session_id
        )
        return

    if not user_profile.family_whatsapp_number:
        logger.warning(
            "No family_whatsapp_number set for user %s — card not delivered", user_id
        )
        return

    s3_key = f"cards/{session_id}.png"
    public_url = await storage.upload_media(
        card_result.image_bytes, s3_key, content_type="image/png"
    )

    caption = f"A memory from today's conversation with {user_profile.name} \U0001f338"
    whatsapp = get_whatsapp_adapter()
    message_sid = await whatsapp.send_image(
        to_number=user_profile.family_whatsapp_number,
        image_bytes=card_result.image_bytes,
        caption=caption,
    )

    await save_memory_card(
        session_id=session_id,
        user_id=user_id,
        card_result=card_result,
        s3_key=s3_key,
        public_url=public_url,
        message_sid=message_sid,
        db=db,
    )
    logger.info("Memory card delivered for session %s: %s", session_id, message_sid)


async def close_and_process_session(
    session_id: str,
    db,
) -> None:
    """
    Background task. Called when a session ends via the webhook or the
    /conversation/close endpoint. This is the single entry point for session
    close — marks the session completed, runs post-session entity
    extraction, then generates and delivers a memory card.
    """
    try:
        state = await session_manager.get_session(session_id, db)
        ended_reason = (
            "goal_met"
            if state.goal_met
            else "llm_suggested"
            if state.session_end_suggested
            else "manual"
        )
        await session_manager.close_session(session_id, ended_reason, db)
        await run_post_session(session_id, state.user_id, db)
        logger.info("Session %s closed and processed", session_id)
    except Exception:
        logger.exception("close_and_process_session failed for session %s", session_id)
        return

    try:
        await _generate_and_deliver_memory_card(session_id, state.user_id, db)
    except Exception:
        logger.exception(
            "Memory card generation/delivery failed for session %s", session_id
        )


async def _persist_turn(
    session_id: str,
    user_id: str,
    turn_number: int,
    inbound_message_sid: Optional[str],
    transcript: str,
    detected_language: str,
    response_text: str,
    extraction_json: dict,
    input_tokens: int,
    output_tokens: int,
    db,
) -> Turn:
    """
    Persist the turn's transcript, response, and extraction JSON. Called
    after the LLM call and before TTS, so a TTS/audio-conversion failure
    downstream can never lose what the user just told Katha.
    """
    turn = Turn(
        session_id=uuid.UUID(session_id),
        user_id=user_id,
        turn_number=turn_number,
        inbound_message_sid=inbound_message_sid,
        transcript=transcript,
        detected_language=detected_language,
        response_text=response_text,
        extraction_json=extraction_json,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
    )
    db.add(turn)
    await db.commit()
    await db.refresh(turn)
    return turn


async def process_voice_turn(
    audio_bytes: bytes,
    session_id: str,
    user_profile: UserProfile,
    db,
    inbound_message_sid: Optional[str] = None,
) -> TurnResult:
    """
    Full pipeline:
    session → STT → pre-policy → prior context → system prompt → LLM
    → post-policy → parse → persist turn → persist story atoms
    → update session → TTS
    Session close (when the session ends) is scheduled by the caller
    (the webhook), not from here — see close_and_process_session.
    """
    # 1. Load session state
    state = await session_manager.get_session(session_id, db)
    logger.info(
        "Loaded session %s: domain=%s exchange=%d",
        session_id,
        state.domain,
        state.exchange_count,
    )

    # 2. Transcribe
    logger.info("STT: transcribing %d bytes", len(audio_bytes))
    stt_result = await sarvam_stt.transcribe(audio_bytes)
    logger.info(
        "STT: transcript=%r language=%s",
        stt_result.transcript,
        stt_result.language_code,
    )

    # 3. Pre-turn policy check
    pre_check = conversation_policy.check_pre_turn(stt_result.transcript, state)
    if not pre_check.allowed:
        logger.warning(
            "Pre-turn policy blocked: crisis_detected=%s", pre_check.crisis_detected
        )
        audio_out = await sarvam_tts.synthesize(
            pre_check.override_response,  # type: ignore[arg-type]
            language_code=stt_result.language_code,
        )
        audio_out = await convert_wav_to_ogg(audio_out)
        return TurnResult(
            response_audio=audio_out,
            response_text=pre_check.override_response or "",
            extraction_json=dict(_EMPTY_EXTRACTION),
            transcript=stt_result.transcript,
            detected_language=stt_result.language_code,
            session_state=state,
            crisis_detected=pre_check.crisis_detected,
            response_mime_type="audio/ogg",
        )

    # 4. Build real prior context from fact store + vector store
    prior_context = await build_prior_context(state.user_id, state.domain, db)

    # 5. Build system prompt
    system_prompt = build_system_prompt(user_profile, state, prior_context)

    # 6. Build messages and call LLM
    messages = [Message(role="user", content=stt_result.transcript)]
    logger.info("LLM: calling with system prompt (%d chars)", len(system_prompt))
    llm_response = await llm.chat(messages, system=system_prompt)
    logger.info(
        "LLM: tokens in=%d out=%d",
        llm_response.input_tokens,
        llm_response.output_tokens,
    )

    # 7. Post-turn policy check
    post_check = conversation_policy.check_post_turn(llm_response.content, state)
    if not post_check.allowed:
        logger.warning("Post-turn policy blocked: malformed LLM response")
        audio_out = await sarvam_tts.synthesize(
            post_check.override_response,  # type: ignore[arg-type]
            language_code=stt_result.language_code,
        )
        audio_out = await convert_wav_to_ogg(audio_out)
        return TurnResult(
            response_audio=audio_out,
            response_text=post_check.override_response or "",
            extraction_json=dict(_EMPTY_EXTRACTION),
            transcript=stt_result.transcript,
            detected_language=stt_result.language_code,
            session_state=state,
            crisis_detected=False,
            response_mime_type="audio/ogg",
        )

    # 8. Parse dual output
    response_text, extraction_json = _parse_llm_output(llm_response.content)
    logger.info(
        "Extraction: energy=%s atoms=%d session_end=%s",
        extraction_json.get("energy_signal"),
        len(extraction_json.get("story_atoms", [])),
        extraction_json.get("session_end_suggested"),
    )

    # 9. Persist this turn — before TTS, so a TTS/ffmpeg failure downstream
    # can never lose the story the user just told (see C1/H5 in the review).
    turn_number = state.exchange_count + 1
    turn = await _persist_turn(
        session_id,
        state.user_id,
        turn_number,
        inbound_message_sid,
        stt_result.transcript,
        stt_result.language_code,
        response_text,
        extraction_json,
        llm_response.input_tokens,
        llm_response.output_tokens,
        db,
    )

    # 10. Persist this turn's story atoms now — not at session close, where
    # only the final (wind-down) turn's atoms used to survive (C1).
    await story_extractor.process_extraction(
        extraction_json, session_id, state.user_id, db, turn_id=turn.id
    )

    # 11. Update session — goal_met is computed from the cumulative atom
    # count just persisted above, so it must run after process_extraction.
    state = await session_manager.update_session(session_id, extraction_json, db)

    # 12. Synthesize
    logger.info("TTS: synthesizing in %s", stt_result.language_code)
    audio_out = await sarvam_tts.synthesize(
        response_text, language_code=stt_result.language_code
    )
    logger.info("TTS: produced %d bytes", len(audio_out))

    # Convert WAV → OGG/Opus for WhatsApp delivery
    audio_out = await convert_wav_to_ogg(audio_out)

    return TurnResult(
        response_audio=audio_out,
        response_text=response_text,
        extraction_json=extraction_json,
        transcript=stt_result.transcript,
        detected_language=stt_result.language_code,
        session_state=state,
        crisis_detected=False,
        response_mime_type="audio/ogg",
    )
