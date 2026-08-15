from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable

import httpx
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient

from media import storage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


@runtime_checkable
class WhatsAppAdapter(Protocol):
    async def send_voice_note(
        self, to_number: str, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> tuple[str, str]:
        """Upload audio to S3 (private) and send Twilio a short-lived
        presigned URL. Returns (MessageSid, s3_key) — callers must track
        the key so the object can be found and deleted later."""
        ...

    async def send_image(self, to_number: str, s3_key: str, caption: str = "") -> str:
        """
        Send Twilio a short-lived presigned URL for an image ALREADY in S3.
        Returns MessageSid.

        Takes a key rather than bytes deliberately. This used to accept
        bytes and upload them itself, which meant every memory card was
        stored twice — once by the caller under cards/{session_id}.png and
        tracked in memory_cards.image_s3_key, then again here under a random
        cards/katha-{uuid}.png that nothing recorded and deletion could
        never find (S2.4b). The caller already has the key.
        """
        ...

    async def send_text(
        self,
        to_number: str,
        text: str,
        template_name: str | None = None,
        template_sid: str | None = None,
        template_variables: dict | None = None,
    ) -> str:
        """Send a text message. Returns MessageSid."""
        ...

    async def download_voice_note(self, media_url: str) -> bytes:
        """Download media from a Twilio media URL. Returns raw audio bytes."""
        ...

    def validate_signature(
        self, request_url: str, params: dict, signature: str
    ) -> bool:
        """Validate Twilio X-Twilio-Signature."""
        ...


class TwilioWhatsAppAdapter:
    def __init__(self, account_sid: str, auth_token: str, from_number: str) -> None:
        self._client = TwilioClient(account_sid, auth_token)
        self._auth_token = auth_token
        self._from = from_number  # e.g. "whatsapp:+14155238886"

    async def send_voice_note(
        self, to_number: str, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> tuple[str, str]:
        import uuid

        key = f"audio/katha-{uuid.uuid4().hex[:12]}.ogg"
        s3_key = await storage.upload_media(audio_bytes, key, mime_type)
        presigned_url = await storage.generate_presigned_url(s3_key)
        msg = await asyncio.to_thread(
            self._client.messages.create,
            from_=self._from,
            to=f"whatsapp:{to_number}",
            media_url=[presigned_url],
        )
        logger.info("Sent voice note to %s: %s", to_number, msg.sid)
        return msg.sid, s3_key

    async def send_image(self, to_number: str, s3_key: str, caption: str = "") -> str:
        presigned_url = await storage.generate_presigned_url(s3_key)
        msg = await asyncio.to_thread(
            self._client.messages.create,
            from_=self._from,
            to=f"whatsapp:{to_number}",
            media_url=[presigned_url],
            body=caption,
        )
        logger.info("Sent image to %s: %s", to_number, msg.sid)
        return msg.sid

    async def send_text(
        self,
        to_number: str,
        text: str,
        template_name: str | None = None,
        template_sid: str | None = None,
        template_variables: dict | None = None,
    ) -> str:
        kwargs: dict = {
            "from_": self._from,
            "to": f"whatsapp:{to_number}",
        }
        if template_sid:
            kwargs["content_sid"] = template_sid
            if template_variables:
                kwargs["content_variables"] = str(template_variables)
        else:
            kwargs["body"] = text
        msg = await asyncio.to_thread(self._client.messages.create, **kwargs)
        logger.info("Sent text to %s: %s", to_number, msg.sid)
        return msg.sid

    async def download_voice_note(self, media_url: str) -> bytes:
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                media_url,
                auth=(self._client.username, self._auth_token),
                follow_redirects=True,
            )
            resp.raise_for_status()
            return resp.content

    def validate_signature(
        self, request_url: str, params: dict, signature: str
    ) -> bool:
        validator = RequestValidator(self._auth_token)
        return validator.validate(request_url, params, signature)
