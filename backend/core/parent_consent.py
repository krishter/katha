"""
Parent consent — the data principal's own agreement.

F-02: the buyer ticks a box saying Katha may record their parent's voice.
Under the DPDP Act the parent is the data principal, and a competent
adult's consent is not their child's to give. This module is where the
parent is asked, and where their answer is interpreted and recorded.

The direction of first contact matters and is not arbitrary. Katha cannot
message first — Meta rejected the approved template with error 63049,
because a first-contact introduction is Marketing by definition and
Marketing is throttled to recipients who have never replied, which is
every parent on day one. So the parent taps a wa.me link her child
forwards, and her inbound message opens the 24-hour window inside which
everything here happens as free-form messages. That also produces a
better consent posture than the design it replaced: she initiated.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass
from enum import Enum
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.consent_record import ConsentRecord
from models.user_profile import UserProfileModel
from speech import catalogue

logger = logging.getLogger(__name__)

# Bumped when the spoken wording below changes materially. Separate from
# the web form's version — the two principals agree to different text.
PARENT_CONSENT_VERSION = "parent-1.0"

PRINCIPAL_PARENT = "parent"
PRINCIPAL_BUYER = "buyer"

# The parent also gives answers that are not about being recorded, logged
# as consent records under her own principal: whether her stories may be
# shared with her child (core/sharing.py, versions "share-…"). Those must
# never be read as an answer to *this* question: "don't send anything to
# my son" is not "stop recording me", and "yes, share them" is not consent
# to be recorded. They are excluded by prefix rather than recording
# consent being required to match one, so a record written some other way
# (an operator's, an older format) still counts as it always has.
SHARING_VERSION_PREFIX = "share-"


class ConsentStatus(str, Enum):
    """Where a parent is in the consent conversation."""

    NOT_ASKED = "not_asked"  # she has never messaged; nothing sent
    AWAITING = "awaiting"  # welcome sent, no clear answer yet
    GRANTED = "granted"
    DECLINED = "declined"
    HALTED = "halted"  # asked twice, still unclear — needs a human


class Answer(str, Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


# Interpretation is deliberately conservative. An explicit affirmative is
# required; ambiguity, silence and confusion are not consent. Getting this
# wrong in the permissive direction means recording an elderly person's
# life history on the strength of a "hmm".
#
# Deliberately absent: "acha" / "अच्छा". It is an acknowledgement at least
# as often as an agreement — the Hindi "I see" — and a filler word is not
# informed consent. Same reasoning excludes a bare "hmm".
_YES_PATTERNS = [
    r"\byes\b",
    r"\byeah\b",
    r"\byep\b",
    r"\bok\b",
    r"\bokay\b",
    r"\bsure\b",
    r"\bagree\b",
    r"\bagreed\b",
    r"\bi accept\b",
    r"\baccept\b",
    r"\bgo ahead\b",
    r"\bplease do\b",
    r"\bthat.s fine\b",
    r"\bfine\b",
    r"\bhappy to\b",
    r"\bi.m in\b",
    r"\bstart\b",
    r"\bcarry on\b",
    # Hindi / Urdu
    r"\bhaan\b",
    r"\bhan\b",
    r"\bji haan\b",
    r"\btheek hai\b",
    r"\bthik hai\b",
    r"\bbilkul\b",
    r"\bzaroor\b",
    r"हाँ",
    r"हां",
    r"ठीक है",
    r"बिलकुल",
    r"ज़रूर",
    # Tamil
    r"\bsari\b",
    r"\bseri\b",
    r"\baama\b",
    r"\baamaa\b",
    r"சரி",
    r"ஆமா",
    r"ஆம்",
    # Telugu / Kannada / Malayalam / Marathi / Bengali
    r"\bsare\b",
    r"\bavunu\b",
    r"అవును",
    r"సరే",
    r"\bhaudu\b",
    r"ಹೌದು",
    r"ಸರಿ",
    r"\bathe\b",
    r"ശരി",
    r"അതെ",
    r"\bho\b",
    r"होय",
    r"चालेल",
    r"\bhyan\b",
    r"হ্যাঁ",
    r"ঠিক আছে",
]

_NO_PATTERNS = [
    r"\bno\b",
    r"\bnope\b",
    r"\bdon.t want\b",
    r"\bdo not want\b",
    r"\bnot interested\b",
    r"\bstop\b",
    r"\bleave me\b",
    r"\bdelete\b",
    r"\bremove me\b",
    r"\bnot now\b",
    r"\brefuse\b",
    r"\bdisagree\b",
    r"\bi decline\b",
    r"\bdecline\b",
    r"\bnahi\b",
    r"\bnahin\b",
    r"\bnaa\b",
    r"\bmat\b",
    r"\bband karo\b",
    r"नहीं",
    r"नही",
    r"बंद",
    r"\billai\b",
    r"\bvenda\b",
    r"\bvendam\b",
    r"இல்லை",
    r"வேண்டாம்",
    r"\bkaadu\b",
    r"\bvaddu\b",
    r"కాదు",
    r"వద్దు",
    r"\billa\b",
    r"\bbeda\b",
    r"ಇಲ್ಲ",
    r"ಬೇಡ",
    r"\bvenda\b",
    r"ഇല്ല",
    r"വേണ്ട",
    r"\bnako\b",
    r"नाही",
    r"नको",
    r"\bna\b",
    r"না",
    r"চাই না",
]

_YES_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _YES_PATTERNS]
_NO_RE = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in _NO_PATTERNS]


def interpret_answer(transcript: str) -> Answer:
    """
    Read a spoken reply as yes, no, or unclear.

    A negation beats an affirmative when both appear: "ok but no" and "haan
    nahi" are refusals, and the cost of misreading a refusal as consent is
    far higher than the reverse — the remedy for an over-cautious UNCLEAR
    is one more question, while the remedy for a wrongly-recorded YES is a
    DPDP breach.
    """
    if not transcript or not transcript.strip():
        return Answer.UNCLEAR

    said_yes = any(r.search(transcript) for r in _YES_RE)
    said_no = any(r.search(transcript) for r in _NO_RE)

    if said_no:
        return Answer.NO
    if said_yes:
        return Answer.YES
    return Answer.UNCLEAR


def build_welcome_text(
    parent_name: str,
    child_name: Optional[str] = None,
    *,
    language: str = catalogue.SOURCE_LANGUAGE,
    gender: Optional[str] = None,
) -> str:
    """
    The first thing the parent ever hears from Katha, in her own language
    (speech/catalogue.py; it used to be English for everyone, M-14).

    The AI disclosure is not decorative. Consent to being recorded, given
    by someone who believes they are talking to a person, is not informed
    consent — and informed consent is the whole purpose of this workstream.
    It therefore comes before the ask, not after it, in every language.
    """
    if child_name:
        return catalogue.text(
            "consent_welcome",
            language,
            gender=gender,
            name=parent_name,
            child=child_name,
        )
    return catalogue.text(
        "consent_welcome_no_child", language, gender=gender, name=parent_name
    )


def build_reask_text(
    parent_name: str,
    *,
    language: str = catalogue.SOURCE_LANGUAGE,
    gender: Optional[str] = None,
) -> str:
    """One clarification, no more. Then a human looks at it."""
    return catalogue.text("consent_reask", language, gender=gender, name=parent_name)


def declined_text(
    language: str = catalogue.SOURCE_LANGUAGE, gender: Optional[str] = None
) -> str:
    return catalogue.text("consent_declined", language, gender=gender)


def granted_text(
    language: str = catalogue.SOURCE_LANGUAGE, gender: Optional[str] = None
) -> str:
    return catalogue.text("consent_granted", language, gender=gender)


@dataclass
class ParentConsentState:
    status: ConsentStatus
    consented_at: Optional[object] = None
    times_asked: int = 0


async def get_status(user_id: str, db: AsyncSession) -> ParentConsentState:
    """
    The parent's consent state, derived from consent_records plus the
    profile's ask counter.
    """
    result = await db.execute(
        select(ConsentRecord)
        .where(ConsentRecord.user_id == user_id)
        .where(ConsentRecord.principal == PRINCIPAL_PARENT)
        .where(~ConsentRecord.consent_version.startswith(SHARING_VERSION_PREFIX))
        .order_by(ConsentRecord.consented_at.desc())
    )
    records = result.scalars().all()

    profile_result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    times_asked = getattr(profile, "parent_consent_asks", 0) or 0

    for record in records:
        # The query already filters, but this is the one place a wrong row
        # would turn into recording someone who said no.
        if record.consent_version.startswith(SHARING_VERSION_PREFIX):
            continue
        if record.consent_version.endswith("-declined"):
            return ParentConsentState(ConsentStatus.DECLINED, record.consented_at)
        return ParentConsentState(ConsentStatus.GRANTED, record.consented_at)

    if times_asked == 0:
        return ParentConsentState(ConsentStatus.NOT_ASKED, times_asked=0)
    if times_asked >= 2:
        return ParentConsentState(ConsentStatus.HALTED, times_asked=times_asked)
    return ParentConsentState(ConsentStatus.AWAITING, times_asked=times_asked)


async def has_granted(user_id: str, db: AsyncSession) -> bool:
    state = await get_status(user_id, db)
    return state.status is ConsentStatus.GRANTED


async def record_answer(
    user_id: str,
    answer: Answer,
    db: AsyncSession,
    turn_id: Optional[uuid.UUID] = None,
) -> None:
    """
    Persist a yes or a no. UNCLEAR writes nothing — it is the absence of an
    answer, not an answer.

    A decline is recorded, not merely acted on: proving we were told to stop
    matters as much as proving we were allowed to start.
    """
    if answer is Answer.UNCLEAR:
        return

    version = PARENT_CONSENT_VERSION
    if answer is Answer.NO:
        version = f"{PARENT_CONSENT_VERSION}-declined"

    db.add(
        ConsentRecord(
            user_id=user_id,
            # No email exists for the parent; she is reached by phone number,
            # and the profile carries it. The column is non-null, so this
            # records what identified her rather than inventing a hash.
            email_hash="",
            consent_version=version,
            principal=PRINCIPAL_PARENT,
            evidence_ref=turn_id,
            ip_address=None,
            user_agent=None,
        )
    )
    await db.commit()
    logger.info(
        "Recorded parent consent for %s: %s (evidence turn %s)",
        user_id,
        answer.value,
        turn_id,
    )


async def note_asked(user_id: str, db: AsyncSession) -> int:
    """Count that we have asked. Two unclear answers and we stop asking."""
    result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        return 0
    profile.parent_consent_asks = (profile.parent_consent_asks or 0) + 1
    db.add(profile)
    await db.commit()
    return profile.parent_consent_asks
