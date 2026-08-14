from __future__ import annotations

import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy import select

from adapters import llm, sarvam_stt, sarvam_tts
from adapters.llm import Message
from adapters.whatsapp_stub import get_whatsapp_adapter
from core import conversation_policy, session_manager
from core.fallback_audio import FailureStage, get_fallback_audio, get_fallback_text
from core.session_manager import SessionState
from extraction import entity_extractor, story_extractor
from media import storage
from media.audio_convert import convert_wav_to_ogg
from memory import fact_store, vector_store
from memory_cards import generator as memory_card_generator
from memory_cards.generator import MemoryCardResult
from models.crisis_event import CrisisEvent
from models.memory_card import MemoryCard
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import (
    PriorContext,
    UserProfile,
    build_extraction_prompt,
    build_system_prompt,
)

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

_DIALOGUE_MAX_TOKENS = 300
_EXTRACTION_MAX_TOKENS = 2000
_EXTRACTION_RETRY_INSTRUCTION = (
    "\n\nIMPORTANT: Your previous reply did not match the required format. "
    "Return ONLY the <extraction>{...}</extraction> block with syntactically "
    "valid JSON inside — no other text, no markdown fences, nothing before "
    "or after it."
)


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
    # None when no Turn row was persisted for this result (STT/LLM total
    # failure, or the pre-turn crisis/malformed-response paths).
    turn_id: Optional[uuid.UUID] = field(default=None)


def _parse_response_only(raw: str) -> str:
    """Extract <response> text from the dialogue call's output."""
    match = _RESPONSE_RE.search(raw)
    return match.group(1).strip() if match else raw.strip()


def _parse_extraction_only(raw: str) -> dict:
    """Extract <extraction> JSON from the extraction call's output."""
    match = _EXTRACTION_RE.search(raw)
    if not match:
        return dict(_EMPTY_EXTRACTION)
    try:
        return json.loads(match.group(1).strip())
    except json.JSONDecodeError:
        logger.warning("Failed to parse extraction JSON; using empty defaults")
        return dict(_EMPTY_EXTRACTION)


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
    """
    Assemble what Katha already knows about this user, for Layer 3.

    The fact store is the load-bearing half: names, relationships, dates
    and places, straight from Postgres. Story-atom retrieval on top of it
    is enrichment — it supplies open threads to revisit.

    Retrieval is therefore wrapped and the fact store is not. Layer 3
    enrichment must never be able to take down a turn: a session that
    remembers the user's sister but forgets which threads were left open
    is coherent, whereas a raised exception here costs the user their
    whole reply. This held when retrieval was an OpenAI embedding call
    and it holds for the SQL query that replaced it (S1.5).
    """
    facts = await fact_store.get_facts(user_id, db)
    significant_people = await fact_store.get_significant_people(user_id, db)

    open_threads: list[str] = []
    try:
        recent_atoms = await vector_store.retrieve_relevant(
            user_id, domain, top_k=5, db=db
        )
        open_threads = _extract_open_threads(recent_atoms)
    except Exception:
        logger.warning(
            "Story-atom retrieval failed for user %s (domain %s) — continuing "
            "with facts and significant people only",
            user_id,
            domain,
            exc_info=True,
        )

    return PriorContext(
        facts=facts,
        open_threads=open_threads,
        significant_people=significant_people,
    )


async def _log_crisis_event(
    session_id: str,
    turn_id: Optional[uuid.UUID],
    source: str,
    matched_pattern: str,
    db,
) -> None:
    """
    Record a crisis detection so someone can review it during the pilot —
    a log line alone is not enough for content this sensitive.
    """
    event = CrisisEvent(
        session_id=uuid.UUID(session_id),
        turn_id=turn_id,
        source=source,
        matched_pattern=matched_pattern,
    )
    db.add(event)
    await db.commit()


async def _load_session_transcript(session_id: str, db) -> str:
    """Concatenate every turn's transcript for this session, in order."""
    result = await db.execute(
        select(Turn.transcript)
        .where(Turn.session_id == uuid.UUID(session_id))
        .order_by(Turn.turn_number)
    )
    return "\n".join(result.scalars().all())


async def _load_last_turn_messages(session_id: str, db) -> list[Message]:
    """
    Fetch the immediately preceding turn's raw exchange (transcript +
    Katha's reply), if any, as user/assistant messages to prepend to the
    dialogue call. Turn rows are written synchronously per turn (see
    _persist_turn) — this does not depend on the deferred extraction
    pipeline, so a story that unfolds over two turns doesn't lose
    continuity if extraction for the prior turn hasn't finished yet.
    """
    result = await db.execute(
        select(Turn.transcript, Turn.response_text)
        .where(Turn.session_id == uuid.UUID(session_id))
        .order_by(Turn.turn_number.desc())
        .limit(1)
    )
    row = result.first()
    if row is None:
        return []
    transcript, response_text = row
    return [
        Message(role="user", content=transcript),
        Message(role="assistant", content=response_text),
    ]


async def find_turn_by_message_sid(message_sid: str, db) -> Optional[Turn]:
    """
    Look up an already-processed turn by its inbound Twilio MessageSid —
    the idempotency check for Twilio's at-least-once delivery: it retries
    a webhook that didn't return a fast 200, and without this check that
    retry would be reprocessed as a brand new turn (H3).
    """
    result = await db.execute(
        select(Turn).where(Turn.inbound_message_sid == message_sid)
    )
    return result.scalar_one_or_none()


async def run_post_session(session_id: str, user_id: str, db) -> None:
    """
    Background task triggered after session close. Story atoms were already
    persisted per-turn as they happened; this only handles the work that
    genuinely needs the whole session: entity extraction over the full
    concatenated transcript.
    Logs exceptions — never raises (voice turns were already delivered).
    """
    try:
        transcript = await _load_session_transcript(session_id, db)
        await entity_extractor.extract_entities(transcript, user_id, db)
    except Exception:
        logger.exception(
            "Post-session entity extraction failed for session %s", session_id
        )


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
    message_sid: Optional[str],
    db,
) -> None:
    """Persist a MemoryCard row for a generated and delivered card. Stores
    only the S3 key — the dashboard generates a short-lived presigned URL
    on demand (see api/routes/family.py); no permanent public URL exists."""
    card = MemoryCard(
        session_id=uuid.UUID(session_id),
        user_id=user_id,
        story_atom_id=(
            uuid.UUID(card_result.story_atom_id) if card_result.story_atom_id else None
        ),
        verbatim_quote=card_result.verbatim_quote,
        domain=card_result.domain,
        image_s3_key=s3_key,
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
    await storage.upload_media(
        card_result.image_bytes, s3_key, content_type="image/png"
    )

    caption = f"A memory from today's conversation with {user_profile.name} \U0001f338"
    whatsapp = get_whatsapp_adapter()
    # Hand over the key just uploaded rather than the bytes — the adapter
    # presigns it instead of uploading a second, untracked copy (S2.4b).
    message_sid = await whatsapp.send_image(
        to_number=user_profile.family_whatsapp_number,
        s3_key=s3_key,
        caption=caption,
    )

    await save_memory_card(
        session_id=session_id,
        user_id=user_id,
        card_result=card_result,
        s3_key=s3_key,
        message_sid=message_sid,
        db=db,
    )
    logger.info("Memory card delivered for session %s: %s", session_id, message_sid)


async def close_and_process_session(
    session_id: str,
    db,
) -> None:
    """
    Called when a session ends. This is the single entry point for session
    close — marks the session completed, runs post-session entity
    extraction, then generates and delivers a memory card. Triggered from
    run_extraction_for_turn, since that's what learns
    session_end_suggested/goal_met.
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
    Persist the turn's transcript and response. Called after the dialogue
    LLM call and before TTS, so a TTS/audio-conversion failure downstream
    can never lose what the user just told Katha. extraction_json starts as
    a placeholder — the real structured extraction is a separate, slower
    call that fills it in afterward (see run_extraction_for_turn).
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


async def set_turn_audio_key(turn_id: uuid.UUID, s3_key: str, db) -> None:
    """
    Record the S3 key of the voice note actually sent for this turn. The
    send happens at the webhook layer, after the turn is already
    committed — this lets the deletion sweep find and remove it later
    (every uploaded object must be enumerable, per the DPDP review's P4).
    """
    result = await db.execute(select(Turn).where(Turn.id == turn_id))
    turn = result.scalar_one_or_none()
    if turn is None:
        logger.warning("set_turn_audio_key: turn %s not found", turn_id)
        return
    turn.response_audio_s3_key = s3_key
    db.add(turn)
    await db.commit()


async def _synthesize_and_convert(text: str, language_code: str) -> bytes:
    """TTS then OGG conversion — the two steps that must succeed together
    for a voice note to go out, or degrade to text (see _send_or_degrade)."""
    audio_out = await sarvam_tts.synthesize(text, language_code=language_code)
    return await convert_wav_to_ogg(audio_out)


async def _send_or_degrade(
    response_text: str,
    detected_language: str,
    transcript: str,
    session_state: SessionState,
    extraction_json: dict,
    crisis_detected: bool,
    turn_id: Optional[uuid.UUID] = None,
) -> TurnResult:
    """
    Try to synthesize voice for response_text. If TTS or the WAV->OGG
    conversion fails, degrade the channel (send text) rather than the
    response — the user still gets the same words, just typed instead of
    spoken (P2: never silence).
    """
    try:
        audio_out = await _synthesize_and_convert(response_text, detected_language)
        return TurnResult(
            response_audio=audio_out,
            response_text=response_text,
            extraction_json=extraction_json,
            transcript=transcript,
            detected_language=detected_language,
            session_state=session_state,
            crisis_detected=crisis_detected,
            response_mime_type="audio/ogg",
            turn_id=turn_id,
        )
    except Exception:
        logger.error(
            "TTS/audio-conversion failed for session %s turn %d — degrading to text",
            session_state.session_id,
            session_state.exchange_count,
            exc_info=True,
        )
        return TurnResult(
            response_audio=b"",
            response_text=response_text,
            extraction_json=extraction_json,
            transcript=transcript,
            detected_language=detected_language,
            session_state=session_state,
            crisis_detected=crisis_detected,
            response_mime_type="text/plain",
            turn_id=turn_id,
        )


async def _fallback_turn_result(
    stage: FailureStage,
    session_state: SessionState,
    transcript: str,
    detected_language: str,
) -> TurnResult:
    """
    Build the reply for an STT or LLM failure using pre-synthesized audio —
    never a live TTS call, since TTS may be down too. Degrades to text if
    even the pre-synthesized clip is unavailable.
    """
    text = get_fallback_text(stage)
    audio = get_fallback_audio(stage, detected_language)
    if audio is not None:
        return TurnResult(
            response_audio=audio,
            response_text=text,
            extraction_json=dict(_EMPTY_EXTRACTION),
            transcript=transcript,
            detected_language=detected_language,
            session_state=session_state,
            crisis_detected=False,
            response_mime_type="audio/ogg",
        )
    logger.error(
        "No pre-synthesized fallback audio available for stage=%s language=%s "
        "— degrading to text",
        stage.value,
        detected_language,
    )
    return TurnResult(
        response_audio=b"",
        response_text=text,
        extraction_json=dict(_EMPTY_EXTRACTION),
        transcript=transcript,
        detected_language=detected_language,
        session_state=session_state,
        crisis_detected=False,
        response_mime_type="text/plain",
    )


async def process_voice_turn(
    audio_bytes: bytes,
    session_id: str,
    user_profile: UserProfile,
    db,
    inbound_message_sid: Optional[str] = None,
    background_tasks: BackgroundTasks | None = None,
) -> TurnResult:
    """
    session → STT → pre-policy → prior context → dialogue LLM call
    → post-policy → crisis-on-response check → record turn → persist turn
    → schedule extraction (background) → TTS

    The dialogue call is the only LLM call on the critical path (fast,
    max_tokens=300, <response> only). Structured extraction — story atoms,
    themes, energy_signal, session_end_suggested — runs as a separate,
    latency-tolerant background call; see run_extraction_for_turn, which is
    also the sole trigger point for close_and_process_session.

    Every external-call stage is wrapped so a failure there produces a
    reply, never silence (P2) — see _fallback_turn_result/_send_or_degrade.
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
    try:
        stt_result = await sarvam_stt.transcribe(audio_bytes)
    except Exception:
        logger.error(
            "STT failed for session %s turn %d",
            session_id,
            state.exchange_count + 1,
            exc_info=True,
        )
        return await _fallback_turn_result(
            FailureStage.STT,
            state,
            transcript="",
            detected_language=user_profile.preferred_language,
        )
    logger.info(
        "STT: transcript=%r language=%s",
        stt_result.transcript,
        stt_result.language_code,
    )

    # 3. Pre-turn crisis check
    pre_check = conversation_policy.check_pre_turn(stt_result.transcript, state)
    if not pre_check.allowed:
        logger.warning(
            "Pre-turn policy blocked: crisis_detected=%s", pre_check.crisis_detected
        )
        await _log_crisis_event(
            session_id, None, "user_transcript", pre_check.matched_pattern or "", db
        )
        return await _send_or_degrade(
            pre_check.override_response or "",
            stt_result.language_code,
            stt_result.transcript,
            state,
            dict(_EMPTY_EXTRACTION),
            crisis_detected=True,
        )

    # 4. Build real prior context from fact store + vector store
    prior_context = await build_prior_context(state.user_id, state.domain, db)

    # 5. Build dialogue prompt and call the dialogue LLM. The immediately
    # preceding turn's raw exchange is included directly (not just via the
    # deferred fact/vector-store pipeline) so a story that unfolds over two
    # turns keeps continuity even if that turn's extraction hasn't run yet.
    dialogue_prompt = build_system_prompt(user_profile, state, prior_context)
    messages = await _load_last_turn_messages(session_id, db)
    messages.append(Message(role="user", content=stt_result.transcript))
    try:
        llm_response = await llm.chat(
            messages, system=dialogue_prompt, max_tokens=_DIALOGUE_MAX_TOKENS
        )
    except Exception:
        logger.error(
            "Dialogue LLM call failed for session %s turn %d",
            session_id,
            state.exchange_count + 1,
            exc_info=True,
        )
        return await _fallback_turn_result(
            FailureStage.LLM, state, stt_result.transcript, stt_result.language_code
        )
    logger.info(
        "LLM: tokens in=%d out=%d",
        llm_response.input_tokens,
        llm_response.output_tokens,
    )

    # 6. Post-turn policy check (malformed dialogue response)
    post_check = conversation_policy.check_post_turn(llm_response.content, state)
    if post_check.salvaged_untagged:
        logger.warning(
            "Dialogue reply arrived without its <response> wrapper — salvaged "
            "the bare text rather than discarding it"
        )
    if not post_check.allowed:
        logger.warning("Post-turn policy blocked: malformed LLM response")
        return await _send_or_degrade(
            post_check.override_response or "",
            stt_result.language_code,
            stt_result.transcript,
            state,
            dict(_EMPTY_EXTRACTION),
            crisis_detected=False,
        )

    # 7. Parse the dialogue response
    response_text = _parse_response_only(llm_response.content)

    # 8. Crisis check on Katha's own generated reply — a safety net
    # independent of what the user said.
    response_crisis = conversation_policy.check_response_for_crisis(response_text)
    crisis_detected = response_crisis.crisis_detected
    if crisis_detected:
        logger.warning("Katha's own response matched a crisis pattern — overriding")
        response_text = response_crisis.override_response or response_text

    # 9. Record the turn happened (exchange_count++). energy_signal,
    # session_end_suggested, and goal_met are updated later by
    # run_extraction_for_turn once structured extraction completes.
    state = await session_manager.record_turn(session_id, db)

    # 10. Persist this turn — before TTS, so a TTS/ffmpeg failure downstream
    # can never lose the story the user just told (see C1/H5 in the review).
    turn = await _persist_turn(
        session_id,
        state.user_id,
        state.exchange_count,
        inbound_message_sid,
        stt_result.transcript,
        stt_result.language_code,
        response_text,
        dict(_EMPTY_EXTRACTION),
        llm_response.input_tokens,
        llm_response.output_tokens,
        db,
    )

    if crisis_detected:
        await _log_crisis_event(
            session_id,
            turn.id,
            "assistant_response",
            response_crisis.matched_pattern or "",
            db,
        )

    # 11. Schedule structured extraction off the critical path.
    if background_tasks is not None:
        background_tasks.add_task(
            run_extraction_for_turn,
            turn.id,
            session_id,
            state,
            user_profile,
            prior_context,
            stt_result.transcript,
            response_text,
            db,
        )

    # 12. Synthesize the reply (or degrade to text if TTS/conversion fails).
    return await _send_or_degrade(
        response_text,
        stt_result.language_code,
        stt_result.transcript,
        state,
        dict(_EMPTY_EXTRACTION),
        crisis_detected=crisis_detected,
        turn_id=turn.id,
    )


async def _call_extraction_llm(prompt: str) -> tuple[dict, int, int]:
    """
    Call the extraction LLM. If the response is malformed, retry once with
    a stricter instruction before giving up and returning an empty
    extraction — this call is off the critical path, so a retry costs
    latency the user never sees.
    """
    llm_response = await llm.chat(
        [Message(role="user", content=prompt)],
        system=None,
        max_tokens=_EXTRACTION_MAX_TOKENS,
    )
    if conversation_policy.check_extraction_response(llm_response.content):
        return (
            _parse_extraction_only(llm_response.content),
            llm_response.input_tokens,
            llm_response.output_tokens,
        )

    logger.warning("Extraction call malformed — retrying once with a stricter prompt")
    retry_response = await llm.chat(
        [Message(role="user", content=prompt + _EXTRACTION_RETRY_INSTRUCTION)],
        system=None,
        max_tokens=_EXTRACTION_MAX_TOKENS,
    )
    total_input = llm_response.input_tokens + retry_response.input_tokens
    total_output = llm_response.output_tokens + retry_response.output_tokens
    if conversation_policy.check_extraction_response(retry_response.content):
        return (
            _parse_extraction_only(retry_response.content),
            total_input,
            total_output,
        )

    logger.error("Extraction call failed validation twice — giving up on this turn")
    return dict(_EMPTY_EXTRACTION), total_input, total_output


async def run_extraction_for_turn(
    turn_id: uuid.UUID,
    session_id: str,
    session_state: SessionState,
    user_profile: UserProfile,
    prior_context: PriorContext,
    transcript: str,
    response_text: str,
    db,
) -> None:
    """
    Background task: the structured-extraction half of a turn, decoupled
    from the dialogue reply so a long, detailed story is never truncated by
    a token budget sized for a warm two-sentence reply (C5). Persists story
    atoms, updates the turn's extraction_json, applies energy/session-end/
    goal_met to the session, and — since this is the only place that learns
    those signals — is the sole trigger for close_and_process_session.
    Never raises: the reply was already sent.
    """
    try:
        extraction_prompt = build_extraction_prompt(
            user_profile, session_state, prior_context, transcript, response_text
        )
        extraction_json, input_tokens, output_tokens = await _call_extraction_llm(
            extraction_prompt
        )

        await story_extractor.process_extraction(
            extraction_json, session_id, session_state.user_id, db, turn_id=turn_id
        )

        turn_result = await db.execute(select(Turn).where(Turn.id == turn_id))
        turn = turn_result.scalar_one_or_none()
        if turn is not None:
            turn.extraction_json = extraction_json
            turn.input_tokens = (turn.input_tokens or 0) + input_tokens
            turn.output_tokens = (turn.output_tokens or 0) + output_tokens
            db.add(turn)
            await db.commit()

        new_state = await session_manager.apply_extraction(
            session_id, extraction_json, db
        )
    except Exception:
        logger.exception(
            "Extraction failed for turn %s (session %s)", turn_id, session_id
        )
        return

    should_close = session_manager.should_end_session(
        new_state, goal_met_before_this_turn=session_state.goal_met
    )
    if new_state.goal_met and not session_state.goal_met and not should_close:
        logger.info(
            "Session %s just reached its domain goal — deferring close to "
            "the next turn's closing exchange",
            session_id,
        )
    if should_close:
        await close_and_process_session(session_id, db)
