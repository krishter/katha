from __future__ import annotations

import logging
import uuid
from pathlib import Path

logger = logging.getLogger(__name__)

_FIXTURE_AUDIO = Path(__file__).parent.parent / "tests" / "fixtures" / "sample.ogg"

# Minimal valid OGG header for tests when fixture file doesn't exist
_FALLBACK_AUDIO = b"OggS" + b"\x00" * 28


class StubWhatsAppAdapter:
    """
    No-op WhatsApp adapter for dev and tests.
    All methods log their arguments and return fake MessageSids.
    Configured via WHATSAPP_ADAPTER=stub.
    """

    def _fake_sid(self) -> str:
        return "STUB_MSG_" + uuid.uuid4().hex[:8]

    async def send_voice_note(
        self, to_number: str, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> str:
        sid = self._fake_sid()
        logger.info(
            "[STUB] send_voice_note to=%s mime=%s bytes=%d sid=%s",
            to_number,
            mime_type,
            len(audio_bytes),
            sid,
        )
        return sid

    async def send_image(
        self, to_number: str, image_bytes: bytes, caption: str = ""
    ) -> str:
        sid = self._fake_sid()
        logger.info(
            "[STUB] send_image to=%s bytes=%d caption=%r sid=%s",
            to_number,
            len(image_bytes),
            caption[:80],
            sid,
        )
        return sid

    async def send_text(
        self,
        to_number: str,
        text: str,
        template_name: str | None = None,
        template_sid: str | None = None,
        template_variables: dict | None = None,
    ) -> str:
        sid = self._fake_sid()
        logger.info(
            "[STUB] send_text to=%s text=%r template=%s sid=%s",
            to_number,
            text[:80],
            template_name,
            sid,
        )
        return sid

    async def download_voice_note(self, media_url: str) -> bytes:
        logger.info("[STUB] download_voice_note url=%s", media_url)
        if _FIXTURE_AUDIO.exists():
            return _FIXTURE_AUDIO.read_bytes()
        return _FALLBACK_AUDIO

    def validate_signature(
        self, request_url: str, params: dict, signature: str
    ) -> bool:
        logger.info("[STUB] validate_signature → True (stub always valid)")
        return True


def get_whatsapp_adapter():
    """
    FastAPI dependency. Returns StubWhatsAppAdapter or TwilioWhatsAppAdapter
    based on WHATSAPP_ADAPTER env var.
    """
    from config import settings

    if settings.WHATSAPP_ADAPTER == "stub":
        return StubWhatsAppAdapter()

    from adapters.whatsapp import TwilioWhatsAppAdapter

    return TwilioWhatsAppAdapter(
        account_sid=settings.TWILIO_ACCOUNT_SID,
        auth_token=settings.TWILIO_AUTH_TOKEN,
        from_number=settings.TWILIO_WHATSAPP_NUMBER,
    )
