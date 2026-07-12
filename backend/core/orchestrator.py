from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from fastapi import BackgroundTasks

from adapters import llm, sarvam_stt, sarvam_tts
from adapters.llm import Message
from core import conversation_policy, session_manager
from core.session_manager import SessionState
from extraction import entity_extractor, story_extractor
from memory import fact_store, vector_store
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


async def run_post_session(
    extraction_json: dict,
    transcript: str,
    session_id: str,
    user_id: str,
    db,
) -> None:
    """
    Background task triggered after session close.
    Runs story extraction and entity extraction sequentially.
    Logs exceptions — never raises (voice turn already delivered).
    """
    try:
        await story_extractor.process_extraction(
            extraction_json, session_id, user_id, db
        )
    except Exception:
        logger.exception(
            "Post-session story extraction failed for session %s", session_id
        )

    try:
        await entity_extractor.extract_entities(transcript, user_id, db)
    except Exception:
        logger.exception(
            "Post-session entity extraction failed for session %s", session_id
        )


async def process_voice_turn(
    audio_bytes: bytes,
    session_id: str,
    user_profile: UserProfile,
    db,
    background_tasks: BackgroundTasks | None = None,
) -> TurnResult:
    """
    Full Phase 3 pipeline:
    session → STT → pre-policy → prior context → system prompt → LLM
    → post-policy → parse → session update → TTS
    → schedule post-session (if session end)
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
        return TurnResult(
            response_audio=audio_out,
            response_text=pre_check.override_response or "",
            extraction_json=dict(_EMPTY_EXTRACTION),
            transcript=stt_result.transcript,
            detected_language=stt_result.language_code,
            session_state=state,
            crisis_detected=pre_check.crisis_detected,
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
        return TurnResult(
            response_audio=audio_out,
            response_text=post_check.override_response or "",
            extraction_json=dict(_EMPTY_EXTRACTION),
            transcript=stt_result.transcript,
            detected_language=stt_result.language_code,
            session_state=state,
            crisis_detected=False,
        )

    # 8. Parse dual output
    response_text, extraction_json = _parse_llm_output(llm_response.content)
    logger.info(
        "Extraction: energy=%s atoms=%d session_end=%s",
        extraction_json.get("energy_signal"),
        len(extraction_json.get("story_atoms", [])),
        extraction_json.get("session_end_suggested"),
    )

    # 9. Update session
    state = await session_manager.update_session(session_id, extraction_json, db)

    # 10. Synthesize
    logger.info("TTS: synthesizing in %s", stt_result.language_code)
    audio_out = await sarvam_tts.synthesize(
        response_text, language_code=stt_result.language_code
    )
    logger.info("TTS: produced %d bytes", len(audio_out))

    # 11. Schedule post-session processing if session ended
    session_ended = extraction_json.get(
        "session_end_suggested"
    ) or session_manager.should_end_session(state)
    if session_ended:
        if background_tasks is not None:
            background_tasks.add_task(
                run_post_session,
                extraction_json,
                stt_result.transcript,
                session_id,
                state.user_id,
                db,
            )
            logger.info("Scheduled post-session processing for %s", session_id)

    return TurnResult(
        response_audio=audio_out,
        response_text=response_text,
        extraction_json=extraction_json,
        transcript=stt_result.transcript,
        detected_language=stt_result.language_code,
        session_state=state,
        crisis_detected=False,
    )
