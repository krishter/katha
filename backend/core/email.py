"""
Email through AWS SES, ap-south-1 (data residency).

`send_email_ses` is the magic-link and upgrade sender, moved here from
core/auth.py (which re-exports it). `send_raw_email_ses` sends a built MIME
message: conversation cards carry their image inline and need their own
headers (docs/specs/CONVERSATION_CARDS.md §8.3).

With SES_MOCK on (the default everywhere but production) nothing is sent:
the email is logged and a mock message id returned.

"Accepted" is what SES tells us, not "delivered". Bounces and complaints
arrive later through SNS, which is not wired yet (§16, Q3).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from email.message import EmailMessage
from email.utils import make_msgid

import boto3

from config import settings

logger = logging.getLogger(__name__)


async def send_email_ses(to: str, subject: str, body_text: str, body_html: str) -> None:
    """
    Send an email via AWS SES (ap-south-1, for data residency).
    In dev (SES_MOCK=true), print the email instead of sending it.
    """
    if settings.SES_MOCK:
        logger.info("[SES_MOCK] To: %s | Subject: %s\n%s", to, subject, body_text)
        return

    client = boto3.client("ses", region_name=settings.AWS_S3_REGION)
    await asyncio.to_thread(
        client.send_email,
        Source=settings.SES_FROM_EMAIL,
        Destination={"ToAddresses": [to]},
        Message={
            "Subject": {"Data": subject},
            "Body": {
                "Text": {"Data": body_text},
                "Html": {"Data": body_html},
            },
        },
    )
    logger.info("Sent email via SES: %s", subject)


def build_mime(
    *,
    to: str,
    subject: str,
    body_text: str,
    body_html: str,
    inline_images: dict[str, bytes] | None = None,
    headers: dict[str, str] | None = None,
) -> EmailMessage:
    """
    A multipart/alternative message: plain text first, then HTML with any
    images attached inline (multipart/related) and referenced from the HTML
    as cid:<name>. Images go inline, not by URL: card images are private,
    and a presigned URL would stop working in the reader's inbox within the
    hour.
    """
    message = EmailMessage()
    message["From"] = settings.SES_FROM_EMAIL
    message["To"] = to
    message["Subject"] = subject
    message["Message-ID"] = make_msgid(domain=settings.SES_FROM_EMAIL.split("@")[-1])
    for name, value in (headers or {}).items():
        message[name] = value

    message.set_content(body_text)
    message.add_alternative(body_html, subtype="html")
    html_part = message.get_payload()[1]
    for cid, data in (inline_images or {}).items():
        html_part.add_related(
            data,
            maintype="image",
            subtype="png",
            cid=f"<{cid}>",
            filename=f"{cid}.png",
            disposition="inline",
        )
    return message


async def send_raw_email_ses(message: EmailMessage) -> str:
    """Send a built message. Returns SES's MessageId (a mock id under
    SES_MOCK)."""
    if settings.SES_MOCK:
        logger.info(
            "[SES_MOCK] Raw email To: %s | Subject: %s",
            message["To"],
            message["Subject"],
        )
        return f"mock-{uuid.uuid4()}"

    client = boto3.client("ses", region_name=settings.AWS_S3_REGION)
    response = await asyncio.to_thread(
        client.send_raw_email,
        Source=settings.SES_FROM_EMAIL,
        Destinations=[message["To"]],
        RawMessage={"Data": message.as_bytes()},
    )
    return response["MessageId"]


async def send_operator_alert(subject: str, body: str) -> None:
    """
    Tell the operator something needs a person: a card waiting for review,
    a parent who said never share. Never raises; an alert that cannot be
    sent is logged, since the thing it is about is already recorded.
    """
    if not settings.OPERATOR_EMAIL:
        logger.info("Operator alert (OPERATOR_EMAIL unset): %s — %s", subject, body)
        return
    try:
        await send_email_ses(
            settings.OPERATOR_EMAIL, f"[Katha] {subject}", body, f"<pre>{body}</pre>"
        )
    except Exception:
        logger.exception("Could not send operator alert: %s", subject)
