from __future__ import annotations

import asyncio
import hashlib
import logging
import secrets
from datetime import datetime, timedelta, timezone

import boto3
from fastapi import HTTPException, Request
from jose import JWTError, jwt
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.family_account import FamilyAccount
from models.magic_link_token import MagicLinkToken

logger = logging.getLogger(__name__)

_ALGORITHM = "HS256"
_COOKIE_NAME = "katha_token"
_MAGIC_LINK_RATE_LIMIT_WINDOW = timedelta(minutes=20)  # ~3/hour, evenly spaced


def _cookie_scope() -> dict:
    """
    The attributes that must be identical on set and delete.

    A browser matches a deletion against name + domain + path. Get the
    domain wrong on either side and the delete is a no-op against a cookie
    that is still live — after logout, and worse, after the user asked for
    all their data to be erased. Keeping the two callers on one dict is the
    cheapest way to stop them drifting apart.
    """
    scope: dict = {"path": "/"}
    if settings.COOKIE_DOMAIN:
        scope["domain"] = settings.COOKIE_DOMAIN
    return scope


def set_session_cookie(response, token: str) -> None:
    """Attach the session cookie. The only place it is created."""
    response.set_cookie(
        key=_COOKIE_NAME,
        value=token,
        httponly=True,
        secure=settings.ENVIRONMENT == "production",
        # app.katha.life and api.katha.life share a registrable domain, so
        # these are same-site requests and "lax" is correct.
        samesite="lax",
        max_age=60 * 60 * 24 * settings.JWT_EXPIRE_DAYS,
        **_cookie_scope(),
    )


def clear_session_cookie(response) -> None:
    """Remove the session cookie. The only place it is deleted."""
    response.delete_cookie(_COOKIE_NAME, **_cookie_scope())


def normalize_email(email: str) -> str:
    """
    The canonical form of an address: trimmed and lowercased.

    Email local-parts are technically case-sensitive; no mail provider a
    pilot family uses treats them that way, and a person typing their own
    address into a login box does not either. Registering as
    "Name@hotmail.com" and later logging in as "name@hotmail.com" has to
    reach the same account — under the enumeration-protection rule in
    send_magic_link, a lookup that misses is silently indistinguishable
    from an address that was never registered, and the person is told a
    link is on its way forever.
    """
    return email.strip().lower()


def hash_email(email: str) -> str:
    """SHA-256 of a lowercased email — used for consent-record audit trail."""
    return hashlib.sha256(normalize_email(email).encode()).hexdigest()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


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


async def find_account_by_email(email: str, db: AsyncSession) -> FamilyAccount | None:
    """
    Look up a family account by address, case-insensitively.

    Compares on lower(email) rather than normalizing only the input,
    because rows written before normalization existed may hold any casing
    — matching those needs no migration, just a lookup that does not care.
    """
    result = await db.execute(
        select(FamilyAccount)
        .where(func.lower(FamilyAccount.email) == normalize_email(email))
        .order_by(FamilyAccount.created_at)
        .limit(1)
    )
    return result.scalar_one_or_none()


async def send_magic_link(email: str, db: AsyncSession) -> bool:
    """
    Generate and email a magic link for the given address.

    No-ops silently for unknown emails so callers can always report success —
    this is the enumeration-protection boundary; nothing above this function
    should ever branch on "was this email found". Also no-ops (same
    silence) when the account requested a link within the rate-limit
    window, so a burst of requests can't be used as an email-bombing relay
    against arbitrary addresses.

    Returns False only when a send was attempted and the mail transport
    raised — the one case the caller may safely tell the user about,
    because it says nothing about whether the address is registered. Every
    silent no-op returns True.
    """
    account = await find_account_by_email(email, db)
    if account is None:
        # The address is logged because this branch is otherwise
        # undiagnosable: the caller is required to report success, so a
        # typo'd or differently-cased address looks exactly like a working
        # one from outside. Consistent with the rate-limit branch below,
        # which already logs it.
        logger.info("Magic link requested for unknown email %s — no-op", email)
        return True

    now = datetime.now(timezone.utc)
    last_requested = account.magic_link_last_requested_at
    if (
        last_requested is not None
        and now - last_requested < _MAGIC_LINK_RATE_LIMIT_WINDOW
    ):
        logger.info("Magic link request rate-limited for %s", email)
        return True

    token = secrets.token_hex(32)
    expires_at = now + timedelta(minutes=settings.MAGIC_LINK_EXPIRE_MINUTES)
    db.add(
        MagicLinkToken(
            email=account.email,
            token_hash=_hash_token(token),
            expires_at=expires_at,
        )
    )
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
    # The rate-limit stamp is written only after the mail transport has
    # accepted the message. Stamping before the send meant one rejection
    # — SES refusing an unverified recipient while in sandbox, a throttle,
    # an expired credential — silently locked the address out of
    # requesting another link for twenty minutes, and the caller is
    # required to report success, so the person saw "a login link is on
    # its way" and had no way to retry into it.
    try:
        await send_email_ses(
            account.email, "Your Katha login link", body_text, body_html
        )
    except Exception:
        logger.exception(
            "Could not send magic link to %s — not recording a rate-limit "
            "stamp, so they can retry immediately",
            account.email,
        )
        return False

    account.magic_link_last_requested_at = now
    db.add(account)
    await db.commit()
    return True


async def verify_magic_link(token: str, db: AsyncSession) -> tuple[str, str]:
    """
    Consume a magic link token: mark it used and return (email, user_id)
    for the linked family account. Raises HTTPException(400) if the token
    is missing, already used, or expired.
    """
    result = await db.execute(
        select(MagicLinkToken).where(
            MagicLinkToken.token_hash == _hash_token(token),
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

    account = await find_account_by_email(link.email, db)
    if account is None:
        await db.commit()
        raise HTTPException(status_code=400, detail="Invalid or expired link")

    await db.commit()
    return account.email, account.user_id


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
    logger.info("Sent magic link email via SES")


# ── Operator auth (docs/specs/ADMIN_METRICS.md §4) ───────────────────────────


def _admin_token_from(request: Request) -> str | None:
    """
    The token an operator presented, from either Authorization scheme.

    Bearer is for scripts. Basic exists so the HTML page works in a browser
    through its native login prompt; the username is ignored and the
    password is the token. Nothing else is read — in particular never the
    family session cookie, so a logged-in family is just as locked out as
    anyone else.
    """
    import base64
    import binascii

    header = request.headers.get("Authorization", "")
    scheme, _, value = header.partition(" ")
    if scheme.lower() == "bearer" and value:
        return value.strip()
    if scheme.lower() == "basic" and value:
        try:
            decoded = base64.b64decode(value.strip(), validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            return None
        _user, sep, password = decoded.partition(":")
        return password if sep else None
    return None


def _check_admin_token(request: Request, *, prompt_browser: bool) -> None:
    expected = settings.ADMIN_API_TOKEN
    if not expected:
        # Off by default: with no token configured the routes do not exist.
        raise HTTPException(status_code=404, detail="Not Found")
    presented = _admin_token_from(request)
    if presented is None or not secrets.compare_digest(
        presented.encode("utf-8"), expected.encode("utf-8")
    ):
        headers = (
            {"WWW-Authenticate": 'Basic realm="katha-admin"'}
            if prompt_browser
            else None
        )
        raise HTTPException(status_code=401, detail="Unauthorized", headers=headers)


def require_admin_token(request: Request) -> None:
    """FastAPI dependency for the operator JSON route."""
    _check_admin_token(request, prompt_browser=False)


def require_admin_token_browser(request: Request) -> None:
    """The same check for the HTML page, which asks the browser to prompt."""
    _check_admin_token(request, prompt_browser=True)
