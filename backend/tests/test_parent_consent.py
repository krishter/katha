from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from core import parent_consent
from core.parent_consent import (
    Answer,
    ConsentStatus,
    build_reask_text,
    build_welcome_text,
    get_status,
    interpret_answer,
    record_answer,
    regex_answer,
)
from speech import catalogue

_USER_ID = "user-parent-1"


# ── interpretation ───────────────────────────────────────────────────────────
#
# Conservative on purpose. The remedy for an over-cautious UNCLEAR is one
# more question; the remedy for a wrongly-recorded YES is a DPDP breach.
#
# A refusal is read from the word list, before any model. A yes is read
# only by the classifier (core/parent_answers.py), stubbed here at the LLM
# adapter, the network edge. The classifier's own judgement on real
# sentences is tested against recorded replies in
# tests/integration/test_parent_consent_flow.py and by the consent eval.

_QUESTION = "May I record our conversations and keep them for your family?"


def _classifier(label: str, span: str):
    reply = SimpleNamespace(
        content=f'{{"label": "{label}", "span": "{span}"}}',
        input_tokens=1,
        output_tokens=1,
        stop_reason="end_turn",
    )
    return patch("adapters.llm.chat", new=AsyncMock(return_value=reply))


async def test_a_quoted_classifier_yes_is_yes():
    with _classifier("yes", "haan ji, record kar lo"):
        assert await interpret_answer("haan ji, record kar lo", _QUESTION) is Answer.YES


async def test_a_yes_that_cannot_quote_her_is_not_yes():
    with _classifier("yes", "yes I agree"):
        assert await interpret_answer("Kaun ho aap?", _QUESTION) is Answer.UNCLEAR


async def test_a_question_back_is_unclear_when_the_classifier_says_so():
    with _classifier("unclear", ""):
        assert await interpret_answer("Kaun ho aap?", _QUESTION) is Answer.UNCLEAR


async def test_the_classifier_being_down_is_never_consent():
    failing = patch("adapters.llm.chat", new=AsyncMock(side_effect=RuntimeError))
    with failing:
        for said in ["Yes", "haan ji", "theek hai", "Yes, please record"]:
            assert await interpret_answer(said, _QUESTION) is Answer.UNCLEAR, said


async def test_plain_refusals_are_declines_without_asking_a_model():
    chat = AsyncMock(side_effect=AssertionError("a refusal must not need a model"))
    with patch("adapters.llm.chat", new=chat):
        for said in ["No", "no thank you", "nahi", "illai", "not interested", "नहीं"]:
            assert await interpret_answer(said, _QUESTION) is Answer.NO, said


async def test_negation_beats_affirmation_when_both_appear():
    """ "ok but no" is a refusal. Reading it as consent is the expensive
    direction to be wrong in."""
    with _classifier("yes", "ok"):
        assert await interpret_answer("ok but no", _QUESTION) is Answer.NO
        assert await interpret_answer("haan nahi, not now", _QUESTION) is Answer.NO


async def test_silence_is_not_consent_and_asks_no_model():
    chat = AsyncMock(side_effect=AssertionError("nothing to classify"))
    with patch("adapters.llm.chat", new=chat):
        for said in ["", "   "]:
            assert await interpret_answer(said, _QUESTION) is Answer.UNCLEAR


# The word list survives only as a test oracle (M-01).


def test_the_oracle_still_knows_a_plain_yes():
    for said in ["Yes", "yes please", "haan ji", "sari", "theek hai", "हाँ"]:
        assert regex_answer(said) is Answer.YES, said


def test_the_oracle_no_longer_hears_yes_in_a_question():
    """M-01: "Kaun ho aap?" matched the bare "ho" and was recorded as
    consent. The words that did that are gone from the list."""
    for said in [
        "Kaun ho aap?",
        "okay",
        "I am fine, who is this?",
        "Where do we start?",
    ]:
        assert regex_answer(said) is Answer.UNCLEAR, said


def test_acknowledgement_is_not_agreement():
    """ "acha" / "अच्छा" is the Hindi "I see" at least as often as it is
    "yes". A filler word is not informed consent."""
    for said in ["acha", "achha", "अच्छा"]:
        assert regex_answer(said) is Answer.UNCLEAR, said


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


def test_welcome_is_in_her_language_with_the_disclosure_before_the_ask():
    """M-14: the welcome was English for every parent. In Tamil, the
    disclosure ("I am not a person") still comes before the question."""
    text = build_welcome_text("Lakshmi", child_name="Priya", language="ta-IN")
    assert "Lakshmi" in text and "Priya" in text
    assert "not a person" not in text
    disclosure = text.index("நான் ஒரு மனிதர் இல்லை")
    ask = text.index("சம்மதமா")
    assert disclosure < ask


def test_outcome_texts_follow_her_language_and_persona():
    assert parent_consent.granted_text("ta-IN") == catalogue.text(
        "consent_granted", "ta-IN"
    )
    assert parent_consent.declined_text("hi-IN", "male") != (
        parent_consent.declined_text("hi-IN", "female")
    )
    assert "Lakshmi" in build_reask_text("Lakshmi", language="te-IN")


def test_welcome_works_without_a_child_name():
    assert "your family set this up" in build_welcome_text("Lakshmi").lower()


# ── recording ────────────────────────────────────────────────────────────────


_NUMBER = "+919000000123"


def _db():
    db = AsyncMock()
    db.add = MagicMock()
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = SimpleNamespace(
        whatsapp_number=_NUMBER, parent_consent_state="confirming"
    )
    db.execute = AsyncMock(return_value=profile_result)
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
    # Bound to the number she gave it from (M-03).
    assert record.whatsapp_number == _NUMBER


async def test_a_decline_is_recorded_not_merely_acted_on():
    """Proving we were told to stop matters as much as proving we were
    allowed to start."""
    db = _db()
    await record_answer(_USER_ID, Answer.NO, db)

    record = db.add.call_args[0][0]
    assert record.principal == "parent"
    assert record.consent_version.endswith("-declined")


# ── status ───────────────────────────────────────────────────────────────────


def _status_db(records, times_asked=0, state="asking", number=_NUMBER):
    db = AsyncMock()
    rec_result = MagicMock()
    rec_result.scalars.return_value.all.return_value = records
    profile_result = MagicMock()
    profile_result.scalar_one_or_none.return_value = SimpleNamespace(
        parent_consent_asks=times_asked,
        parent_consent_state=state,
        whatsapp_number=number,
    )
    db.execute = AsyncMock(side_effect=[rec_result, profile_result])
    return db


async def test_status_is_not_asked_before_she_has_ever_messaged():
    state = await get_status(_USER_ID, _status_db([], times_asked=0))
    assert state.status is ConsentStatus.NOT_ASKED


async def test_status_is_awaiting_after_one_unclear_exchange():
    state = await get_status(_USER_ID, _status_db([], times_asked=1))
    assert state.status is ConsentStatus.AWAITING


async def test_status_still_awaits_her_answer_to_the_re_ask():
    """The re-ask is sent with the counter at 2. Her answer to it must
    still be read; halting at 2 meant it never was."""
    state = await get_status(_USER_ID, _status_db([], times_asked=2))
    assert state.status is ConsentStatus.AWAITING


async def test_status_halts_only_when_halted():
    state = await get_status(_USER_ID, _status_db([], times_asked=2, state="halted"))
    assert state.status is ConsentStatus.HALTED


async def test_status_is_confirming_between_the_two_yeses():
    state = await get_status(
        _USER_ID, _status_db([], times_asked=1, state="confirming")
    )
    assert state.status is ConsentStatus.CONFIRMING


async def test_a_grant_from_another_number_is_not_a_grant():
    """M-03: consent is the person's, and Katha knows her by her number."""
    granted = SimpleNamespace(
        consent_version="parent-1.0",
        consented_at="2026-08-30",
        whatsapp_number="+919999999999",
    )
    state = await get_status(_USER_ID, _status_db([granted], times_asked=1))
    assert state.status is not ConsentStatus.GRANTED


async def test_a_reset_retires_earlier_answers():
    declined = SimpleNamespace(
        consent_version="parent-1.0-declined",
        consented_at="2026-08-30",
        whatsapp_number=_NUMBER,
    )
    reset = SimpleNamespace(
        consent_version="parent-1.0-reset",
        consented_at="2026-09-01",
        whatsapp_number="+919999999999",
    )
    state = await get_status(_USER_ID, _status_db([reset, declined], times_asked=0))
    assert state.status is ConsentStatus.NOT_ASKED


async def test_status_reflects_a_granted_record():
    granted = SimpleNamespace(
        consent_version="parent-1.0", consented_at="2026-08-30", whatsapp_number=_NUMBER
    )
    state = await get_status(_USER_ID, _status_db([granted], times_asked=1))
    assert state.status is ConsentStatus.GRANTED


async def test_status_reflects_a_decline():
    declined = SimpleNamespace(
        consent_version="parent-1.0-declined", consented_at="2026-08-30"
    )
    state = await get_status(_USER_ID, _status_db([declined], times_asked=1))
    assert state.status is ConsentStatus.DECLINED


# ── sharing answers are not recording consent (CONVERSATION_CARDS.md §5.1) ──


async def test_a_never_share_answer_does_not_withdraw_recording_consent():
    """She said yes to being recorded, then "don't send anything to my
    son". That is a sharing answer; she is still recording."""
    granted = SimpleNamespace(
        consent_version="parent-1.0", consented_at="2026-08-30", whatsapp_number=_NUMBER
    )
    never = SimpleNamespace(
        consent_version="share-1.0-never", consented_at="2026-09-02"
    )
    state = await get_status(_USER_ID, _status_db([never, granted], times_asked=1))
    assert state.status is ConsentStatus.GRANTED


async def test_a_yes_share_answer_never_reads_as_recording_consent():
    """A newer "yes, share them" must not mask an earlier decline, nor
    stand in for consent never given."""
    declined = SimpleNamespace(
        consent_version="parent-1.0-declined", consented_at="2026-08-30"
    )
    share = SimpleNamespace(consent_version="share-1.0", consented_at="2026-09-02")
    state = await get_status(_USER_ID, _status_db([share, declined], times_asked=1))
    assert state.status is ConsentStatus.DECLINED

    state = await get_status(_USER_ID, _status_db([share], times_asked=1))
    assert state.status is ConsentStatus.AWAITING
