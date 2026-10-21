"""
She asks Katha to stop (PRE_PILOT_FIX_PLAN P4, M-02; decision D4).

The welcome promises it out loud: "You can stop at any time, just by
telling me to, and I will not trouble you again." DPDP s.6(4) asks the
same, that withdrawing be as easy as agreeing. So every message from a
parent who has agreed is checked, in the webhook, before anything else
happens to it:

  1. Fixed phrases first ("stop recording", "band karo", "இனிமே
     வேண்டாம்"...), counted only in a short message or at its start. A story
     with "stop" in it must not end her service.
  2. Otherwise, only if a cheap keyword prefilter sees a stop-like word,
     the classifier (core/parent_answers.py, WITHDRAW), which must quote
     her.

A crisis phrase is never a withdrawal: the crisis protocol answers first.

A withdrawal (D4): the session ends with no outputs, unsent cards are
withdrawn, a parent-1.0-withdrawn consent record points at the turn that
carries her words, she is told once in her language, the operator and the
family are told, and everything is erased after seven days unless an
operator clears the hold. The hold is there for a misheard "stop".
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import state as card_state
from core import conversation_policy, parent_answers, parent_consent, session_manager
from core.email import send_email_ses, send_operator_alert
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.family_account import FamilyAccount
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

HOLD = timedelta(days=7)
# A fixed phrase counts in a message this short, or when it opens one.
_SHORT_WORDS = 12
_OPENING_CHARS = 25

# Asking to stop for good, in each language Katha speaks, native script and
# romanised. Machine-drafted with the rest of the catalogue (D1).
_WITHDRAW_PATTERNS = [
    # English
    r"\bstop recording\b",
    r"\bdon.?t (call|message) me (again|any ?more)\b",
    r"\bdo not (call|message) me (again|any ?more)\b",
    r"\bdelete (everything|it all|all of it|my (data|stories|recordings))\b",
    r"\bi (don.?t|do not) want (this|to do this) any ?more\b",
    r"\bleave me alone\b",
    # Hindi / Urdu, romanised and native
    r"\bband kar(o| do| dijiye)\b",
    r"\bmujhe nahi?n? chahiye\b",
    r"\brecord mat karo\b",
    r"\bsab (delete|mita) (karo|do|dijiye)\b",
    r"बंद कर(ो| दो| दीजिए)",
    r"मुझे नहीं चाहिए",
    r"रिकॉर्ड मत करो",
    r"सब मिटा दो",
    r"بند کر(و| دو| دیجیے)",
    r"مجھے نہیں چاہیے",
    # Tamil
    r"நிறுத்து(ங்க|ங்கள்)?",
    r"இனிமே வேண்டாம்",
    r"பதிவு செய்ய வேண்டாம்",
    r"\bniruthunga\b",
    r"\bini(me)? venda(m)?\b",
    # Telugu
    r"ఆపండి",
    r"ఇక వద్దు",
    r"రికార్డ్ చేయొద్దు",
    r"\baapandi\b",
    r"\bika vaddu\b",
    # Kannada
    r"ನಿಲ್ಲಿಸಿ",
    r"ಇನ್ನು ಬೇಡ",
    r"\bnillisi\b",
    r"\binnu beda\b",
    # Malayalam
    r"നിർത്ത(ൂ|ുക)",
    r"ഇനി വേണ്ട",
    r"\bini venda\b",
    # Marathi
    r"बंद करा",
    r"मला नको",
    r"\bband kara\b",
    r"\bmala nako\b",
    # Bengali / Assamese
    r"বন্ধ কর(ুন|ো)",
    r"আর চাই না",
    r"বন্ধ কৰক",
    r"মোক নালাগে",
    r"\bbondho koro?(un)?\b",
    # Gujarati
    r"બંધ કરો",
    r"મારે નથી જોઈતું",
    # Punjabi
    r"ਬੰਦ ਕਰੋ",
    r"ਮੈਨੂੰ ਨਹੀਂ ਚਾਹੀਦਾ",
    # Odia
    r"ବନ୍ଦ କରନ୍ତୁ",
    r"ଆଉ ଦରକାର ନାହିଁ",
]

# Stop-like words. A hit sends the message to the classifier; a miss means
# no model call at all, which keeps this cheap on every turn.
_PREFILTER = [
    r"\bstop",
    r"\bdelete",
    r"\berase",
    r"\bremove",
    r"\bdon.?t (call|message|want)",
    r"\bleave me",
    r"\bband\b",
    r"\bnahi?n? chahiye",
    r"\bmat\b",
    r"\bmita",
    r"\bnirut",
    r"\bnirth",
    r"\bvenda",
    r"\bvaddu",
    r"\baapandi",
    r"\bnillis",
    r"\bbeda\b",
    r"\bnako\b",
    r"\bbondho",
    r"\bchai na",
    r"बंद",
    r"नहीं चाहिए",
    r"नको",
    r"मिटा",
    r"بند",
    r"نہیں چاہیے",
    r"நிறுத்த",
    r"வேண்டாம்",
    r"ఆప",
    r"వద్దు",
    r"ನಿಲ್ಲಿಸ",
    r"ಬೇಡ",
    r"നിർത്ത",
    r"വേണ്ട",
    r"বন্ধ",
    r"চাই না",
    r"নালাগে",
    r"બંધ",
    r"નથી જોઈતું",
    r"ਬੰਦ",
    r"ਨਹੀਂ ਚਾਹੀਦਾ",
    r"ବନ୍ଦ",
    r"ଦରକାର ନାହିଁ",
]

_WITHDRAW_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _WITHDRAW_PATTERNS]
_PREFILTER_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _PREFILTER]

_QUESTION = "(no question: this is something she said in a conversation)"


def said_stop(transcript: str) -> bool:
    """A fixed stop phrase, in a short message or opening a longer one."""
    if not transcript:
        return False
    short = len(transcript.split()) <= _SHORT_WORDS
    for pattern in _WITHDRAW_RE:
        match = pattern.search(transcript)
        if match and (short or match.start() <= _OPENING_CHARS):
            return True
    return False


def might_be_stop(transcript: str) -> bool:
    """Whether the classifier should look at all."""
    return bool(transcript) and any(p.search(transcript) for p in _PREFILTER_RE)


async def is_withdrawal(transcript: str) -> bool:
    """Is she asking Katha to stop for good? A crisis is never read as one:
    the crisis protocol answers it first."""
    if not transcript or not transcript.strip():
        return False
    if conversation_policy.find_crisis_keyword(transcript):
        return False
    if said_stop(transcript):
        return True
    if not might_be_stop(transcript):
        return False
    result = await parent_answers.classify(
        parent_answers.WITHDRAW, _QUESTION, transcript
    )
    return result.label == "withdraw"


async def _withdraw_cards(user_id: str, db: AsyncSession) -> int:
    cards = (
        (
            await db.execute(
                select(ConversationCard).where(ConversationCard.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    return sum(1 for card in cards if card_state.withdraw(card))


async def record(
    profile: UserProfileModel,
    transcript: str,
    db: AsyncSession,
    *,
    inbound_message_sid: Optional[str],
    now: Optional[datetime] = None,
) -> datetime:
    """
    Everything a withdrawal does to the data, in one place. Returns when
    she will be erased. Commits.
    """
    from core import orchestrator

    now = now or datetime.now(timezone.utc)
    user_id = profile.user_id

    # Her words are the evidence, so they are kept as a turn: in the
    # session she was in, or the consent container if none was open. The
    # inbound SID makes a Twilio retry a duplicate, not a second withdrawal.
    active = await session_manager.get_active_session_by_number(
        profile.whatsapp_number, db
    )
    if active is not None:
        session_id, turn_number = active.session_id, active.exchange_count + 1
    else:
        container = await session_manager.get_or_create_consent_session(
            user_id, profile.whatsapp_number, db
        )
        session_id, turn_number = str(container.id), 0
    turn = await orchestrator._persist_turn(
        session_id=session_id,
        user_id=user_id,
        turn_number=turn_number,
        inbound_message_sid=inbound_message_sid,
        transcript=transcript,
        detected_language=profile.preferred_language,
        response_text="[parent withdrew]",
        extraction_json={},
        input_tokens=0,
        output_tokens=0,
        db=db,
    )

    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="",
            consent_version=parent_consent.WITHDRAWN_VERSION,
            principal=parent_consent.PRINCIPAL_PARENT,
            evidence_ref=turn.id,
            whatsapp_number=profile.whatsapp_number,
        )
    )
    withdrawn_cards = await _withdraw_cards(user_id, db)
    erase_at = now + HOLD
    fresh = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    fresh.erase_after = erase_at
    fresh.last_decline_reply_at = now
    await db.commit()

    if active is not None:
        # Closed, and nothing made from it: close_session produces no
        # outputs, and _produce_session_outputs refuses a parent who has
        # not agreed, in case an earlier turn's extraction gets there late.
        await session_manager.close_session(active.session_id, "withdrawn", db)

    logger.warning(
        "Parent %s withdrew consent (turn %s); %d card(s) withdrawn; erase after %s",
        user_id,
        turn.id,
        withdrawn_cards,
        erase_at.isoformat(),
    )
    return erase_at


async def notify(profile: UserProfileModel, erase_at: datetime, db: AsyncSession):
    """Tell the operator and the family. Never raises."""
    when = erase_at.strftime("%d %B %Y")
    await send_operator_alert(
        # No name in the subject: subjects reach logs (P7).
        "A parent withdrew consent",
        f"{profile.name} ({profile.user_id}) asked Katha to stop. Katha has "
        f"stopped. Everything will be erased on {when} unless you clear the "
        "hold first:\n"
        f"  python -m scripts.erase_user --user-id {profile.user_id} "
        "--cancel-hold --by <you>\n"
        "Clear it only if she tells you herself that she did not mean it.",
    )
    try:
        email = (
            await db.execute(
                select(FamilyAccount.email).where(
                    FamilyAccount.user_id == profile.user_id
                )
            )
        ).scalar_one_or_none()
        if not email:
            return
        body = (
            f"{profile.name} asked Katha to stop, and Katha has stopped. It "
            f"will not record anything more or message {profile.name} again.\n\n"
            f"Everything kept from their conversations will be deleted on "
            f"{when}. If {profile.name} did not mean to stop, reply to this "
            "email before then and we will talk to them."
        )
        await send_email_ses(
            email,
            f"{profile.name} asked Katha to stop",
            body,
            "<p>" + body.replace("\n\n", "</p><p>") + "</p>",
        )
    except Exception:
        logger.exception("Could not email the family of %s", profile.user_id)


async def erase_due(db: AsyncSession, now: Optional[datetime] = None) -> int:
    """
    Erase every parent whose withdrawal hold has passed (D4). Run by the
    scheduler. Returns how many were erased. An operator clears a hold with
    scripts/erase_user.py --cancel-hold; a cleared hold is NULL and never
    due.
    """
    from core import erasure

    now = now or datetime.now(timezone.utc)
    due = (
        await db.execute(
            select(UserProfileModel.user_id, UserProfileModel.name)
            .where(UserProfileModel.erase_after.is_not(None))
            .where(UserProfileModel.erase_after <= now)
        )
    ).all()
    for user_id, name in due:
        report = await erasure.erase_user(user_id, db, actor=erasure.ACTOR_HOLD)
        flag = "" if report.complete else " — WITH FAILURES"
        await send_operator_alert(
            f"Erased a family after withdrawal{flag}",
            f"The 7-day hold for {name} ({user_id}) passed and their data was "
            f"erased.\nRemoved: {report.counts}\n"
            + (f"Could not remove: {report.failures}\n" if report.failures else ""),
        )
    return len(due)
