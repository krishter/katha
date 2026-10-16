from __future__ import annotations

import logging

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
    Query,
    Request,
    Response,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters import sarvam_stt
from adapters.whatsapp_stub import get_whatsapp_adapter
from config import settings
from core import orchestrator, parent_consent, session_manager, usage
from core.delivery import record_card_delivery
from core.fallback_audio import FailureStage, get_fallback_text
from models.db import get_db
from models.user_profile import UserProfileModel
from prompts.system_prompt import UserProfile

logger = logging.getLogger(__name__)

router = APIRouter()

_ICARE_NUMBER = "9152987821"
_CRISIS_TEXT = (
    f"I'm worried about you. Please reach out to iCall India: {_ICARE_NUMBER}. "
    "They are available and ready to help."
)
_TEXT_ONLY_REPLY = (
    "Please send me a voice message — I'd love to hear your voice! \U0001f399"
)
_NOT_SCHEDULED_TEXT = "Hi! Your session isn't scheduled yet."

# Twilio POSTs here and interprets whatever comes back. Returning
# Response(content="OK") sent no Content-Type header at all, and Twilio
# treats a bodied 200 it cannot parse as TwiML as the text of a reply to
# send — so the parent received a bare "OK" from Katha after every single
# voice note (observed in production 2026-09-29..10-02 as outbound-api
# messages with body "OK", one second after each real reply).
#
# Empty TwiML is the documented way to say "received, nothing to send".
# The media type is what makes it work: with it Twilio parses an empty
# <Response/> and sends nothing; without it the body becomes a message.
_TWIML_NO_REPLY = '<?xml version="1.0" encoding="UTF-8"?><Response></Response>'


def _ack() -> Response:
    """Acknowledge a Twilio webhook without sending anything back."""
    return Response(
        status_code=200, content=_TWIML_NO_REPLY, media_type="application/xml"
    )


async def _handle_parent_consent(
    whatsapp,
    profile: UserProfileModel,
    transcript: str,
    turn_id=None,
    *,
    db: AsyncSession,
) -> None:
    """
    Ask the parent, or read her answer. Runs instead of a domain session
    for anyone who has not yet agreed (F-02).

    Everything here is a free-form message inside the 24-hour window her
    own inbound message opened. That is the only reason a voice note is
    possible at all — Meta rejected the approved template for first
    contact with 63049, so there is no template path to fall back on.
    """
    state = await parent_consent.get_status(profile.user_id, db)

    if state.status is parent_consent.ConsentStatus.HALTED:
        # Asked twice, still unclear. Pestering an elderly person who does
        # not understand what is being asked is its own harm; a human picks
        # this up from the dashboard.
        logger.info("Parent consent halted for %s — not re-asking", profile.user_id)
        return

    # Nothing said yet: this is her first message, so introduce and ask.
    if state.status is parent_consent.ConsentStatus.NOT_ASKED:
        await _speak(
            whatsapp,
            profile,
            parent_consent.build_welcome_text(profile.name),
            stage="parent_welcome",
        )
        await parent_consent.note_asked(profile.user_id, db)
        return

    # She has been asked. Read what she said.
    answer = parent_consent.interpret_answer(transcript)

    # Persist the exchange before recording anything. A ConsentRecord whose
    # evidence_ref points at nothing proves nothing — the audio and the
    # transcript of her saying yes are what make it auditable.
    if answer is not parent_consent.Answer.UNCLEAR:
        turn_id = await _persist_consent_turn(profile, transcript, answer, db)

    if answer is parent_consent.Answer.YES:
        await parent_consent.record_answer(profile.user_id, answer, db, turn_id=turn_id)
        await _speak(
            whatsapp, profile, parent_consent.GRANTED_TEXT, stage="parent_granted"
        )
        return

    if answer is parent_consent.Answer.NO:
        await parent_consent.record_answer(profile.user_id, answer, db, turn_id=turn_id)
        await _speak(
            whatsapp, profile, parent_consent.DECLINE_TEXT, stage="parent_declined"
        )
        return

    # Unclear — one clarification, then stop.
    await _speak(
        whatsapp,
        profile,
        parent_consent.build_reask_text(profile.name),
        stage="parent_reask",
    )
    await parent_consent.note_asked(profile.user_id, db)


async def _persist_consent_turn(
    profile: UserProfileModel,
    transcript: str,
    answer,
    db: AsyncSession,
):
    """Store the consent exchange as a Turn, and return its id."""
    try:
        session_row = await session_manager.get_or_create_consent_session(
            profile.user_id, profile.whatsapp_number, db
        )
        turn = await orchestrator._persist_turn(
            session_id=str(session_row.id),
            user_id=profile.user_id,
            turn_number=0,
            inbound_message_sid=None,
            transcript=transcript,
            detected_language=profile.preferred_language,
            response_text=f"[parent consent: {answer.value}]",
            extraction_json={},
            input_tokens=0,
            output_tokens=0,
            db=db,
        )
        return turn.id
    except Exception:
        # Never let bookkeeping cost us the answer itself. A record with a
        # null evidence_ref is weaker than one with it, but far better than
        # losing a clearly-given yes or a clearly-given no.
        logger.exception(
            "Could not persist consent turn for %s — recording without evidence",
            profile.user_id,
        )
        return None


async def _speak(whatsapp, profile: UserProfileModel, text: str, *, stage: str) -> None:
    """
    Say something as a voice note in the parent's own language, falling
    back to text if speech fails.

    Voice is the point: this user was chosen for a product that talks, and
    the first thing she hears should not be a wall of text in English.
    """
    try:
        from adapters import sarvam_tts
        from media.audio_convert import convert_wav_to_ogg

        audio = await sarvam_tts.synthesize(
            text, language_code=profile.preferred_language, purpose="tts_system"
        )
        audio = await convert_wav_to_ogg(audio)
        await whatsapp.send_voice_note(
            profile.whatsapp_number, audio, mime_type="audio/ogg"
        )
    except Exception:
        logger.exception(
            "Voice failed for %s (stage=%s) — falling back to text", stage, stage
        )
        await _safe_send_text(whatsapp, profile.whatsapp_number, text, stage=stage)


_ALREADY_TALKED_TEXT = (
    "We already had our conversation today — thank you for that. "
    "I'll be here again tomorrow, and I'm looking forward to it. "
    "If there's something you want to add, just send it and I'll keep it safe."
)


async def _open_session_on_demand(
    whatsapp,
    profile: UserProfileModel | None,
    from_number: str,
    db: AsyncSession,
):
    """
    Open a session because the parent sent something and none was active.

    Returns the new SessionState, or None if no session should start — in
    which case this has already said something appropriate and the caller
    should just acknowledge the webhook.

    Consent is not re-checked here: step 5 above returns before this point
    for anyone who has not granted it, so reaching this function means she
    has agreed.
    """
    if profile is None:
        # A number with no profile — not a parent we know. Nothing to open.
        logger.info("Inbound from unknown number %s", from_number)
        await _safe_send_text(
            whatsapp, from_number, _NOT_SCHEDULED_TEXT, stage="no_active_session"
        )
        return None

    # One conversation a day. A second would burn a free-tier session on a
    # cadence the product does not promise, and the daily rhythm is the
    # therapeutic design (PRD 5.1), not a rate limit. An abandoned session
    # does not count — that is the case this whole path exists for.
    if await session_manager.completed_session_today(
        profile.user_id, db, profile.timezone or "Asia/Kolkata"
    ):
        logger.info(
            "Inbound from %s but a session already completed today — not opening",
            profile.user_id,
        )
        await _speak(whatsapp, profile, _ALREADY_TALKED_TEXT, stage="already_talked")
        return None

    try:
        state = await session_manager.start_session(profile.user_id, db)
    except HTTPException as exc:
        # 402 is the freemium limit. start_session has already sent the
        # upgrade prompt to the family, so say nothing further to her —
        # being asked to pay is not her transaction.
        if exc.status_code == 402:
            logger.info(
                "On-demand session refused for %s — free limit", profile.user_id
            )
            return None
        raise
    except Exception:
        logger.exception("Could not open on-demand session for %s", profile.user_id)
        await _safe_send_text(
            whatsapp,
            from_number,
            get_fallback_text(FailureStage.OTHER),
            stage="on_demand_open",
        )
        return None

    logger.info(
        "Opened on-demand session %s for %s (number=%d domain=%s) — she messaged first",
        state.session_id,
        profile.user_id,
        state.session_number,
        state.domain,
    )
    return state


async def _load_profile_by_number(
    whatsapp_number: str, db: AsyncSession
) -> UserProfileModel | None:
    """The parent is known by the number she messages from."""
    result = await db.execute(
        select(UserProfileModel).where(
            UserProfileModel.whatsapp_number == whatsapp_number
        )
    )
    return result.scalar_one_or_none()


async def _load_user_profile_for_session(
    session_state: session_manager.SessionState, db: AsyncSession
) -> UserProfile:
    """Load UserProfile from user_profiles table for the given session."""
    result = await db.execute(
        select(UserProfileModel).where(
            UserProfileModel.user_id == session_state.user_id
        )
    )
    row = result.scalar_one_or_none()
    if row is None:
        return UserProfile(
            name="Friend",
            preferred_language="hi-IN",
            onboarding_context="",
            # No row, so nothing is known — Layer 1 then tells the model
            # not to guess rather than letting it infer from the name.
            pronouns="",
        )
    return UserProfile(
        name=row.name,
        preferred_language=row.preferred_language,
        onboarding_context=row.onboarding_context or "",
        pronouns=row.pronouns or "",
    )


async def _safe_send_text(whatsapp, to_number: str, text: str, *, stage: str) -> None:
    """
    Send a fallback text. Wrapped so a failure here — the last line of
    defence against silence — cannot re-enter the handler and cannot
    itself go unlogged.
    """
    try:
        await whatsapp.send_text(to_number, text)
    except Exception:
        logger.error(
            "Failed to send fallback text (stage=%s) to %s",
            stage,
            to_number,
            exc_info=True,
        )


async def _deliver_turn_result(
    whatsapp, to_number: str, result: orchestrator.TurnResult, db: AsyncSession
) -> None:
    """
    Deliver whatever process_voice_turn produced. If it already degraded to
    text (TTS/conversion failed), send text. If the send itself fails,
    fall back to text with the same content as a last resort. On a real
    voice-note send, records the S3 key used so it's enumerable for
    deletion later (P4) — the upload happens inside send_voice_note, after
    the turn is already committed, so this is a separate follow-up write.
    """
    try:
        if result.response_mime_type == "text/plain":
            await whatsapp.send_text(to_number, result.response_text)
        else:
            _message_sid, s3_key = await whatsapp.send_voice_note(
                to_number, result.response_audio, mime_type=result.response_mime_type
            )
            if result.turn_id is not None:
                await orchestrator.set_turn_audio_key(result.turn_id, s3_key, db)
    except Exception:
        logger.error(
            "Failed to deliver turn result to %s — falling back to text",
            to_number,
            exc_info=True,
        )
        await _safe_send_text(whatsapp, to_number, result.response_text, stage="send")


# Twilio's own vocabulary for what became of a message. "delivered" and
# "read" are the only two that mean it reached the handset; "sent" only
# means Meta accepted it from Twilio, which is precisely the distinction
# that hid the memory-card failure.
_DELIVERED_STATUSES = {"delivered", "read"}
_FAILED_STATUSES = {"failed", "undelivered"}

# Meta: free-form message outside the 24-hour customer-service window.
# Called out by name because it is the expected failure for any message to
# the family number, who never messages Katha and so never opens one.
_OUTSIDE_WINDOW_ERROR = "63016"


@router.post("/webhook/twilio/status")
async def twilio_status_callback(
    request: Request,
    db: AsyncSession = Depends(get_db),
    whatsapp=Depends(get_whatsapp_adapter),
) -> Response:
    """
    Twilio's out-of-band report on a message we sent.

    Twilio accepts a WhatsApp message and returns a SID straight away;
    whether Meta delivered it arrives here, seconds to minutes later. With
    no one listening, a card rejected with 63016 looked exactly like a card
    that arrived, and memory_cards.delivered_at was stamped on acceptance —
    so the dashboard reported delivery for messages that never left.

    Always 200, like the inbound webhook: Twilio retries non-200 and there
    is nothing a retry would fix here.
    """
    try:
        form = await request.form()
        params = dict(form)

        signature = request.headers.get("X-Twilio-Signature", "")
        url = f"{settings.PUBLIC_BASE_URL}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        if not whatsapp.validate_signature(url, params, signature):
            logger.warning("Invalid Twilio signature on status callback")
            return Response(status_code=403, content="Forbidden")

        message_sid = params.get("MessageSid") or params.get("SmsSid")
        status = (params.get("MessageStatus") or "").lower()
        error_code = params.get("ErrorCode") or None

        if not message_sid or not status:
            return _ack()

        if status in _FAILED_STATUSES:
            # Logged at error, with the code spelled out, because this is
            # the signal that was missing entirely. A 63016 here means the
            # recipient has no open window and the send needed an approved
            # template.
            logger.error(
                "Message %s %s (ErrorCode=%s)%s",
                message_sid,
                status,
                error_code,
                " — outside the 24-hour window; this send needed an approved template"
                if error_code == _OUTSIDE_WINDOW_ERROR
                else "",
            )
        else:
            logger.info("Message %s -> %s", message_sid, status)

        await record_card_delivery(message_sid, status, error_code, db)
        await usage.record_status(message_sid, status, error_code)

    except Exception:
        logger.error("Status callback processing error", exc_info=True)

    return _ack()


@router.get("/webhook/whatsapp")
async def whatsapp_verify(
    hub_mode: str = Query(alias="hub.mode", default=""),
    hub_verify_token: str = Query(alias="hub.verify_token", default=""),
    hub_challenge: str = Query(alias="hub.challenge", default=""),
) -> Response:
    """Meta Cloud API webhook verification (future-proof)."""
    if hub_mode == "subscribe" and hub_verify_token == settings.WEBHOOK_VERIFY_TOKEN:
        return Response(content=hub_challenge, media_type="text/plain")
    return Response(status_code=403, content="Forbidden")


async def _note_inbound(profile: UserProfileModel | None, db: AsyncSession) -> None:
    """
    Stamp when she last messaged. Meta allows free-form messages only within
    24 hours of it. Bookkeeping, so it can never cost the turn.
    """
    if profile is None:
        return
    try:
        from datetime import datetime, timezone

        profile.last_inbound_at = datetime.now(timezone.utc)
        db.add(profile)
        await db.commit()
    except Exception:
        logger.warning(
            "Could not record last_inbound_at for %s", profile.user_id, exc_info=True
        )
        await db.rollback()


@router.post("/webhook/whatsapp")
async def whatsapp_incoming(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    whatsapp=Depends(get_whatsapp_adapter),
) -> Response:
    """
    Main webhook handler for incoming Twilio WhatsApp events. See
    _whatsapp_incoming.

    Every paid call made while handling the message is attributed to her in
    the usage ledger. The handler widens that scope as it learns who she is,
    which session this is and which turn; resetting here puts it back
    however the handler exits.
    """
    token = usage.enter_scope()
    try:
        return await _whatsapp_incoming(request, background_tasks, db, whatsapp)
    finally:
        usage.exit_scope(token)


async def _whatsapp_incoming(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession,
    whatsapp,
) -> Response:
    """
    Main webhook handler for incoming Twilio WhatsApp events.
    Always returns HTTP 200 — Twilio retries on non-200.

    Every inbound message produces an outbound message, including on
    failure (P2). process_voice_turn handles STT/LLM/TTS failures
    internally and always returns a TurnResult; this handler's own
    try/except is the last-resort net for anything else (session lookup,
    DB errors, etc.) — and it, too, always tries to reply.
    """
    from_number = None
    try:
        # 1. Parse form payload
        form = await request.form()
        params = dict(form)

        # 2. Validate Twilio signature. Built from PUBLIC_BASE_URL, not
        # request.url — behind a TLS-terminating load balancer, request.url
        # reports the internal http:// scheme, which never matches the
        # https:// URL Twilio actually signed (H2).
        signature = request.headers.get("X-Twilio-Signature", "")
        url = f"{settings.PUBLIC_BASE_URL}{request.url.path}"
        if request.url.query:
            url = f"{url}?{request.url.query}"
        if not whatsapp.validate_signature(url, params, signature):
            logger.warning("Invalid Twilio signature from %s", request.client)
            return Response(status_code=403, content="Forbidden")

        # 3. Parse message fields
        raw_from = params.get("From", "")
        from_number = raw_from.replace("whatsapp:", "")
        media_url = params.get("MediaUrl0", "")
        media_type = params.get("MediaContentType0", "")
        # None (not "") when absent — turns.inbound_message_sid is unique,
        # and Postgres treats multiple NULLs as distinct but multiple ""
        # values as a genuine collision.
        message_sid = params.get("MessageSid") or None

        logger.info(
            "Webhook: from=%s media_type=%s sid=%s",
            from_number,
            media_type,
            message_sid,
        )

        # 4. Idempotency: Twilio retries a webhook that didn't return a
        # fast 200. Without this, a retry would reprocess the same voice
        # note as a brand new turn — duplicate LLM call, duplicate charge,
        # two replies to one message (H3).
        if message_sid:
            existing_turn = await orchestrator.find_turn_by_message_sid(message_sid, db)
            if existing_turn is not None:
                logger.info(
                    "Duplicate webhook for MessageSid %s — already processed, skipping",
                    message_sid,
                )
                return _ack()

        # 5. Consent gate (F-02). Before anything else, has this parent
        # agreed? She is the data principal; her child's tick-box is not
        # her consent. An inbound message from someone who has not agreed
        # gets the welcome and the question — never a domain session.
        #
        # This sits ahead of the session lookup deliberately: her first
        # message arrives with no session at all, and the old path answered
        # it with "your session isn't scheduled yet".
        profile = await _load_profile_by_number(from_number, db)

        # Twilio charges for every inbound message, including ones that
        # never become a turn. Recorded after the idempotency check above,
        # and once per SID even when Twilio retries (see record_inbound).
        usage.enter_scope(user_id=profile.user_id if profile is not None else None)
        await usage.record_inbound(message_sid)
        await _note_inbound(profile, db)

        if profile is not None and not await parent_consent.has_granted(
            profile.user_id, db
        ):
            transcript = ""
            turn_id = None
            if media_url and "audio" in media_type:
                # Her answer is spoken, so it has to be transcribed before
                # it can be read. Failure here is not consent — it falls
                # through to UNCLEAR and asks again.
                try:
                    audio_bytes = await whatsapp.download_voice_note(media_url)
                    stt = await sarvam_stt.transcribe(audio_bytes)
                    transcript = stt.transcript
                except Exception:
                    logger.exception(
                        "Could not transcribe consent reply from %s", from_number
                    )
            else:
                transcript = params.get("Body", "") or ""

            await _handle_parent_consent(whatsapp, profile, transcript, turn_id, db=db)
            return _ack()

        # 6. Look up active session, or open one because she just asked.
        #
        # The scheduled session is abandoned four hours after it opens, so
        # for twenty hours a day there is no active session and this branch
        # used to answer "your session isn't scheduled yet" — turning away
        # the one person the product exists to listen to, at the moment she
        # reached out. Worse in practice, because the 09:30 opener is
        # rejected by Meta outside the 24-hour window (63016,
        # docs/proposals/outbound-messaging-window.md), so she is never told
        # a window existed at all.
        #
        # An inbound voice note IS the signal the scheduler was trying to
        # manufacture. Open a session and let her talk.
        state = await session_manager.get_active_session_by_number(from_number, db)
        if state is None:
            state = await _open_session_on_demand(whatsapp, profile, from_number, db)
            if state is None:
                return _ack()
        usage.enter_scope(user_id=state.user_id, session_id=state.session_id)

        # 6. Handle voice note
        if media_url and "audio" in media_type:
            audio_bytes = await whatsapp.download_voice_note(media_url)
            user_profile = await _load_user_profile_for_session(state, db)

            result = await orchestrator.process_voice_turn(
                audio_bytes,
                state.session_id,
                user_profile,
                db,
                inbound_message_sid=message_sid,
                background_tasks=background_tasks,
            )

            usage.enter_scope(turn_id=result.turn_id)
            await _deliver_turn_result(whatsapp, from_number, result, db)

            # Update last_user_message_at
            await session_manager.touch_last_message(state.session_id, db)

            if result.crisis_detected:
                await _safe_send_text(
                    whatsapp, from_number, _CRISIS_TEXT, stage="crisis"
                )

            # Session close is triggered from run_extraction_for_turn, once
            # extraction learns session_end_suggested/goal_met — not here.

        else:
            # 7. Text message — prompt for voice note
            await _safe_send_text(
                whatsapp, from_number, _TEXT_ONLY_REPLY, stage="text_only"
            )

    except Exception:
        logger.error("Webhook processing error for %s", from_number, exc_info=True)
        if from_number:
            await _safe_send_text(
                whatsapp,
                from_number,
                get_fallback_text(FailureStage.OTHER),
                stage="other",
            )

    return _ack()
