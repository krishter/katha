from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.whatsapp_stub import get_whatsapp_adapter
from config import settings
from core import orchestrator, session_manager
from core.fallback_audio import FailureStage, get_fallback_text
from models.db import get_db
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


async def _load_user_profile_for_session(
    session_state: session_manager.SessionState, db: AsyncSession
) -> UserProfile:
    """Load UserProfile from user_profiles table for the given session."""
    from sqlalchemy import select

    from models.user_profile import UserProfile as UserProfileModel

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
        )
    return UserProfile(
        name=row.name,
        preferred_language=row.preferred_language,
        onboarding_context=row.onboarding_context or "",
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


@router.post("/webhook/whatsapp")
async def whatsapp_incoming(
    request: Request,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
    whatsapp=Depends(get_whatsapp_adapter),
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
                return Response(status_code=200, content="OK")

        # 5. Look up active session by WhatsApp number
        state = await session_manager.get_active_session_by_number(from_number, db)
        if state is None:
            logger.info("No active session for %s", from_number)
            await _safe_send_text(
                whatsapp, from_number, _NOT_SCHEDULED_TEXT, stage="no_active_session"
            )
            return Response(status_code=200, content="OK")

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

    return Response(status_code=200, content="OK")
