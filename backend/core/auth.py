from __future__ import annotations

import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import boto3
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.family_account import FamilyAccount
from models.magic_link_token import MagicLinkToken

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_COOKIE_NAME = "katha_token"


def hash_email(email: str) -> str:
    """SHA-256 of a lowercased email — used for consent-record audit trail."""
    return hashlib.sha256(email.lower().encode()).hexdigest()


def create_jwt(email: str, user_id: str) -> str:
    """Issue a signed JWT. Payload: {sub: email, user_id, exp}."""
    expire = datetime.now(timezone.utc) + timedelta(days=settings.JWT_EXPIRE_DAYS)
    payload = {"sub": email, "user_id": user_id, "exp": expire}
    return jwt.encode(payload, settings.JWT_SECRET, algorithm=_ALGORITHM)


def verify_jwt(token: str) -> dict:
    """Decode and verify a JWT. Raise HTTPException(401) if expired or invalid."""
    try:
        return jwt.decode(token, settings.JWT_SECRET, algorithms=[_ALGORITHM])
    except JWTError as exc:
        raise HTTPException(
            status_code=401, detail="Invalid or expired session"
        ) from exc


def get_current_user(request: Request) -> dict:
    """FastAPI dependency. Extract and verify the JWT from the katha_token cookie."""
    token = request.cookies.get(_COOKIE_NAME)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return verify_jwt(token)


async def send_magic_link(email: str, db: AsyncSession) -> None:
    """
    Generate and email a magic link for the given address.
    No-ops silently for unknown emails so callers can always report success —
    this is the enumeration-protection boundary; nothing above this function
    should ever branch on "was this email found".
    """
    result = await db.execute(select(FamilyAccount).where(FamilyAccount.email == email))
    account = result.scalar_one_or_none()
    if account is None:
        logger.info("Magic link requested for unknown email — no-op")
        return

    token = secrets.token_hex(32)
    expires_at = datetime.now(timezone.utc) + timedelta(
        minutes=settings.MAGIC_LINK_EXPIRE_MINUTES
    )
    db.add(MagicLinkToken(email=email, token=token, expires_at=expires_at))
    await db.commit()

    url = f"{settings.APP_BASE_URL}/family/auth/verify?token={token}"
    body_text = (
        "Click to log in to Katha (link expires in 15 minutes):\n"
        f"{url}\n\n"
        "If you didn't request this, ignore this email."
    )
    body_html = (
        "<p>Click to log in to Katha (link expires in 15 minutes):</p>"
        f'<p><a href="{url}">{url}</a></p>'
        "<p>If you didn't request this, ignore this email.</p>"
    )
    send_email_ses(email, "Your Katha login link", body_text, body_html)


async def verify_magic_link(token: str, db: AsyncSession) -> tuple[str, str]:
    """
    Consume a magic link token: mark it used and return (email, user_id)
    for the linked family account. Raises HTTPException(400) if the token
    is missing, already used, or expired.
    """
    result = await db.execute(
        select(MagicLinkToken).where(
            MagicLinkToken.token == token,
            MagicLinkToken.used.is_(False),
            MagicLinkToken.expires_at > datetime.now(timezone.utc),
        )
    )
    link = result.scalar_one_or_none()
    if link is None:
        raise HTTPException(status_code=400, detail="Invalid or expired link")

    await db.execute(
        update(MagicLinkToken).where(MagicLinkToken.id == link.id).values(used=True)
    )

    account_result = await db.execute(
        select(FamilyAccount).where(FamilyAccount.email == link.email)
    )
    account = account_result.scalar_one_or_none()
    if account is None:
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired link")

    await db.commit()
    return account.email, account.user_id


def send_email_ses(to: str, subject: str, body_text: str, body_html: str) -> None:
    """
    Send an email via AWS SES (ap-south-1, for data residency).
    In dev (SES_MOCK=true), print the email instead of sending it.
    """
    if settings.SES_MOCK:
        logger.info("[SES_MOCK] To: %s | Subject: %s\n%s", to, subject, body_text)
        return

    client = boto3.client("ses", region_name=settings.AWS_S3_REGION)
    client.send_email(
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
    logger.info("Sent magic link email via SES")
