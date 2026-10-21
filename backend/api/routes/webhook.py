from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

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
from cards import context as card_context
from cards import family_inbox
from config import settings
from core import (
    family_number,
    orchestrator,
    parent_consent,
    session_manager,
    twilio_cleanup,
    usage,
    withdrawal,
)
from core.delivery import record_card_delivery
from models.db import get_db
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel
from prompts.system_prompt import UserProfile
from speech import catalogue

logger = logging.getLogger(__name__)

router = APIRouter()

# After a "no" or a withdrawal, Katha answers her at most this often (P4).
# "I will not message you again" has to hold; one reply a month keeps her
# from talking into silence if she has forgotten.
_REPLY_AFTER_NO_EVERY = timedelta(days=30)

# The one fixed message that stays English: it goes to a number with no
# profile, so there is no language to speak it in. Every other fixed
# message is in the parent's language (speech/catalogue.py, M-14).
_NOT_SCHEDULED_TEXT = "Hi! Your session isn't scheduled yet."


def _say(message_id: str, profile: UserProfileModel | None, **fields: str) -> str:
    """A fixed message in her language, as Katha's persona says it. English
    only when there is no profile to take a language from."""
    if profile is None:
        return catalogue.text(message_id, catalogue.SOURCE_LANGUAGE, **fields)
    return catalogue.text(
        message_id,
        profile.preferred_language or catalogue.SOURCE_LANGUAGE,
        gender=profile.katha_gender,
        **fields,
    )


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


async def _welcome_text(profile: UserProfileModel, db: AsyncSession) -> str:
    """The welcome, naming the child who set it up (P3 3.6). "Your family"
    only when the family gave no name."""
    child = (
        await db.execute(
            select(FamilyAccount.child_name).where(
                FamilyAccount.user_id == profile.user_id
            )
        )
    ).scalar_one_or_none()
    return parent_consent.build_welcome_text(
        profile.name,
        (child or "").strip() or None,
        language=profile.preferred_language,
        gender=profile.katha_gender,
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

    The conversation (P3, D2):
      welcome → yes → "just so I am sure…" → yes → recorded
      welcome → unclear → one re-ask → unclear → halted for a person
      no, at any point → recorded as a decline
    A first yes is never recorded on its own: a classifier can be wrong,
    and the confirmation restates what she is agreeing to. Anything but a
    second yes goes back to asking, under the same two-ask limit.

    Everything here is a free-form message inside the 24-hour window her
    own inbound message opened. That is the only reason a voice note is
    possible at all — Meta rejected the approved template for first
    contact with 63049, so there is no template path to fall back on.
    """
    state = await parent_consent.get_status(profile.user_id, db)
    language, gender = profile.preferred_language, profile.katha_gender

    if state.status in (
        parent_consent.ConsentStatus.DECLINED,
        parent_consent.ConsentStatus.WITHDRAWN,
    ):
        await _reply_after_no(whatsapp, profile, state.status, db)
        return

    if state.status is parent_consent.ConsentStatus.HALTED:
        # Still unclear after the re-ask. Pestering an elderly person who
        # does not understand what is being asked is its own harm; a human
        # picks this up from the dashboard.
        logger.info("Parent consent halted for %s — not re-asking", profile.user_id)
        return

    # Nothing said yet: this is her first message, so introduce and ask.
    if state.status is parent_consent.ConsentStatus.NOT_ASKED:
        await _speak(
            whatsapp,
            profile,
            await _welcome_text(profile, db),
            stage="parent_welcome",
        )
        await parent_consent.note_asked(profile.user_id, db)
        return

    # She has been asked. Read what she said, against what she was asked.
    confirming = state.status is parent_consent.ConsentStatus.CONFIRMING
    if confirming:
        question = parent_consent.confirm_text(language, gender)
    elif state.times_asked >= 2:
        question = parent_consent.build_reask_text(
            profile.name, language=language, gender=gender
        )
    else:
        question = await _welcome_text(profile, db)
    answer = await parent_consent.interpret_answer(transcript, question)

    # Persist the exchange before recording anything. A ConsentRecord whose
    # evidence_ref points at nothing proves nothing — the audio and the
    # transcript of her saying yes are what make it auditable.
    if answer is parent_consent.Answer.YES and not confirming:
        await _persist_consent_turn(profile, transcript, "yes, asked to confirm", db)
        await parent_consent.set_state(
            profile.user_id, parent_consent.STATE_CONFIRMING, db
        )
        await _speak(
            whatsapp,
            profile,
            parent_consent.confirm_text(language, gender),
            stage="parent_confirm",
            fixed=True,
        )
        return

    if answer is parent_consent.Answer.YES:
        turn_id = await _persist_consent_turn(profile, transcript, "yes, confirmed", db)
        await parent_consent.record_answer(profile.user_id, answer, db, turn_id=turn_id)
        await _speak(
            whatsapp,
            profile,
            parent_consent.granted_text(language, gender),
            stage="parent_granted",
            fixed=True,
        )
        return

    if answer is parent_consent.Answer.NO:
        turn_id = await _persist_consent_turn(profile, transcript, "no", db)
        await parent_consent.record_answer(profile.user_id, answer, db, turn_id=turn_id)
        await _speak(
            whatsapp,
            profile,
            parent_consent.declined_text(language, gender),
            stage="parent_declined",
            fixed=True,
        )
        await _note_reply_after_no(profile, db)
        return

    # Unclear. After the re-ask, stop; before it, one clarification.
    if state.times_asked >= 2:
        await parent_consent.set_state(profile.user_id, parent_consent.STATE_HALTED, db)
        logger.info(
            "Parent consent for %s still unclear after the re-ask — halted",
            profile.user_id,
        )
        return
    if confirming:
        await parent_consent.set_state(profile.user_id, parent_consent.STATE_ASKING, db)
    await _speak(
        whatsapp,
        profile,
        parent_consent.build_reask_text(profile.name, language=language, gender=gender),
        stage="parent_reask",
    )
    await parent_consent.note_asked(profile.user_id, db)


async def _note_reply_after_no(profile: UserProfileModel, db: AsyncSession) -> None:
    profile.last_decline_reply_at = datetime.now(timezone.utc)
    db.add(profile)
    await db.commit()


async def _reply_after_no(
    whatsapp,
    profile: UserProfileModel,
    status: parent_consent.ConsentStatus,
    db: AsyncSession,
) -> None:
    """
    She said no, or asked Katha to stop, and has messaged again. Katha said
    it would not message her again, so it answers at most once a month,
    with the same words as before, and otherwise says nothing (P4).
    """
    last = profile.last_decline_reply_at
    if last is not None and datetime.now(timezone.utc) - last < _REPLY_AFTER_NO_EVERY:
        logger.info(
            "Parent %s (%s) messaged again — not replying",
            profile.user_id,
            status.value,
        )
        return
    language, gender = profile.preferred_language, profile.katha_gender
    text = (
        parent_consent.withdraw_text(language, gender)
        if status is parent_consent.ConsentStatus.WITHDRAWN
        else parent_consent.declined_text(language, gender)
    )
    await _speak(whatsapp, profile, text, stage="reply_after_no", fixed=True)
    await _note_reply_after_no(profile, db)


async def _persist_consent_turn(
    profile: UserProfileModel,
    transcript: str,
    label: str,
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
            response_text=f"[parent consent: {label}]",
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


async def _speak(
    whatsapp,
    profile: UserProfileModel,
    text: str,
    *,
    stage: str,
    fixed: bool = False,
) -> None:
    """
    Say something as a voice note in the parent's own language, falling
    back to text if speech fails.

    Voice is the point: this user was chosen for a product that talks, and
    the first thing she hears should not be a wall of text in English.

    `fixed` marks text with no name in it, the same for every parent: it
    comes from the system-speech cache instead of a fresh synthesis
    (COST_TO_SERVE.md C6). Anything personalised must leave it False.
    """
    try:
        if fixed:
            from media import system_speech, tts

            audio = await system_speech.get_system_speech(
                text,
                profile.preferred_language,
                gender=profile.katha_gender,
                voice=tts.voice_of(profile),
            )
        else:
            from media import tts
            from media.audio_convert import convert_wav_to_ogg

            audio = await tts.synthesize(text, profile, purpose="tts_system")
            audio = await convert_wav_to_ogg(audio)
        await whatsapp.send_voice_note(
            profile.whatsapp_number, audio, mime_type="audio/ogg"
        )
    except Exception:
        logger.exception(
            "Voice failed for %s (stage=%s) — falling back to text", stage, stage
        )
        await _safe_send_text(whatsapp, profile.whatsapp_number, text, stage=stage)


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
        await _speak(
            whatsapp,
            profile,
            _say("already_talked", profile),
            stage="already_talked",
            fixed=True,
        )
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
            _say("fallback_other", profile),
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
        katha_gender=row.katha_gender,
        tts_voice=row.tts_voice or "",
        cards=await card_context.context_for(row, db),
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

        # Final: Twilio's copy of what Katha sent her can go (P6, D8). Only
        # after the status is recorded above. A delete that fails stays
        # queued for the scheduler.
        if status in twilio_cleanup.FINAL_STATUSES:
            await twilio_cleanup.delete_now(message_sid, whatsapp)

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
    # Known once the number is looked up; the last-resort apology below
    # uses it to speak her language.
    profile = None
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

        # Twilio keeps every message until told otherwise (P6, D8). Queue
        # this one for deletion now, so even a message whose handling fails
        # below is deleted by the scheduler once INBOUND_GRACE has passed.
        # A voice note is deleted sooner, straight after it is transcribed.
        await twilio_cleanup.queue(
            message_sid,
            direction=twilio_cleanup.INBOUND,
            user_id=profile.user_id if profile is not None else None,
        )

        # 5a. The child confirming the family number (P3, D3): "CONFIRM
        # <code>" from the number memory cards go to. Cards on or off, since
        # memory cards go to that number either way. A parent's own number
        # never takes this path.
        confirm_code = family_number.parse(params.get("Body", ""))
        if profile is None and confirm_code:
            parent = await family_number.confirm(from_number, confirm_code, db)
            usage.enter_scope(user_id=parent.user_id if parent else None)
            await usage.record_inbound(message_sid)
            await _safe_send_text(
                whatsapp,
                from_number,
                family_number.CONFIRMED_TEXT
                if parent
                else family_number.NOT_CONFIRMED_TEXT,
                stage="family_confirm",
            )
            return _ack()

        # 5b. A child replying about a conversation card (CONVERSATION_CARDS.md
        # §10.1). Only for a number that is no parent's own — the parent
        # wins a clash — and only when cards are on for that family. With
        # cards off, the child's number takes the unknown-number path as
        # it always has.
        if profile is None and settings.CONVERSATION_CARDS_ENABLED:
            family_parent = await family_inbox.parent_for_family_number(from_number, db)
            if family_parent is not None:
                usage.enter_scope(user_id=family_parent.user_id)
                await usage.record_inbound(message_sid)

                async def transcribe(url: str) -> str:
                    audio = await whatsapp.download_voice_note(url)
                    return (await sarvam_stt.transcribe(audio)).transcript

                await family_inbox.handle_whatsapp(
                    whatsapp, family_parent, from_number, params, transcribe, db
                )
                return _ack()

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
                    # Heard; Twilio's copy can go, after the response.
                    background_tasks.add_task(
                        twilio_cleanup.delete_now, message_sid, whatsapp
                    )
                except Exception:
                    logger.exception(
                        "Could not transcribe consent reply from %s", from_number
                    )
            else:
                transcript = params.get("Body", "") or ""

            await _handle_parent_consent(whatsapp, profile, transcript, turn_id, db=db)
            return _ack()

        # 5c. Does she want Katha to stop (P4)? Checked on every message from
        # a parent who has agreed, before a session is looked up or opened:
        # a "stop" sent after the day's session closed, or at the free
        # limit, must be heard too. So the voice note is transcribed here,
        # and the same transcript is handed to the turn below; it is never
        # transcribed twice. A failed STT is left to the turn, which
        # retries it and apologises. Only for a parent Katha knows: a
        # stranger's voice note is never downloaded or transcribed.
        audio_bytes = None
        stt_result = None
        heard = ""
        if profile is not None and media_url and "audio" in media_type:
            audio_bytes = await whatsapp.download_voice_note(media_url)
            try:
                stt_result = await sarvam_stt.transcribe(audio_bytes)
                # Heard; Twilio's copy can go, after the response (P6).
                background_tasks.add_task(
                    twilio_cleanup.delete_now, message_sid, whatsapp
                )
            except Exception:
                logger.warning(
                    "STT failed before the withdrawal check for %s",
                    profile.user_id,
                    exc_info=True,
                )
            heard = stt_result.transcript if stt_result else ""
        elif profile is not None:
            heard = params.get("Body", "") or ""
        if profile is not None and await withdrawal.is_withdrawal(heard):
            erase_at = await withdrawal.record(
                profile, heard, db, inbound_message_sid=message_sid
            )
            await _speak(
                whatsapp,
                profile,
                parent_consent.withdraw_text(
                    profile.preferred_language, profile.katha_gender
                ),
                stage="withdrawn",
                fixed=True,
            )
            await withdrawal.notify(profile, erase_at, db)
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
            if audio_bytes is None:
                audio_bytes = await whatsapp.download_voice_note(media_url)
            user_profile = await _load_user_profile_for_session(state, db)

            result = await orchestrator.process_voice_turn(
                audio_bytes,
                state.session_id,
                user_profile,
                db,
                inbound_message_sid=message_sid,
                background_tasks=background_tasks,
                stt_result=stt_result,
            )

            usage.enter_scope(turn_id=result.turn_id)
            await _deliver_turn_result(whatsapp, from_number, result, db)

            # Update last_user_message_at
            await session_manager.touch_last_message(state.session_id, db)

            if result.crisis_detected:
                await _safe_send_text(
                    whatsapp, from_number, _say("crisis_text", profile), stage="crisis"
                )

            # Session close is triggered from run_extraction_for_turn, once
            # extraction learns session_end_suggested/goal_met — not here.

        else:
            # 7. Text message — prompt for voice note
            await _safe_send_text(
                whatsapp, from_number, _say("text_only", profile), stage="text_only"
            )

    except Exception:
        logger.error("Webhook processing error for %s", from_number, exc_info=True)
        if from_number:
            await _safe_send_text(
                whatsapp,
                from_number,
                _say("fallback_other", profile),
                stage="other",
            )

    return _ack()
