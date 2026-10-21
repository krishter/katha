from __future__ import annotations

import asyncio
import json
import logging
from typing import TYPE_CHECKING, Protocol, runtime_checkable
from urllib.parse import urlsplit

import httpx
from twilio.request_validator import RequestValidator
from twilio.rest import Client as TwilioClient

from config import settings
from core import redact, twilio_cleanup, usage
from media import storage

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class TemplateMediaHostMismatch(RuntimeError):
    """
    A presigned URL does not sit on the host the approved template builds
    its media URL from.

    Raised rather than worked around. The template holds
    "https://<fixed host>/{{1}}" and we supply only the tail, so a
    mismatch means the URL Meta assembles points at a host we did not sign
    for. That fetch fails at Meta, which tells us nothing — the message is
    simply never delivered — so the only place this can be caught is here,
    before sending.
    """


def _template_media_suffix(presigned_url: str) -> str:
    """
    The part of a presigned URL that goes into the template's media
    variable: everything after the host, with no leading slash.

    Twilio only permits a variable in a template media URL after a fixed
    domain, so the approved template is
    "https://<TWILIO_TEMPLATE_MEDIA_HOST>/{{1}}" and {{1}} is the key plus
    the signing query string. The query string is not optional — strip it
    and the object is private, so Meta's fetcher gets a 403 and the card
    silently never arrives.

    Raises TemplateMediaHostMismatch if the URL is not on the configured
    host, because the template would then assemble a URL we never signed.
    """
    if not settings.TWILIO_TEMPLATE_MEDIA_HOST:
        raise TemplateMediaHostMismatch(
            "TWILIO_TEMPLATE_MEDIA_HOST is not set, so the host half of the "
            "template's media URL is unknown and cannot be checked against "
            f"the presigned URL ({urlsplit(presigned_url).netloc})"
        )

    parts = urlsplit(presigned_url)
    if parts.netloc != settings.TWILIO_TEMPLATE_MEDIA_HOST:
        raise TemplateMediaHostMismatch(
            f"presigned URL host {parts.netloc!r} does not match the host the "
            f"approved template builds its media URL from "
            f"({settings.TWILIO_TEMPLATE_MEDIA_HOST!r}). Meta would fetch "
            "https://"
            f"{settings.TWILIO_TEMPLATE_MEDIA_HOST}/<this object>, which is "
            "not where it lives, and the card would never arrive."
        )

    suffix = parts.path.lstrip("/")
    return f"{suffix}?{parts.query}" if parts.query else suffix


@runtime_checkable
class WhatsAppAdapter(Protocol):
    async def send_voice_note(
        self, to_number: str, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> tuple[str, str]:
        """Upload audio to S3 (private) and send Twilio a short-lived
        presigned URL. Returns (MessageSid, s3_key) — callers must track
        the key so the object can be found and deleted later."""
        ...

    async def send_image(
        self,
        to_number: str,
        s3_key: str,
        caption: str = "",
        template_sid: str | None = None,
        template_variables: dict | None = None,
        media_variable: str = "1",
        operation: str = "outbound_card",
    ) -> str:
        """
        Send Twilio a short-lived presigned URL for an image ALREADY in S3.
        Returns MessageSid.

        `template_sid` makes this an approved-template send. That is not
        optional for the memory card: the adult child never messages
        Katha's number, so no 24-hour window is ever open for them and a
        free-form media message is rejected by Meta with 63016. Without a
        template the card is accepted by Twilio and then silently dropped.

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

    async def delete_message(self, message_sid: str) -> None:
        """Delete a message and its media from Twilio. Raises on failure."""
        ...

    def validate_signature(
        self, request_url: str, params: dict, signature: str
    ) -> bool:
        """Validate Twilio X-Twilio-Signature."""
        ...


class TwilioWhatsAppAdapter:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        from_number: str,
        messaging_service_sid: str = "",
    ) -> None:
        self._client = TwilioClient(account_sid, auth_token)
        self._auth_token = auth_token
        self._from = from_number  # e.g. "whatsapp:+14155238886"
        self._messaging_service_sid = messaging_service_sid

    def _sender(self) -> dict:
        """
        How this message identifies its sender.

        The production number lives in a Messaging Service, and Twilio wants
        `messaging_service_sid` rather than `from_` for those — passing the
        bare number sends from outside the service and loses its sender
        pool, compliance settings and fallback behaviour. Falls back to
        `from_` so the sandbox number keeps working unconfigured.
        """
        if self._messaging_service_sid:
            return {"messaging_service_sid": self._messaging_service_sid}
        return {"from_": self._from}

    def _status_callback(self) -> dict:
        """
        Where Twilio reports what became of the message.

        Twilio accepts a WhatsApp message and returns a SID immediately;
        whether Meta actually delivered it arrives later, out of band. With
        nothing listening, a message rejected with 63016 is indistinguish-
        able from one that arrived, which is how every memory card could
        fail for weeks while the database recorded them as delivered.
        """
        if not settings.PUBLIC_BASE_URL:
            return {}
        return {"status_callback": f"{settings.PUBLIC_BASE_URL}/webhook/twilio/status"}

    async def send_voice_note(
        self, to_number: str, audio_bytes: bytes, mime_type: str = "audio/ogg"
    ) -> tuple[str, str]:
        import uuid

        key = f"audio/katha-{uuid.uuid4().hex[:12]}.ogg"
        s3_key = await storage.upload_media(audio_bytes, key, mime_type)
        presigned_url = await storage.generate_presigned_url(s3_key)
        msg = await asyncio.to_thread(
            self._client.messages.create,
            **self._sender(),
            to=f"whatsapp:{to_number}",
            media_url=[presigned_url],
            **self._status_callback(),
        )
        logger.info("Sent voice note to %s: %s", redact.phone(to_number), msg.sid)
        await usage.record_outbound("outbound_voice", msg.sid)
        await twilio_cleanup.queue(msg.sid, direction=twilio_cleanup.OUTBOUND)
        return msg.sid, s3_key

    async def send_image(
        self,
        to_number: str,
        s3_key: str,
        caption: str = "",
        template_sid: str | None = None,
        template_variables: dict | None = None,
        media_variable: str = "1",
        operation: str = "outbound_card",
    ) -> str:
        presigned_url = await storage.generate_presigned_url(s3_key)
        kwargs: dict = {
            **self._sender(),
            "to": f"whatsapp:{to_number}",
            **self._status_callback(),
        }
        if template_sid:
            # The template carries the copy; the image rides in as a
            # variable because the card differs every day. `media_variable`
            # is which numbered placeholder the approved template puts the
            # media URL in.
            variables = dict(template_variables or {})
            variables[media_variable] = _template_media_suffix(presigned_url)
            kwargs["content_sid"] = template_sid
            # JSON, not str(): a Python repr with single quotes is not
            # parsed by Twilio and the template silently substitutes
            # nothing (same trap as send_text).
            kwargs["content_variables"] = json.dumps(variables)
        else:
            kwargs["media_url"] = [presigned_url]
            kwargs["body"] = caption
        msg = await asyncio.to_thread(self._client.messages.create, **kwargs)
        logger.info(
            "Sent image to %s: %s (template=%s)",
            redact.phone(to_number),
            msg.sid,
            template_sid or "none — free-form, needs an open 24h window",
        )
        await usage.record_outbound(operation, msg.sid, template_sid)
        await twilio_cleanup.queue(msg.sid, direction=twilio_cleanup.OUTBOUND)
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
            **self._sender(),
            "to": f"whatsapp:{to_number}",
            **self._status_callback(),
        }
        if template_sid:
            kwargs["content_sid"] = template_sid
            if template_variables:
                # JSON, not str(). str() on a dict yields a Python repr with
                # single quotes — {'1': 'Lakshmi'} — which Twilio does not
                # parse, so the template either fails or substitutes nothing.
                # Every variable in the welcome templates is a name.
                kwargs["content_variables"] = json.dumps(template_variables)
        else:
            kwargs["body"] = text
        msg = await asyncio.to_thread(self._client.messages.create, **kwargs)
        logger.info("Sent text to %s: %s", redact.phone(to_number), msg.sid)
        await usage.record_outbound(
            "outbound_template" if template_sid else "outbound_text",
            msg.sid,
            template_sid,
        )
        await twilio_cleanup.queue(msg.sid, direction=twilio_cleanup.OUTBOUND)
        return msg.sid

    async def delete_message(self, message_sid: str) -> None:
        """
        Delete a message and its media from Twilio (P6, D8). Media first:
        each item is its own resource, and deleting it is what removes the
        stored voice note. Raises on failure; core/twilio_cleanup.py
        queues the retry.
        """

        def _delete() -> None:
            message = self._client.messages(message_sid)
            for media in message.media.list():
                message.media(media.sid).delete()
            message.delete()

        await asyncio.to_thread(_delete)
        logger.info("Deleted %s and its media from Twilio", message_sid)

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
