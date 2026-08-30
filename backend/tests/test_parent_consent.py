from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from core.parent_consent import (
    Answer,
    ConsentStatus,
    build_welcome_text,
    get_status,
    interpret_answer,
    record_answer,
)

_USER_ID = "user-parent-1"


# ── interpretation ───────────────────────────────────────────────────────────
#
# Conservative on purpose. The remedy for an over-cautious UNCLEAR is one
# more question; the remedy for a wrongly-recorded YES is a DPDP breach.


def test_plain_affirmatives_are_consent():
    for said in ["Yes", "yes please", "haan ji", "sari", "theek hai", "okay", "हाँ"]:
        assert interpret_answer(said) is Answer.YES, said


def test_plain_refusals_are_declines():
    for said in ["No", "no thank you", "nahi", "illai", "not interested", "नहीं"]:
        assert interpret_answer(said) is Answer.NO, said


def test_negation_beats_affirmation_when_both_appear():
    """ "ok but no" is a refusal. Reading it as consent is the expensive
    direction to be wrong in."""
    assert interpret_answer("ok but no") is Answer.NO
    assert interpret_answer("haan nahi, not now") is Answer.NO


def test_silence_and_confusion_are_not_consent():
    for said in ["", "   ", "hmm", "who is this?", "what", "kaun hai"]:
        assert interpret_answer(said) is Answer.UNCLEAR, said


def test_acknowledgement_is_not_agreement():
    """ "acha" / "अच्छा" is the Hindi "I see" at least as often as it is
    "yes". A filler word is not informed consent, so it must fall through
    to a clarifying question rather than being recorded as agreement."""
    for said in ["acha", "achha", "अच्छा"]:
        assert interpret_answer(said) is Answer.UNCLEAR, said


# ── the welcome ──────────────────────────────────────────────────────────────


def test_welcome_discloses_that_katha_is_not_human():
    """Consent to being recorded, given by someone who believes they are
    speaking to a person, is not informed consent."""
    text = build_welcome_text("Lakshmi", child_name="Priya")
    lowered = text.lower()

    assert "not a person" in lowered
    assert "ai" in lowered.split() or "an ai" in lowered
    # The disclosure has to land before the ask, not after it.
    assert lowered.index("not a person") < lowered.index("would you be happy")


def test_welcome_covers_the_required_ground():
    text = build_welcome_text("Lakshmi", child_name="Priya").lower()
    assert "priya" in text  # who arranged it
    assert "record" in text  # that it is recorded
    assert "family" in text  # who it is kept for
    assert "stop at any time" in text  # how to leave


def test_welcome_works_without_a_child_name():
    assert "your family set this up" in build_welcome_text("Lakshmi").lower()


# ── recording ────────────────────────────────────────────────────────────────


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    return db


async def test_unclear_writes_no_record():
    """An unclear answer is the absence of an answer, not an answer."""
    db = _db()
    await record_answer(_USER_ID, Answer.UNCLEAR, db)
    db.add.assert_not_called()


async def test_consent_is_recorded_against_the_parent_principal():
    db = _db()
    import uuid

    turn_id = uuid.uuid4()

    await record_answer(_USER_ID, Answer.YES, db, turn_id=turn_id)

    record = db.add.call_args[0][0]
    assert record.principal == "parent"
    assert record.evidence_ref == turn_id
    assert not record.consent_version.endswith("-declined")
    # A voice note leaves no IP or user agent; the turn is the evidence.
    assert record.ip_address is None
    assert record.user_agent is None


async def test_a_decline_is_recorded_not_merely_acted_on():
    """Proving we were told to stop matters as much as proving we were
    allowed to start."""
    db = _db()
    await record_answer(_USER_ID, Answer.NO, db)

    record = db.add.call_args[0][0]
    assert record.principal == "parent"
    assert record.consent_version.endswith("-declined")


# ── status ───────────────────────────────────────────────────────────────────


def _status_db(records, times_asked=0):
    db = AsyncMock()
    rec_result = MagicMock()
    rec_result.scalars.return_value.all.return_value = records
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = SimpleNamespace(
        parent_consent_asks=times_asked
    )
    db.execute = AsyncMock(side_effect=[rec_result, profile_result])
    return db


async def test_status_is_not_asked_before_she_has_ever_messaged():
    state = await get_status(_USER_ID, _status_db([], times_asked=0))
    assert state.status is ConsentStatus.NOT_ASKED


async def test_status_is_awaiting_after_one_unclear_exchange():
    state = await get_status(_USER_ID, _status_db([], times_asked=1))
    assert state.status is ConsentStatus.AWAITING


async def test_status_halts_after_asking_twice():
    """Ambiguity re-asks exactly once, then stops and waits for a human
    rather than pestering an elderly person."""
    state = await get_status(_USER_ID, _status_db([], times_asked=2))
    assert state.status is ConsentStatus.HALTED


async def test_status_reflects_a_granted_record():
    granted = SimpleNamespace(consent_version="parent-1.0", consented_at="2026-08-30")
    state = await get_status(_USER_ID, _status_db([granted], times_asked=1))
    assert state.status is ConsentStatus.GRANTED


async def test_status_reflects_a_decline():
    declined = SimpleNamespace(
        consent_version="parent-1.0-declined", consented_at="2026-08-30"
    )
    state = await get_status(_USER_ID, _status_db([declined], times_asked=1))
    assert state.status is ConsentStatus.DECLINED
