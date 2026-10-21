from __future__ import annotations

import logging
import re
import uuid
from datetime import datetime, time
from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from core import auth, family_number, parent_consent
from core.auth import get_current_user
from media import tts, voices
from models.consent_record import ConsentRecord
from models.db import get_db
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel
from prompts.system_prompt import SUPPORTED_LANGUAGES

logger = logging.getLogger(__name__)

router = APIRouter()

# Bumped from 1.0 in Sprint 1 S2.3. The checklist text materially changed:
# it now links to a deletion control that actually exists (F-01), and it no
# longer implies the buyer can consent on the parent's behalf — it states
# that Katha will ask the parent directly (F-02). consent_version exists to
# record which wording a given record attests to, so records written against
# the old text keep saying 1.0. No backfill.
_CONSENT_VERSION = "1.1"


def _wa_me_link() -> str:
    """Click-to-chat link addressing Katha's sender number."""
    sender = settings.TWILIO_WHATSAPP_NUMBER.replace("whatsapp:", "").lstrip("+")
    return f"https://wa.me/{sender}?text={quote('Namaste')}" if sender else ""


_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


def _validate_e164(value: str, field_name: str) -> None:
    if not _E164_RE.match(value):
        raise HTTPException(
            status_code=422,
            detail=f"{field_name} must be in E.164 format, e.g. +919876543210",
        )


def _validate_session_time(value: str) -> time:
    try:
        return datetime.strptime(value, "%H:%M").time()
    except ValueError as exc:
        raise HTTPException(
            status_code=422, detail="session_time must be HH:MM, e.g. 09:30"
        ) from exc


@router.post("/onboarding/start")
async def onboarding_start(
    email: str = Form(...), db: AsyncSession = Depends(get_db)
) -> dict:
    """
    Step 1 of onboarding: email registration, and the universal email
    entry point — the login page posts here too, because this is the only
    endpoint that handles a first-time visitor and a returning one alike.
    No auth required. Always returns 200.
    """
    account = await auth.find_account_by_email(email, db)

    if account is None:
        db.add(
            FamilyAccount(
                email=auth.normalize_email(email),
                user_id=f"user_{uuid.uuid4().hex}",
                plan="free",
                onboarding_complete=False,
            )
        )
        await db.commit()
        status = "new"
    else:
        status = "existing" if account.onboarding_complete else "incomplete"

    # Every one of the three cases needs a link in the inbox, including
    # "incomplete". That case used to return here without sending one, and
    # both callers of this endpoint tell the visitor a link is on its way
    # regardless of status — so a family that got as far as registering
    # their parent and then stopped before the consent step was locked out
    # permanently, looking at "check your email" with nothing ever
    # arriving. Resuming an abandoned wizard needs exactly the same
    # authenticated session as logging in, so it needs the same link.
    sent = await auth.send_magic_link(email, db)

    return {
        "status": status,
        # False only when the mail transport itself refused the message.
        # It reveals nothing about whether the address is registered —
        # send_magic_link returns True for every silent no-op — so the UI
        # can safely say "we could not send it" instead of claiming a
        # delivery that did not happen.
        "link_sent": sent,
    }


@router.post("/onboarding/profile")
async def onboarding_profile(
    parent_name: str = Form(...),
    whatsapp_number: str = Form(...),
    family_whatsapp_number: str = Form(...),
    preferred_language: str = Form(...),
    session_time: str = Form(...),
    pronouns: str = Form(default=""),
    onboarding_context: str = Form(default=""),
    # Katha's persona. Defaulted so an older client that never sends it
    # keeps the voice every parent has heard so far.
    katha_gender: str = Form(default=voices.DEFAULT_GENDER),
    # What she calls the child who set this up. The welcome names them
    # (P3 3.6): "Priya set this up for you" is why she should listen.
    child_name: str = Form(default=""),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Steps 2 + 3 combined: parent profile + seed context."""
    if katha_gender not in voices.GENDERS:
        raise HTTPException(
            status_code=422, detail="katha_gender must be 'female' or 'male'"
        )
    # Every fixed thing Katha says comes from a message file per language
    # (speech/catalogue.py). A language without one would hear English.
    if preferred_language not in SUPPORTED_LANGUAGES:
        raise HTTPException(
            status_code=422,
            detail="preferred_language must be one of "
            + ", ".join(SUPPORTED_LANGUAGES),
        )
    _validate_e164(whatsapp_number, "whatsapp_number")
    _validate_e164(family_whatsapp_number, "family_whatsapp_number")
    scheduled_time = _validate_session_time(session_time)

    user_id = current_user["user_id"]
    result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = result.scalar_one_or_none()

    # One profile per number (P3, M-03): the webhook knows her only by the
    # number, so a second family registering it would hear her stories.
    clash = await db.execute(
        select(UserProfileModel.user_id)
        .where(UserProfileModel.whatsapp_number == whatsapp_number)
        .where(UserProfileModel.user_id != user_id)
    )
    if clash.first() is not None:
        raise HTTPException(
            status_code=409,
            detail="This WhatsApp number is already registered with Katha.",
        )

    is_new = profile is None
    if profile is None:
        profile = UserProfileModel(user_id=user_id)
        db.add(profile)
    elif profile.whatsapp_number != whatsapp_number:
        # Her consent is hers, given from her number. Once she has agreed,
        # moving it is an operator's job (scripts/change_parent_number.py),
        # never a form field: a typo here would hand her stories to someone
        # else's phone.
        if await parent_consent.has_granted(user_id, db):
            raise HTTPException(
                status_code=409,
                detail="Contact Katha to change your parent's number.",
            )
        # Not yet agreed: whatever was said to the old number was said to
        # someone else, so the new number starts from the welcome.
        profile.parent_consent_asks = 0
        profile.parent_consent_state = parent_consent.STATE_ASKING

    if (
        is_new
        or profile.preferred_language != preferred_language
        or profile.katha_gender != katha_gender
    ):
        # Pin her voice (media/tts.py) from language and persona. Re-sent
        # setup that changes neither leaves the pin alone, so a family
        # editing the context never changes the voice.
        profile.tts_voice = tts.default_voice(preferred_language, katha_gender).pin
    if profile.family_whatsapp_number != family_whatsapp_number:
        # A new number has to be proven again (core/family_number.py).
        profile.family_number_confirmed_at = None
    profile.name = parent_name
    profile.whatsapp_number = whatsapp_number
    profile.family_whatsapp_number = family_whatsapp_number
    profile.preferred_language = preferred_language
    profile.scheduled_time = scheduled_time
    # Optional on purpose. An empty value means "not stated", which the
    # prompt handles by not using gendered forms at all — a blank is a
    # safer record than a guess, so nothing here coerces a choice.
    profile.pronouns = pronouns.strip()
    profile.katha_gender = katha_gender
    profile.onboarding_context = onboarding_context

    account = (
        await db.execute(select(FamilyAccount).where(FamilyAccount.user_id == user_id))
    ).scalar_one_or_none()
    if account is not None and child_name.strip():
        account.child_name = child_name.strip()

    try:
        await db.commit()
    except IntegrityError as exc:
        # Two families registering the same number at the same moment: the
        # check above passed for both, and the unique index caught one.
        await db.rollback()
        raise HTTPException(
            status_code=409,
            detail="This WhatsApp number is already registered with Katha.",
        ) from exc
    return {"status": "ok"}


@router.post("/onboarding/consent")
async def onboarding_consent(
    request: Request,
    consent_given: bool = Form(...),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """Step 4: DPDP consent. Required before onboarding can complete."""
    if not consent_given:
        raise HTTPException(status_code=400, detail="Consent is required to continue.")

    user_id = current_user["user_id"]
    email = current_user["sub"]

    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash=auth.hash_email(email),
            consent_version=_CONSENT_VERSION,
            ip_address=request.client.host if request.client else None,
            user_agent=request.headers.get("user-agent"),
        )
    )

    account_result = await db.execute(
        select(FamilyAccount).where(FamilyAccount.user_id == user_id)
    )
    account = account_result.scalar_one_or_none()
    if account is None:
        raise HTTPException(status_code=404, detail="Family account not found")
    account.onboarding_complete = True
    # The link the child taps from their own WhatsApp to prove the family
    # number is theirs. Cards wait for it; email does not.
    confirm_code = family_number.issue_code(account)

    profile_result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    if profile is None:
        raise HTTPException(
            status_code=400, detail="Complete the profile step before consenting."
        )

    await db.commit()

    session_time_str = profile.scheduled_time.strftime("%H:%M")
    logger.info(
        "First session scheduled for user %s at %s IST", user_id, session_time_str
    )

    return {
        "status": "complete",
        "parent_name": profile.name,
        "session_time": session_time_str,
        # The link the child forwards. Katha cannot open the conversation
        # (Meta 63049), so nothing happens until the parent taps this.
        "parent_whatsapp_link": _wa_me_link(),
        "family_confirm_link": family_number.confirm_link(confirm_code),
    }
