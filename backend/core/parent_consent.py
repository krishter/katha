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

from core import parent_answers
from models.consent_record import ConsentRecord
from models.user_profile import UserProfileModel
from speech import catalogue

logger = logging.getLogger(__name__)

# Bumped when the spoken wording below changes materially. Separate from
# the web form's version — the two principals agree to different text.
# 1.1 (P10, M-06): the welcome now says that AI companies, some outside
# India, process her words, that a person at Katha may read them, and that
# her family sees summaries.
PARENT_CONSENT_VERSION = "parent-1.1"
# A parent who agreed to the 1.0 wording is asked once more, on her next
# message, before anything else (consent_update). For this change only; a
# later wording change decides its own rule.
PREVIOUS_GRANT_VERSION = "parent-1.0"
# Written by scripts/change_parent_number.py: whatever she said on her old
# number is not an answer from whoever holds the new one.
RESET_VERSION = f"{PARENT_CONSENT_VERSION}-reset"
# She agreed, then asked Katha to stop (core/withdrawal.py, P4). Read like a
# decline: nothing is recorded, and she is not asked again.
WITHDRAWN_VERSION = f"{PARENT_CONSENT_VERSION}-withdrawn"

# user_profiles.parent_consent_state
STATE_ASKING = "asking"
STATE_CONFIRMING = "confirming"
STATE_HALTED = "halted"
# The 1.1 update (P10): asked, and asked once more after an unclear answer.
STATE_UPDATING = "updating"
STATE_UPDATING_REASKED = "updating_reasked"

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
    CONFIRMING = "confirming"  # she said yes once; asked to confirm
    GRANTED = "granted"
    DECLINED = "declined"
    WITHDRAWN = "withdrawn"  # agreed, then asked Katha to stop
    UPDATE = "update"  # agreed to the 1.0 wording; asked about 1.1 (P10)
    HALTED = "halted"  # still unclear after the re-ask — needs a human


class Answer(str, Enum):
    YES = "yes"
    NO = "no"
    UNCLEAR = "unclear"


# Interpretation is deliberately conservative. An explicit affirmative is
# required; ambiguity, silence and confusion are not consent. Getting this
# wrong in the permissive direction means recording an elderly person's
# life history on the strength of a "hmm".
#
# A yes is read by the classifier (core/parent_answers.py), never by these
# patterns. A word list cannot tell "theek hai, record kar lo" from "theek
# hai, par yeh kya hai?", and "Kaun ho aap?" matched the bare "ho" (M-01).
# _YES_PATTERNS is kept as a test oracle only: the clear yeses in the tests
# must match it, so a classifier that drifts permissive shows up against
# it. Removed from it too, for the same reason: ok, okay, fine, start, ho,
# han and sure.
#
# Deliberately absent: "acha" / "अच्छा". It is an acknowledgement at least
# as often as an agreement — the Hindi "I see" — and a filler word is not
# informed consent. Same reasoning excludes a bare "hmm".
_YES_PATTERNS = [
    r"\byes\b",
    r"\byeah\b",
    r"\byep\b",
    r"\bagree\b",
    r"\bagreed\b",
    r"\bi accept\b",
    r"\baccept\b",
    r"\bgo ahead\b",
    r"\bplease do\b",
    r"\bthat.s fine\b",
    r"\bhappy to\b",
    r"\bi.m in\b",
    r"\bcarry on\b",
    # Hindi / Urdu
    r"\bhaan\b",
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


def said_no(transcript: str) -> bool:
    """A refusal the word list can see. Checked before the classifier, so
    refusing never depends on a model being up."""
    return bool(transcript) and any(r.search(transcript) for r in _NO_RE)


def regex_answer(transcript: str) -> Answer:
    """
    The word-list reading. A TEST ORACLE ONLY: nothing decides on it.

    A negation beats an affirmative when both appear: "ok but no" and
    "haan nahi" are refusals.
    """
    if not transcript or not transcript.strip():
        return Answer.UNCLEAR
    if said_no(transcript):
        return Answer.NO
    if any(r.search(transcript) for r in _YES_RE):
        return Answer.YES
    return Answer.UNCLEAR


async def interpret_answer(transcript: str, question: str) -> Answer:
    """
    Read her reply to `question` as yes, no, or unclear.

    1. A refusal in the word list is NO, without asking a model.
    2. Otherwise the classifier reads it (core/parent_answers.py). A yes
       stands only if it quotes her words; any failure is UNCLEAR.

    The cost is asymmetric, and so is the design: the remedy for an
    over-cautious UNCLEAR is one more question, while the remedy for a
    wrongly recorded YES is a DPDP breach.
    """
    if not transcript or not transcript.strip():
        return Answer.UNCLEAR
    if said_no(transcript):
        return Answer.NO
    result = await parent_answers.classify(parent_answers.CONSENT, question, transcript)
    if result.label == "yes":
        return Answer.YES
    if result.label == "no":
        return Answer.NO
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
    processing = catalogue.text("consent_processing", language, gender=gender)
    if child_name:
        return catalogue.text(
            "consent_welcome",
            language,
            gender=gender,
            name=parent_name,
            child=child_name,
            processing=processing,
        )
    return catalogue.text(
        "consent_welcome_no_child",
        language,
        gender=gender,
        name=parent_name,
        processing=processing,
    )


def update_text(
    parent_name: str,
    *,
    language: str = catalogue.SOURCE_LANGUAGE,
    gender: Optional[str] = None,
) -> str:
    """For a parent who agreed to the 1.0 wording (P10): the two sentences
    she was not told, and the question again."""
    return catalogue.text(
        "consent_update",
        language,
        gender=gender,
        name=parent_name,
        processing=catalogue.text("consent_processing", language, gender=gender),
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


def withdraw_text(
    language: str = catalogue.SOURCE_LANGUAGE, gender: Optional[str] = None
) -> str:
    """What she hears once, when she asks Katha to stop (P4)."""
    return catalogue.text("withdraw_confirm", language, gender=gender)


def confirm_text(
    language: str = catalogue.SOURCE_LANGUAGE, gender: Optional[str] = None
) -> str:
    """After her first yes: what she is agreeing to, once more, and the
    question again. Only a second yes is recorded (P3, D2)."""
    return catalogue.text("consent_confirm", language, gender=gender)


@dataclass
class ParentConsentState:
    status: ConsentStatus
    consented_at: Optional[object] = None
    times_asked: int = 0


async def get_status(user_id: str, db: AsyncSession) -> ParentConsentState:
    """
    The parent's consent state, derived from consent_records plus the
    profile's consent state and ask counter.

    A grant counts only while the profile still has the number it was given
    from (M-03): Katha knows her by her number, so a grant given on one
    number says nothing about whoever holds another. A decline is not
    loosened the same way; a reset record (scripts/change_parent_number.py)
    is what clears either.
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
    state = getattr(profile, "parent_consent_state", None) or STATE_ASKING
    number = getattr(profile, "whatsapp_number", None)

    for record in records:
        # The query already filters, but this is the one place a wrong row
        # would turn into recording someone who said no.
        if record.consent_version.startswith(SHARING_VERSION_PREFIX):
            continue
        # By suffix: a reset or withdrawal under an earlier wording still
        # counts.
        if record.consent_version.endswith("-reset"):
            break
        if record.consent_version.endswith("-declined"):
            return ParentConsentState(ConsentStatus.DECLINED, record.consented_at)
        if record.consent_version.endswith("-withdrawn"):
            return ParentConsentState(ConsentStatus.WITHDRAWN, record.consented_at)
        if record.whatsapp_number != number:
            logger.warning(
                "Parent consent for %s was given from another number — not a grant",
                user_id,
            )
            break
        if record.consent_version == PREVIOUS_GRANT_VERSION:
            # She agreed before the notice said who processes her words.
            if state == STATE_HALTED:
                return ParentConsentState(ConsentStatus.HALTED, times_asked=times_asked)
            return ParentConsentState(ConsentStatus.UPDATE, record.consented_at)
        return ParentConsentState(ConsentStatus.GRANTED, record.consented_at)

    if state == STATE_HALTED:
        return ParentConsentState(ConsentStatus.HALTED, times_asked=times_asked)
    if state == STATE_CONFIRMING:
        return ParentConsentState(ConsentStatus.CONFIRMING, times_asked=times_asked)
    if times_asked == 0:
        return ParentConsentState(ConsentStatus.NOT_ASKED, times_asked=0)
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

    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is not None:
        # Answered: the conversation is no longer mid-way.
        profile.parent_consent_state = STATE_ASKING

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
            whatsapp_number=profile.whatsapp_number if profile else None,
        )
    )
    await db.commit()
    logger.info(
        "Recorded parent consent for %s: %s (evidence turn %s)",
        user_id,
        answer.value,
        turn_id,
    )


async def set_state(user_id: str, state: str, db: AsyncSession) -> None:
    """Move the consent conversation to `state` (asking, confirming,
    halted)."""
    result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = result.scalar_one_or_none()
    if profile is None:
        return
    profile.parent_consent_state = state
    await db.commit()


async def note_asked(user_id: str, db: AsyncSession) -> int:
    """Count that we have asked: the welcome is one, the re-ask two. An
    unclear answer to the re-ask halts (webhook._handle_parent_consent)."""
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
