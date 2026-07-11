from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import APIRouter, Depends, File, Form, Response, UploadFile
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession

from core import orchestrator, session_manager
from models.db import get_db
from prompts.system_prompt import UserProfile

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/session")
async def create_session(
    user_id: str = Form(...),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Create a new session and return the session_id."""
    state = await session_manager.start_session(user_id, db)
    return {"session_id": state.session_id, "domain": state.domain}


@router.post("/turn")
async def conversation_turn(
    audio: UploadFile = File(...),
    session_id: str = Form(...),
    user_name: str = Form(default="Friend"),
    preferred_language: str = Form(default="hi-IN"),
    onboarding_context: str = Form(default=""),
    db: AsyncSession = Depends(get_db),
) -> Response:
    try:
        audio_bytes = await audio.read()
        user_profile = UserProfile(
            name=user_name,
            preferred_language=preferred_language,
            onboarding_context=onboarding_context,
        )
        result = await orchestrator.process_voice_turn(
            audio_bytes, session_id, user_profile, db
        )
        return Response(
            content=result.response_audio,
            media_type="audio/wav",
            headers={
                "X-Transcript": quote(result.transcript),
                "X-Language": result.detected_language,
                "X-Energy": result.extraction_json.get("energy_signal", "high"),
                "X-Session-End": str(
                    result.extraction_json.get("session_end_suggested", False)
                ).lower(),
                "X-Crisis": str(result.crisis_detected).lower(),
            },
        )
    except Exception as exc:
        logger.exception("Error processing conversation turn")
        return JSONResponse(status_code=500, content={"error": str(exc)})
