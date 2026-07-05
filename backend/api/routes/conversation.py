import logging
from urllib.parse import quote

from fastapi import APIRouter, File, Response, UploadFile
from fastapi.responses import JSONResponse

from core import orchestrator

logger = logging.getLogger(__name__)

router = APIRouter()


@router.post("/turn")
async def conversation_turn(audio: UploadFile = File(...)) -> Response:
    try:
        audio_bytes = await audio.read()
        result = await orchestrator.process_voice_turn(audio_bytes)
        return Response(
            content=result.response_audio,
            media_type="audio/wav",
            headers={
                # URL-encode transcript so non-ASCII chars are header-safe
                "X-Transcript": quote(result.transcript),
                "X-Language": result.detected_language,
            },
        )
    except Exception as exc:
        logger.exception("Error processing conversation turn")
        return JSONResponse(status_code=500, content={"error": str(exc)})
