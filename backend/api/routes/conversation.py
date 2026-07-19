from __future__ import annotations

import logging
from urllib.parse import quote

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    Response,
    UploadFile,
)
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
    background_tasks: BackgroundTasks = BackgroundTasks(),
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
            audio_bytes, session_id, user_profile, db, background_tasks
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


@router.post("/close")
async def close_session(
    session_id: str = Form(...),
    extraction_json_str: str = Form(default="{}"),
    transcript: str = Form(default=""),
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Explicit session close endpoint. Triggers post-session processing as a
    background task (story extraction + entity extraction + memory card
    generation and delivery). Called by the client when the
    session_end_suggested flag is seen in response headers, or when the
    user explicitly ends the session.
    """
    import json as _json

    try:
        extraction_json = _json.loads(extraction_json_str)
    except _json.JSONDecodeError:
        extraction_json = {}

    background_tasks.add_task(
        orchestrator.close_and_process_session,
        session_id,
        transcript,
        extraction_json,
        db,
    )
    logger.info("Session %s closed; post-session processing scheduled", session_id)
    return {"status": "closed", "session_id": session_id}
