from __future__ import annotations

import dataclasses
import json
import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from fastapi import BackgroundTasks
from sqlalchemy import select, update

import cards
from adapters import llm, sarvam_stt
from adapters.llm import Message
from adapters.whatsapp_stub import get_whatsapp_adapter
from cards import asks as card_asks
from cards import privacy as card_privacy
from cards import write as card_writer
from config import settings
from core import (
    conversation_policy,
    family_number,
    parent_consent,
    session_manager,
    usage,
)
from core.fallback_audio import FailureStage, get_fallback_audio, get_fallback_text
from core.session_manager import SessionState
from extraction import entity_extractor, story_extractor
from media import storage, tts
from media.audio_convert import convert_wav_to_ogg
from memory import fact_store, vector_store
from memory_cards import generator as memory_card_generator
from memory_cards.generator import MemoryCardResult
from models.crisis_event import CrisisEvent
from models.memory_card import MemoryCard
from models.session import Session
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.domains import domain_label
from prompts.system_prompt import (
    PriorContext,
    UserProfile,
    build_extraction_prompt,
    build_system_prompt_parts,
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

# How many story atoms Layer 3 retrieval fetches. Retrieval is ordered by
# recency, so this is the window within which older domains stay visible at
# all — at the previous value of 5, a user with more than five atoms spread
# across domains lost the oldest ones entirely, which at a daily-session,
# 8-domain cadence arrives around session 5-6 rather than the "session 100"
# an earlier decision record claimed.
#
# 25 covers all 8 domains at the ~3 atoms each the interview framework
# targets (TECH_DESIGN 2.2), with headroom. Fetching wide is cheap — it is
# one indexed query, and _MAX_RENDERED_THREADS bounds what actually reaches
# the prompt. See docs/proposals/embedding-strategy.md.
_RETRIEVAL_TOP_K = 25

# Sized from measurement, not from how long a reply reads in English.
# A warm 2-4 sentence reply in Devanagari costs 165-300 output tokens
# (Indic scripts tokenize far more expensively than Latin), and the model
# sometimes spends another 400-600 thinking out loud first. At the previous
# value of 300 that put a long Hindi turn over the ceiling roughly 40% of
# the time, and every such turn reached the parent as either "I lost my
# train of thought" or a reply cut off mid-word.
#
# The cap only costs latency when the model actually generates into it, so
# headroom here is close to free; what keeps replies short is Layer 5's
# length instruction, not this number.
#
# Raised for the 5.5 models (COST_TO_SERVE.md §6): thinking counts toward
# max_tokens, and their tokenizer yields about 30% more tokens for the same
# text. Extraction on Haiku thinks adaptively, so it gets the most room.
_DIALOGUE_MAX_TOKENS = 1600
_EXTRACTION_MAX_TOKENS = 4000
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
    """
    Extract <response> text from the dialogue call's output.

    Reasoning blocks are stripped first. Without that, an output with no
    <response> tags falls through to "return everything", which on a reply
    that thought out loud means handing back Katha's own English analysis
    of the user as if it were her reply to them.

    process_voice_turn does not use this — it takes the text
    conversation_policy.check_post_turn decided on, which also handles
    truncation. This remains for callers that bypass the policy (the eval
    runner).
    """
    cleaned = conversation_policy.strip_reasoning(raw)
    match = _RESPONSE_RE.search(cleaned)
    return match.group(1).strip() if match else cleaned.strip()


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
    """
    Aggregate open_threads from story atoms, ordered breadth-first across
    domains rather than atom by atom.

    Layer 3 renders only the first _MAX_RENDERED_THREADS of these, so this
    ordering decides what survives the cut. Taking them atom by atom meant
    one talkative session's domain could fill the whole budget — a user who
    spent yesterday on childhood would get eight childhood threads and
    nothing from the other seven domains, which is the same starvation
    S2.5 is fixing one layer down. Round-robin instead: one thread from
    each domain, then a second from each, and so on.

    Order within a domain is preserved, and atoms arrive newest-first, so
    the first thread offered per domain is its most recent.
    """
    by_domain: dict[str, list[str]] = {}
    seen: set[str] = set()

    for atom in recent_atoms:
        domain = getattr(atom, "domain", None) or "unknown"
        for thread in getattr(atom, "open_threads", None) or []:
            if thread in seen:
                continue
            seen.add(thread)
            by_domain.setdefault(domain, []).append(thread)

    threads: list[str] = []
    for tier in range(max((len(v) for v in by_domain.values()), default=0)):
        for domain_threads in by_domain.values():
            if tier < len(domain_threads):
                threads.append(domain_threads[tier])
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
            user_id, domain, top_k=_RETRIEVAL_TOP_K, db=db
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


async def prior_context_for_session(
    session_id: str, user_id: str, domain: str, db
) -> PriorContext:
    """
    Layer 3 for this turn: built on the session's first turn and reused for
    the rest of it (COST_TO_SERVE.md C2), unless LAYER3_FREEZE_PER_SESSION
    is off.

    Freezing is what makes the dialogue prompt's opening layers identical
    from one turn to the next, so they can be cached. What it gives up is
    small: atoms extracted mid-session are already in the conversation
    itself, and the next session picks them up. A session that started
    before the snapshot existed has none, and gets one on its next turn.

    The snapshot is bookkeeping. If reading or writing it fails, the turn
    goes ahead on a freshly built Layer 3, exactly as before.
    """
    if not settings.LAYER3_FREEZE_PER_SESSION:
        return await build_prior_context(user_id, domain, db)

    try:
        stored = (
            await db.execute(
                select(Session.prior_context).where(
                    Session.id == uuid.UUID(str(session_id))
                )
            )
        ).scalar_one_or_none()
        if isinstance(stored, dict):
            return PriorContext(
                facts=stored.get("facts") or {},
                open_threads=stored.get("open_threads") or [],
                significant_people=stored.get("significant_people") or [],
            )
    except Exception:
        logger.warning(
            "Could not read the Layer 3 snapshot for session %s — rebuilding",
            session_id,
            exc_info=True,
        )
        await db.rollback()

    prior_context = await build_prior_context(user_id, domain, db)
    try:
        # Only where none exists yet, so two turns racing on a new session
        # cannot leave it with two different snapshots over its lifetime.
        await db.execute(
            update(Session)
            .where(Session.id == uuid.UUID(str(session_id)))
            .where(Session.prior_context.is_(None))
            .values(prior_context=dataclasses.asdict(prior_context))
        )
        await db.commit()
    except Exception:
        logger.warning(
            "Could not store the Layer 3 snapshot for session %s — this turn "
            "goes ahead, the next one rebuilds",
            session_id,
            exc_info=True,
        )
        await db.rollback()
    return prior_context


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
        # Not delivered_at. Twilio returning a SID means Twilio accepted
        # the message, which for WhatsApp says nothing about whether it
        # arrived — one outside the 24-hour window is accepted, given a
        # SID, then rejected by Meta asynchronously. delivered_at is set
        # by the status callback (core/delivery.py) and only for a status
        # that means the handset got it.
        delivery_status="queued" if message_sid else None,
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
    # One card per session. Both close paths can in principle reach this
    # for the same session — the extraction-triggered close races the stale
    # sweep — and the family should never get the same memory twice.
    existing = await db.execute(
        select(MemoryCard.id).where(MemoryCard.session_id == uuid.UUID(session_id))
    )
    if existing.scalar_one_or_none() is not None:
        logger.info(
            "Session %s already has a memory card — not making another", session_id
        )
        return

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

    # The family number is whatever the child typed until they prove it is
    # theirs (core/family_number.py, P3). Until then the card is kept for
    # the dashboard and not sent: a mistyped digit would send her words to
    # a stranger. Only cards made after the confirmation are sent.
    if not family_number.is_confirmed(user_profile):
        logger.info(
            "Family number for %s not confirmed — card for session %s stored, not sent",
            user_id,
            session_id,
        )
        await save_memory_card(
            session_id=session_id,
            user_id=user_id,
            card_result=card_result,
            s3_key=s3_key,
            message_sid=None,
            db=db,
        )
        return

    caption = f"A memory from today's conversation with {user_profile.name} \U0001f338"
    whatsapp = get_whatsapp_adapter()

    # The adult child never messages Katha's number — they use the web
    # dashboard — so no 24-hour customer-service window is ever open for
    # them and a free-form media message is rejected by Meta with 63016.
    # The approved template is the only delivery path that works.
    #
    # Variable mapping, which the registered template must match:
    #   {{1}} the presigned object's key and query string; the template
    #         supplies the host (see TWILIO_TEMPLATE_MEDIA_HOST)
    #   {{2}} the parent's name
    #   {{3}} the life chapter, as a person would say it
    template_sid = settings.TWILIO_TEMPLATE_MEMORY_CARD
    if not template_sid:
        logger.warning(
            "TWILIO_TEMPLATE_MEMORY_CARD is unset — sending the card free-form, "
            "which Meta rejects (63016) unless %s has messaged us in the last "
            "24 hours",
            user_profile.family_whatsapp_number,
        )

    # Hand over the key just uploaded rather than the bytes — the adapter
    # presigns it instead of uploading a second, untracked copy (S2.4b).
    message_sid = await whatsapp.send_image(
        to_number=user_profile.family_whatsapp_number,
        s3_key=s3_key,
        caption=caption,
        template_sid=template_sid or None,
        template_variables={
            "2": user_profile.name,
            "3": domain_label(card_result.domain),
        },
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


async def produce_session_outputs(session_id: str, user_id: str, db) -> None:
    """
    See _produce_session_outputs. Runs from a background task or the
    scheduler, so the usage-ledger scope is set here explicitly rather than
    inherited — entity extraction and the card's send belong to this
    session.
    """
    with usage.usage_scope(user_id=user_id, session_id=session_id):
        await _produce_session_outputs(session_id, user_id, db)


async def _produce_session_outputs(session_id: str, user_id: str, db) -> None:
    """
    Everything a finished session owes the family: entity extraction over
    the whole transcript, then the memory card.

    Shared by both ways a session can end. A conversation that simply
    stops — the normal way people end a chat — used to reach none of this,
    because the stale sweep only flipped a status column. PRD §7.3 promises
    a card after each session and Risk V1 makes that the headline
    mitigation for the P0 engagement risk, so "she talked for twenty
    minutes and never said goodbye" cannot be the case that silently gets
    nothing.

    Each half is wrapped separately: a failed entity extraction must not
    cost the card, and neither can raise into a caller that has already
    done the thing it was called for.
    """
    # Nothing is made from a session once she has asked Katha to stop (P4).
    # An earlier turn's extraction can finish after her withdrawal and try
    # to close the session; that must not send her family a card.
    try:
        status = (await parent_consent.get_status(user_id, db)).status
    except Exception:
        logger.exception("Could not read consent before outputs for %s", user_id)
        return
    if status in (
        parent_consent.ConsentStatus.WITHDRAWN,
        parent_consent.ConsentStatus.DECLINED,
    ):
        logger.info(
            "No outputs for session %s: %s has %s", session_id, user_id, status.value
        )
        return

    try:
        await run_post_session(session_id, user_id, db)
    except Exception:
        logger.exception("Post-session processing failed for session %s", session_id)

    # Conversation cards replace the memory card for a family that has
    # them (CONVERSATION_CARDS.md D2): today's card reaches the child
    # without the parent's agreement, a conversation card never does. The
    # profile is only read when the global switch is on, so with it off
    # this is exactly the path it always was.
    if settings.CONVERSATION_CARDS_ENABLED:
        try:
            profile = await get_user_profile(user_id, db)
        except Exception:
            # Not knowing whether she has cards, send nothing: falling back
            # to the memory card would reach a cards family's child without
            # her agreement.
            logger.exception(
                "Could not load profile for session %s — no card of either kind",
                session_id,
            )
            return
        if cards.enabled_for(profile):
            await card_writer.draft_for_session(session_id, user_id, db)
            return

    try:
        await _generate_and_deliver_memory_card(session_id, user_id, db)
    except Exception:
        logger.exception(
            "Memory card generation/delivery failed for session %s", session_id
        )


async def close_and_process_session(
    session_id: str,
    db,
) -> None:
    """
    Called when a session ends cleanly. Marks it completed, then produces
    the session's outputs. Triggered from run_extraction_for_turn, since
    that is what learns session_end_suggested/goal_met.
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
        logger.info("Session %s closed", session_id)
    except Exception:
        logger.exception("close_and_process_session failed for session %s", session_id)
        return

    await produce_session_outputs(session_id, state.user_id, db)


async def process_abandoned_sessions(
    abandoned: list[session_manager.AbandonedSession], db
) -> int:
    """
    Produce outputs for sessions the stale sweep just abandoned. Returns
    how many were processed.

    A session with no exchanges is skipped: Katha opened it, nobody
    replied, and there is no conversation to make a card from. Those are
    the only truly empty ones — exchange_count is incremented only after a
    turn fully succeeds.
    """
    processed = 0
    for session in abandoned:
        if session.exchange_count == 0:
            continue
        logger.info(
            "Producing outputs for abandoned session %s (%d exchange(s))",
            session.session_id,
            session.exchange_count,
        )
        await produce_session_outputs(session.session_id, session.user_id, db)
        processed += 1
    return processed


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


async def _synthesize_and_convert(
    text: str, user_profile: UserProfile, purpose: str = "tts_reply"
) -> bytes:
    """TTS then OGG conversion — the two steps that must succeed together
    for a voice note to go out, or degrade to text (see _send_or_degrade)."""
    audio_out = await tts.synthesize(text, user_profile, purpose=purpose)
    return await convert_wav_to_ogg(audio_out)


async def _send_or_degrade(
    response_text: str,
    detected_language: str,
    transcript: str,
    session_state: SessionState,
    extraction_json: dict,
    crisis_detected: bool,
    turn_id: Optional[uuid.UUID] = None,
    *,
    user_profile: UserProfile,
    tts_purpose: str = "tts_reply",
) -> TurnResult:
    """
    Try to synthesize voice for response_text. If TTS or the WAV->OGG
    conversion fails, degrade the channel (send text) rather than the
    response — the user still gets the same words, just typed instead of
    spoken (P2: never silence).
    """
    try:
        # The voice is the one pinned on her profile, never chosen from the
        # detected language, so she hears the same voice on every turn
        # (media/tts.py).
        audio_out = await _synthesize_and_convert(
            response_text, user_profile, tts_purpose
        )
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
    *,
    user_profile: UserProfile,
) -> TurnResult:
    """
    Build the reply for an STT or LLM failure using pre-synthesized audio —
    never a live TTS call, since TTS may be down too. Degrades to text if
    even the pre-synthesized clip is unavailable.
    """
    text = get_fallback_text(
        stage, user_profile.preferred_language, user_profile.katha_gender
    )
    audio = get_fallback_audio(
        stage,
        user_profile.preferred_language,
        user_profile.katha_gender,
        tts.voice_of(user_profile),
    )
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
    stt_result=None,
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

    # 2. Transcribe, unless the webhook already did: it reads every message
    # for a withdrawal before the turn (P4), and hands its transcript on.
    try:
        if stt_result is None:
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
            user_profile=user_profile,
        )
    logger.info(
        "STT: transcript=%r language=%s",
        stt_result.transcript,
        stt_result.language_code,
    )

    # 3. Pre-turn crisis check
    pre_check = conversation_policy.check_pre_turn(
        stt_result.transcript,
        state,
        language=user_profile.preferred_language,
        gender=user_profile.katha_gender,
    )
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
            user_profile=user_profile,
        )

    # 3b. Conversation cards (CONVERSATION_CARDS.md §6, §9.2): answer the
    # ask her last reply made, and plan what this turn carries. Only for a
    # family with cards; for everyone else user_profile.cards is None and
    # this turn is exactly what it always was.
    plan: Optional[card_asks.TurnPlan] = None
    if user_profile.cards is not None:
        try:
            plan = await card_asks.before_dialogue(
                session_id=session_id,
                user_id=state.user_id,
                transcript=stt_result.transcript,
                db=db,
            )
            user_profile = dataclasses.replace(
                user_profile,
                cards=dataclasses.replace(
                    user_profile.cards, layer4=plan.layer4, tag_kinds=plan.tag_kinds
                ),
            )
        except Exception:
            logger.exception("Card ask handling failed for session %s", session_id)
            await db.rollback()
            plan = None

    # 4. Build real prior context from fact store + vector store
    prior_context = await prior_context_for_session(
        session_id, state.user_id, state.domain, db
    )

    # 5. Build dialogue prompt and call the dialogue LLM. The immediately
    # preceding turn's raw exchange is included directly (not just via the
    # deferred fact/vector-store pipeline) so a story that unfolds over two
    # turns keeps continuity even if that turn's extraction hasn't run yet.
    #
    # The prompt goes in two parts so its stable half (Layers 1-3) can be
    # cached across the session's turns (C3).
    if plan is not None and plan.readback_text:
        # She said yes to hearing a card. The reply is the card's stored
        # read-back, exactly what the operator saw, never generated now
        # (§6.2).
        return await _record_and_send(
            session_id,
            state,
            user_profile,
            prior_context,
            stt_result,
            plan.readback_text,
            input_tokens=0,
            output_tokens=0,
            inbound_message_sid=inbound_message_sid,
            background_tasks=background_tasks,
            crisis_detected=False,
            plan=plan,
            asked=set(),
            tts_purpose="card_readback_tts",
            db=db,
        )

    prompt_parts = build_system_prompt_parts(user_profile, state, prior_context)
    messages = await _load_last_turn_messages(session_id, db)
    messages.append(Message(role="user", content=stt_result.transcript))
    try:
        llm_response = await llm.chat(
            messages,
            system=prompt_parts.per_turn,
            cache_system_prefix=prompt_parts.stable,
            max_tokens=_DIALOGUE_MAX_TOKENS,
            model=settings.LLM_DIALOGUE_MODEL,
            effort=settings.LLM_DIALOGUE_EFFORT,
            purpose="dialogue",
        )
    except Exception:
        logger.error(
            "Dialogue LLM call failed for session %s turn %d",
            session_id,
            state.exchange_count + 1,
            exc_info=True,
        )
        return await _fallback_turn_result(
            FailureStage.LLM,
            state,
            stt_result.transcript,
            stt_result.language_code,
            user_profile=user_profile,
        )
    logger.info(
        "LLM: tokens in=%d out=%d",
        llm_response.input_tokens,
        llm_response.output_tokens,
    )

    # 6. Post-turn policy check (malformed dialogue response). With cards
    # on, the <asked> tag is read and removed first, so it is never spoken.
    raw_reply = llm_response.content
    asked: set[str] = set()
    if plan is not None:
        asked, raw_reply = card_asks.split_asked_tag(raw_reply)
    truncated = llm_response.stop_reason == "max_tokens"
    post_check = conversation_policy.check_post_turn(
        raw_reply,
        state,
        truncated=truncated,
        language=user_profile.preferred_language,
        gender=user_profile.katha_gender,
    )
    if post_check.salvaged_untagged:
        logger.warning(
            "Dialogue reply arrived without its <response> wrapper — salvaged "
            "the bare text rather than discarding it"
        )
    if post_check.salvaged_truncated:
        logger.warning(
            "Dialogue reply for session %s was truncated at max_tokens=%d "
            "(output_tokens=%d) — sent it trimmed to its last complete "
            "sentence",
            session_id,
            _DIALOGUE_MAX_TOKENS,
            llm_response.output_tokens,
        )
    if not post_check.allowed:
        logger.warning(
            "Post-turn policy blocked: unusable LLM response (truncated=%s, "
            "output_tokens=%d)",
            truncated,
            llm_response.output_tokens,
        )
        return await _send_or_degrade(
            post_check.override_response or "",
            stt_result.language_code,
            stt_result.transcript,
            state,
            dict(_EMPTY_EXTRACTION),
            crisis_detected=False,
            user_profile=user_profile,
        )

    # 7. The dialogue response — as the policy decided it, with any
    # reasoning block stripped and any truncation trimmed.
    response_text = post_check.response_text or ""

    # 8. Crisis check on Katha's own generated reply — a safety net
    # independent of what the user said.
    response_crisis = conversation_policy.check_response_for_crisis(
        response_text,
        language=user_profile.preferred_language,
        gender=user_profile.katha_gender,
    )
    crisis_detected = response_crisis.crisis_detected
    if crisis_detected:
        logger.warning("Katha's own response matched a crisis pattern — overriding")
        response_text = response_crisis.override_response or response_text

    return await _record_and_send(
        session_id,
        state,
        user_profile,
        prior_context,
        stt_result,
        response_text,
        input_tokens=llm_response.input_tokens,
        output_tokens=llm_response.output_tokens,
        inbound_message_sid=inbound_message_sid,
        background_tasks=background_tasks,
        crisis_detected=crisis_detected,
        response_crisis=response_crisis,
        plan=plan,
        asked=asked,
        db=db,
    )


async def _record_and_send(
    session_id: str,
    state: SessionState,
    user_profile: UserProfile,
    prior_context: PriorContext,
    stt_result,
    response_text: str,
    *,
    input_tokens: int,
    output_tokens: int,
    inbound_message_sid: Optional[str],
    background_tasks: BackgroundTasks | None,
    crisis_detected: bool,
    db,
    response_crisis=None,
    plan: Optional[card_asks.TurnPlan] = None,
    asked: Optional[set[str]] = None,
    tts_purpose: str = "tts_reply",
) -> TurnResult:
    """Steps 9-12 of a turn, shared by the dialogue reply and a card's
    read-back."""
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
        input_tokens,
        output_tokens,
        db,
    )

    # From here on, paid calls (the reply's TTS) belong to this turn.
    with usage.usage_scope(turn_id=turn.id):
        if crisis_detected:
            await _log_crisis_event(
                session_id,
                turn.id,
                "assistant_response",
                (response_crisis.matched_pattern or "") if response_crisis else "",
                db,
            )

        if plan is not None:
            await _record_card_asks(plan, asked or set(), turn.id, state, db)
            if plan.correction_card_id is not None and background_tasks is not None:
                background_tasks.add_task(
                    card_writer.rewrite_for_correction,
                    plan.correction_card_id,
                    turn.id,
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
            user_profile=user_profile,
            tts_purpose=tts_purpose,
        )


async def _record_card_asks(
    plan: card_asks.TurnPlan, asked: set[str], turn_id, state: SessionState, db
) -> None:
    """The ask this reply made, and her answers, recorded against the
    saved turn. Never costs the reply."""
    try:
        await card_asks.after_dialogue(
            plan, asked, session_id=state.session_id, user_id=state.user_id, db=db
        )
        await card_asks.after_turn_saved(
            plan, turn_id, session_id=state.session_id, user_id=state.user_id, db=db
        )
    except Exception:
        logger.exception("Recording card asks failed for turn %s", turn_id)
        await db.rollback()


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
        model=settings.LLM_EXTRACTION_MODEL,
        effort=settings.LLM_EXTRACTION_EFFORT,
        purpose="extraction",
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
        model=settings.LLM_EXTRACTION_MODEL,
        effort=settings.LLM_EXTRACTION_EFFORT,
        purpose="extraction_retry",
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
    See _run_extraction_for_turn. A background task, so the usage-ledger
    scope is set explicitly: a task scheduled from the webhook must not rely
    on inheriting the request's context.
    """
    with usage.usage_scope(
        user_id=session_state.user_id, session_id=session_id, turn_id=turn_id
    ):
        await _run_extraction_for_turn(
            turn_id,
            session_id,
            session_state,
            user_profile,
            prior_context,
            transcript,
            response_text,
            db,
        )


async def _alert_extraction_failed(turn_id: uuid.UUID, user_id: str) -> None:
    """Never raises. Names the turn, not what she said (P7)."""
    from core.email import send_operator_alert

    await send_operator_alert(
        "A turn's extraction failed",
        f"Turn {turn_id} for {user_id}: its extraction, or the check for a "
        "privacy request in it, did not complete. Its stories stay hidden "
        "from the family until it is re-run.",
    )


async def _mark_extraction_failed(turn_id: uuid.UUID, user_id: str, db) -> None:
    """A turn whose extraction did not finish stays hidden (fail closed,
    P5). Leaves a turn already marked done alone: its privacy was read."""
    try:
        await db.rollback()
        turn = (
            await db.execute(select(Turn).where(Turn.id == turn_id))
        ).scalar_one_or_none()
        if turn is None or turn.extraction_status != "pending":
            return
        turn.extraction_status = "failed"
        await db.commit()
    except Exception:
        logger.exception("Could not mark turn %s failed", turn_id)
        return
    await _alert_extraction_failed(turn_id, user_id)


async def _run_extraction_for_turn(
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
            extraction_json,
            session_id,
            session_state.user_id,
            db,
            turn_id=turn_id,
            session_number=session_state.session_number,
            # The fallback when the model returns a domain the registry
            # does not know. Canonical by construction — start_session
            # picks it from the registry.
            session_domain=session_state.domain,
        )

        # "Keep this between us", for every family (P5, D5), and with cards
        # her sharing preference said in passing (CONVERSATION_CARDS.md §5).
        # Its own try: a failure here must not cost the turn's atoms or the
        # session close. It does cost their visibility: the turn is marked
        # failed, and its atoms stay hidden from the family.
        privacy_read = True
        try:
            if user_profile.cards is not None:
                await card_privacy.apply_share_signals(
                    extraction_json,
                    turn_id=turn_id,
                    session_id=session_id,
                    user_id=session_state.user_id,
                    db=db,
                )
            else:
                await card_privacy.apply_private_request(
                    extraction_json,
                    turn_id=turn_id,
                    user_id=session_state.user_id,
                    db=db,
                    cards_on=False,
                )
        except Exception:
            logger.exception("Privacy step failed for turn %s", turn_id)
            await db.rollback()
            privacy_read = False

        turn_result = await db.execute(select(Turn).where(Turn.id == turn_id))
        turn = turn_result.scalar_one_or_none()
        if turn is not None:
            turn.extraction_json = extraction_json
            turn.input_tokens = (turn.input_tokens or 0) + input_tokens
            turn.output_tokens = (turn.output_tokens or 0) + output_tokens
            # Only now may its atoms reach the family (not_private_atom).
            turn.extraction_status = "done" if privacy_read else "failed"
            db.add(turn)
            await db.commit()
        if not privacy_read:
            await _alert_extraction_failed(turn_id, session_state.user_id)

        new_state = await session_manager.apply_extraction(
            session_id, extraction_json, db
        )
    except Exception:
        logger.exception(
            "Extraction failed for turn %s (session %s)", turn_id, session_id
        )
        await _mark_extraction_failed(turn_id, session_state.user_id, db)
        return

    should_close = session_manager.should_end_session(
        new_state, goal_met_before_this_turn=session_state.goal_met
    )
    if (
        should_close
        and user_profile.cards is not None
        and await card_asks.has_pending_ask(session_id, db)
    ):
        # She owes an answer to an ask in Katha's last reply ("shall I read
        # it to you?"). Closing now would turn her answer away as a second
        # conversation for the day; the stale sweep closes it if she never
        # replies.
        logger.info("Session %s stays open: an ask is waiting for her", session_id)
        should_close = False
    if new_state.goal_met and not session_state.goal_met and not should_close:
        logger.info(
            "Session %s just reached its domain goal — deferring close to "
            "the next turn's closing exchange",
            session_id,
        )
    if should_close:
        await close_and_process_session(session_id, db)
