from __future__ import annotations

import logging

from fastapi import APIRouter, BackgroundTasks, Depends, Query, Request, Response
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.whatsapp_stub import get_whatsapp_adapter
from config import settings
from core import orchestrator, session_manager
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
    """
    try:
        # 1. Parse form payload
        form = await request.form()
        params = dict(form)

        # 2. Validate Twilio signature
        signature = request.headers.get("X-Twilio-Signature", "")
        url = str(request.url)
        if not whatsapp.validate_signature(url, params, signature):
            logger.warning("Invalid Twilio signature from %s", request.client)
            return Response(status_code=403, content="Forbidden")

        # 3. Parse message fields
        raw_from = params.get("From", "")
        from_number = raw_from.replace("whatsapp:", "")
        media_url = params.get("MediaUrl0", "")
        media_type = params.get("MediaContentType0", "")
        message_sid = params.get("MessageSid", "")

        logger.info(
            "Webhook: from=%s media_type=%s sid=%s",
            from_number,
            media_type,
            message_sid,
        )

        # 4. Look up active session by WhatsApp number
        state = await session_manager.get_active_session_by_number(from_number, db)
        if state is None:
            logger.info("No active session for %s", from_number)
            await whatsapp.send_text(
                from_number, "Hi! Your session isn't scheduled yet."
            )
            return Response(status_code=200, content="OK")

        # 5. Handle voice note
        if media_url and "audio" in media_type:
            audio_bytes = await whatsapp.download_voice_note(media_url)
            user_profile = await _load_user_profile_for_session(state, db)

            result = await orchestrator.process_voice_turn(
                audio_bytes,
                state.session_id,
                user_profile,
                db,
                background_tasks,
            )

            await whatsapp.send_voice_note(
                from_number, result.response_audio, mime_type="audio/ogg"
            )

            # Update last_user_message_at
            await session_manager.touch_last_message(state.session_id, db)

            if result.crisis_detected:
                await whatsapp.send_text(from_number, _CRISIS_TEXT)

            if result.session_state.session_end_suggested:
                background_tasks.add_task(
                    orchestrator.close_and_process_session,
                    state.session_id,
                    result.transcript,
                    result.extraction_json,
                    db,
                )

        else:
            # 6. Text message — prompt for voice note
            await whatsapp.send_text(from_number, _TEXT_ONLY_REPLY)

    except Exception:
        logger.exception("Webhook processing error")
        # Always return 200 to prevent Twilio retries

    return Response(status_code=200, content="OK")
