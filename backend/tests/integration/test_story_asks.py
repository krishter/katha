"""
Stories Stage 2 against real Postgres (docs/specs/STORIES.md §2.3, D-b,
TC-40 to TC-42): a Story read back to her through the session's one ask,
what her answer does, corrections as new versions, and privacy after the
fact. Her answers are labelled by a mocked classifier and the composing
model is scripted; the evals measure the real ones.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from api.routes.family import export_data
from cards import asks, privacy, state
from core import orchestrator, session_manager
from models.conversation_card import ConversationCard
from models.family_account import FamilyAccount
from models.session import Session
from models.story import Story, StoryVersion
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import CardsContext, UserProfile
from stories import asks as story_asks
from stories import consolidate, correction, store

pytestmark = pytest.mark.integration

READBACK = "ഇത് അമ്മ പറഞ്ഞ കഥയാണ്. ... ഇത് ശരിയാണോ, അനന്തിനോട് പറയട്ടെ?"


@pytest.fixture(autouse=True)
def stories_on(monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    monkeypatch.setattr("config.settings.STORIES_ENABLED", True)
    monkeypatch.setattr("config.settings.STORIES_OPERATOR_REVIEW", True)
    monkeypatch.setattr("cards.privacy.llm.chat", AsyncMock())
    monkeypatch.setattr("stories.correction.send_operator_alert", AsyncMock())


def labels(*answers):
    """parent_answers.classify replies, in order."""
    replies = [
        SimpleNamespace(
            content=json.dumps({"label": label, "span": span}),
            input_tokens=1,
            output_tokens=1,
        )
        for label, span in answers
    ]
    return patch("core.parent_answers.llm.chat", new=AsyncMock(side_effect=replies))


async def seed(
    db, *, stories_enabled=True, share="share_with_approval", exchange_count=0
):
    user_id = f"sask-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Saraswathi",
            whatsapp_number=f"+9193{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ml-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=True,
            stories_enabled=stories_enabled,
            share_preference=share,
            share_preference_asks=1,
        )
    )
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.com",
            user_id=user_id,
            child_name="Anand",
            parent_title="Amma",
        )
    )
    told = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=3,
        domain="childhood",
        status="completed",
    )
    today = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=4,
        domain="childhood",
        exchange_count=exchange_count,
    )
    db.add_all([told, today])
    await db.flush()
    turns = []
    for i, said in enumerate(
        [
            "We lived by the river. I was ten.",
            "The water came in and Appa carried my grandmother out.",
        ],
        start=1,
    ):
        turn = Turn(
            id=uuid.uuid4(),
            session_id=told.id,
            user_id=user_id,
            turn_number=i,
            transcript=said,
            detected_language="ml-IN",
            response_text="എന്നിട്ട്?",
            extraction_status="done",
        )
        db.add(turn)
        turns.append(turn)
    await db.commit()
    # Ids, not the rows: a commit expires the rows.
    return SimpleNamespace(
        user_id=user_id, session_id=str(today.id), turns=[t.id for t in turns]
    )


def _arc(t1, t2):
    def element(ids):
        return {"present": True, "summary": "x", "turn_ids": [str(i) for i in ids]}

    return {
        "setting": element([t1]),
        "event": element([t2]),
        "turn": element([t2]),
        "resolution": {"present": False, "summary": "", "turn_ids": []},
    }


async def waiting_story(db, f, *, review="passed", offers=0, since=None) -> uuid.UUID:
    t1, t2 = f.turns
    story = Story(
        id=uuid.uuid4(),
        user_id=f.user_id,
        status="story",
        review_status=review,
        approval_status="pending",
        title="The night the river came in",
        domain="childhood",
        arc=_arc(t1, t2),
        sessions_seen=[3],
        last_seen_session=3,
        body_first_person="I was ten. The water came in. Appa carried my "
        "grandmother out.",
        source_language="ml-IN",
        sentence_sources=[
            {"text": "I was ten.", "verdict": "supported", "turn_ids": [str(t1)]},
            {
                "text": "The water came in.",
                "verdict": "supported",
                "turn_ids": [str(t2)],
            },
        ],
        readback_text=READBACK,
        readback_language="ml-IN",
        readback_check={"result": "passed"},
        readback_offers=offers,
        promoted_at=datetime.now(timezone.utc) - timedelta(days=2),
        operator_reviewed_at=since or datetime.now(timezone.utc) - timedelta(days=1),
    )
    story.readback_hash = store.fingerprint(story)
    db.add(story)
    await db.commit()
    return story.id


async def waiting_card(db, user_id, *, since) -> uuid.UUID:
    card = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        card,
        title="The walk to the talkies",
        hook="Did you know?",
        excerpt="We walked.",
        call_prompt="What film was it?",
        readback_text="A card read-back",
    )
    card.status = "awaiting_parent"
    card.operator_reviewed_at = since
    card.created_at = since
    db.add(card)
    await db.commit()
    return card.id


async def get(db, model, id_):
    db.expire_all()
    return (await db.execute(select(model).where(model.id == id_))).scalar_one()


async def a_turn(db, f, transcript="…", extraction_status="done") -> uuid.UUID:
    turn_id = uuid.uuid4()
    db.add(
        Turn(
            id=turn_id,
            session_id=uuid.UUID(f.session_id),
            user_id=f.user_id,
            turn_number=9,
            transcript=transcript,
            detected_language="ml-IN",
            response_text="…",
            extraction_status=extraction_status,
        )
    )
    await db.commit()
    return turn_id


async def plan(db, f, transcript="നമസ്കാരം"):
    return await asks.before_dialogue(
        session_id=f.session_id, user_id=f.user_id, transcript=transcript, db=db
    )


async def offered_and_heard(db, f, story_id):
    """She is offered the Story, says yes, and hears its read-back."""
    p = await plan(db, f)
    assert p.offered == asks.STORY_READBACK_OFFER and p.offered_ref == story_id
    await asks.after_dialogue(
        p,
        {asks.STORY_READBACK_OFFER},
        session_id=f.session_id,
        user_id=f.user_id,
        db=db,
    )
    with labels(("yes", "ശരി, വായിക്കൂ")):
        heard = await plan(db, f, "ശരി, വായിക്കൂ")
    assert heard.readback_text == READBACK and heard.readback_story_id == story_id
    turn_id = await a_turn(db, f, "ശരി, വായിക്കൂ")
    await asks.after_dialogue(
        heard, set(), session_id=f.session_id, user_id=f.user_id, db=db
    )
    await asks.after_turn_saved(
        heard, turn_id, session_id=f.session_id, user_id=f.user_id, db=db
    )
    session = await get(db, Session, uuid.UUID(f.session_id))
    assert session.pending_ask == asks.STORY_READBACK
    assert session.pending_ask_ref == story_id


async def answer(db, f, label, said="…"):
    with labels((label, said)):
        p = await plan(db, f, said)
    turn_id = await a_turn(db, f, said)
    await asks.after_dialogue(
        p, set(), session_id=f.session_id, user_id=f.user_id, db=db
    )
    await asks.after_turn_saved(
        p, turn_id, session_id=f.session_id, user_id=f.user_id, db=db
    )
    return p, turn_id


# ── offering ─────────────────────────────────────────────────────────────────


async def test_a_passed_story_is_offered_at_a_greeting_and_counted(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)

    p = await plan(real_db, f)

    assert p.offered == asks.STORY_READBACK_OFFER and p.offered_ref == story_id
    assert asks.STORY_READBACK_OFFER in p.tag_kinds
    joined = " ".join(p.layer4)
    assert "The night the river came in" in joined and "Anand" in joined
    assert "never read, quote or retell" in joined
    # Not counted until the model actually made the offer.
    assert (await get(real_db, Story, story_id)).readback_offers == 0
    await asks.after_dialogue(
        p,
        {asks.STORY_READBACK_OFFER},
        session_id=f.session_id,
        user_id=f.user_id,
        db=real_db,
    )
    assert (await get(real_db, Story, story_id)).readback_offers == 1


@pytest.mark.parametrize(
    "setup",
    ["not_passed", "never_share", "stories_off", "offered_three_times"],
)
async def test_a_story_is_not_offered_when_it_may_not_be(real_db, setup):
    f = await seed(
        real_db,
        stories_enabled=setup != "stories_off",
        share="never" if setup == "never_share" else "share_with_approval",
    )
    await waiting_story(
        real_db,
        f,
        review="operator_review" if setup == "not_passed" else "passed",
        offers=3 if setup == "offered_three_times" else 0,
    )

    p = await plan(real_db, f)

    assert p.offered is None


async def test_after_three_offers_the_story_waits_and_is_never_sent(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f, offers=3)

    await plan(real_db, f)

    story = await get(real_db, Story, story_id)
    assert story.approval_status == "pending"
    assert (await export_data(current_user={"user_id": f.user_id}, db=real_db))[
        "first_person_stories"
    ] == []


async def test_a_story_whose_words_changed_after_review_is_not_offered(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    story = await get(real_db, Story, story_id)
    story.body_first_person = "Something else entirely."
    await real_db.commit()

    p = await plan(real_db, f)

    assert p.offered is None


# ── TC-42: one ask ───────────────────────────────────────────────────────────


@pytest.mark.parametrize("older", ["story", "card"])
async def test_with_a_story_and_a_card_waiting_one_ask_the_older(real_db, older):
    f = await seed(real_db)
    now = datetime.now(timezone.utc)
    story_since = now - timedelta(days=3 if older == "story" else 1)
    card_since = now - timedelta(days=1 if older == "story" else 3)
    story_id = await waiting_story(real_db, f, since=story_since)
    card_id = await waiting_card(real_db, f.user_id, since=card_since)

    p = await plan(real_db, f)

    if older == "story":
        assert (p.offered, p.offered_ref) == (asks.STORY_READBACK_OFFER, story_id)
        assert asks.READBACK_OFFER not in p.tag_kinds
    else:
        assert (p.offered, p.offered_ref) == (asks.READBACK_OFFER, card_id)
        assert asks.STORY_READBACK_OFFER not in p.tag_kinds
    await asks.after_dialogue(
        p, {p.offered}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )
    # The session's one ask is made: nothing else is offered this session.
    with labels(("other", "")):
        again = await plan(real_db, f, "അങ്ങനെ ഒരു ദിവസം...")
    assert again.offered is None


async def test_mid_story_nothing_is_offered(real_db):
    f = await seed(real_db, exchange_count=3)
    await waiting_story(real_db, f)

    p = await plan(real_db, f, "അന്ന് രാത്രി വെള്ളം കയറി...")

    assert p.offered is None


# ── her answer ───────────────────────────────────────────────────────────────


async def test_she_hears_it_and_approves_on_the_words_she_heard(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)

    p, turn_id = await answer(real_db, f, "approve", "ശരിയാണ്, അവന് അയച്ചോളൂ")

    story = await get(real_db, Story, story_id)
    assert story.approval_status == "approved"
    assert story.approved_hash == story.readback_hash == store.fingerprint(story)
    assert story.approved_version == story.version == 1
    assert story.parent_answer == "approve"
    assert story.parent_answer_turn_id == turn_id
    assert p.outcome == "story_readback_approve"
    exported = await export_data(current_user={"user_id": f.user_id}, db=real_db)
    assert [s["title"] for s in exported["first_person_stories"]] == [story.title]


@pytest.mark.parametrize("label", ["keep_private", "not_now", "unclear"])
async def test_any_other_answer_shows_the_family_nothing(real_db, label):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)

    await answer(real_db, f, label)

    story = await get(real_db, Story, story_id)
    expected = "kept_private" if label == "keep_private" else "pending"
    assert story.approval_status == expected
    assert story.approved_hash is None
    assert (await export_data(current_user={"user_id": f.user_id}, db=real_db))[
        "first_person_stories"
    ] == []


async def test_saying_no_to_hearing_it_keeps_it_waiting(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    p = await plan(real_db, f)
    await asks.after_dialogue(
        p, {p.offered}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )

    with labels(("not_now", "പിന്നെ")):
        later = await plan(real_db, f, "പിന്നെ")

    assert later.readback_text is None and later.outcome == "story_offer_not_now"
    story = await get(real_db, Story, story_id)
    assert story.approval_status == "pending" and story.readback_offers == 1


async def test_an_approval_for_other_words_is_refused(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    # The words change between the read-back and her yes.
    story = await get(real_db, Story, story_id)
    story.readback_text = READBACK + " (changed)"
    await real_db.commit()

    await answer(real_db, f, "approve", "ശരി")

    story = await get(real_db, Story, story_id)
    assert story.approval_status == "pending" and story.approved_hash is None


async def test_the_database_still_refuses_an_approval_without_review(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f, review="operator_review")
    story = await get(real_db, Story, story_id)
    story.approval_status = "approved"
    story.approved_hash = story.readback_hash
    with pytest.raises(IntegrityError):
        await real_db.commit()
    await real_db.rollback()


# ── TC-41: correction ────────────────────────────────────────────────────────


class Recomposer:
    """Answers the correction's compose, fidelity and read-back steps."""

    def __init__(self, f, correction_id: str, *, fidelity="supported"):
        # The setting and event are still carried by her first tellings;
        # the corrected age comes from the correction.
        self.told = [str(t) for t in f.turns]
        self.correction_id = correction_id
        self.fidelity = fidelity
        self.prompts: list[tuple[str, str]] = []

    async def __call__(self, prompt, *, step, max_tokens):
        self.prompts.append((step, prompt))
        if step == "compose":
            reply = {
                "title": "The night the river came in",
                "body_first_person": "I was twelve. The water came in.",
                "quotes": [],
            }
        elif step == "fidelity":
            reply = {
                "sentences": [
                    {
                        "text": "I was twelve.",
                        "verdict": self.fidelity,
                        "turn_ids": [self.correction_id],
                        "reason": "she did not say how old she was",
                    },
                    {
                        "text": "The water came in.",
                        "verdict": "supported",
                        "turn_ids": self.told,
                    },
                ]
            }
            if self.fidelity == "unsupported":
                reply["sentences"] = reply["sentences"][:1]
        elif step == "readback":
            reply = {"readback_text": "എനിക്ക് പന്ത്രണ്ട് വയസ്സായിരുന്നു. ... ശരിയാണോ?"}
        elif step == "readback_check":
            reply = {"sentences": [{"text": "x", "verdict": "supported"}]}
        else:
            raise AssertionError(f"unexpected step {step}")
        return json.dumps(reply, ensure_ascii=False), 10, 10


async def test_a_correction_becomes_a_new_version_back_to_review(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    said = "ഇല്ല, എനിക്ക് പത്തല്ല, പന്ത്രണ്ട് വയസ്സായിരുന്നു"
    p, turn_id = await answer(real_db, f, "correction", said)
    assert p.correction_story_id == story_id

    model = Recomposer(f, str(turn_id))
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    story = await get(real_db, Story, story_id)
    assert story.version == 2 and story.correction_round == 1
    assert story.body_first_person == "I was twelve. The water came in."
    assert story.review_status == "operator_review"
    assert story.approval_status == "pending"
    assert story.readback_offers == 0 and story.approved_hash is None
    assert story.readback_hash == store.fingerprint(story)
    # Composed from her earlier tellings and her correction, told it wins.
    compose = next(p for s, p in model.prompts if s == "compose")
    assert said in compose and "We lived by the river" in compose
    assert f"turn {turn_id}" in compose and "THEIR CORRECTION" in compose
    fidelity = next(p for s, p in model.prompts if s == "fidelity")
    assert "THEIR CORRECTION" in fidelity
    # The version she heard is kept.
    (old,) = (
        (
            await real_db.execute(
                select(StoryVersion).where(StoryVersion.story_id == story_id)
            )
        )
        .scalars()
        .all()
    )
    assert old.version == 1 and old.body_first_person.startswith("I was ten.")
    assert old.readback_text == READBACK
    # Her correction is one of its sources now, for the operator's page.
    assert str(turn_id) in correction.source_turn_ids(story)
    # TC-41: nothing reaches the family while it waits.
    assert (await export_data(current_user={"user_id": f.user_id}, db=real_db))[
        "first_person_stories"
    ] == []


async def test_a_second_correction_goes_to_the_operator_by_hand(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    story = await get(real_db, Story, story_id)
    story.correction_round = 1
    story.version = 2
    story.readback_hash = store.fingerprint(story)
    await real_db.commit()
    await offered_and_heard(real_db, f, story_id)
    _, turn_id = await answer(real_db, f, "correction", "ഇല്ല, അതല്ല")

    model = Recomposer(f, str(turn_id))
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    story = await get(real_db, Story, story_id)
    assert model.prompts == []  # not re-composed
    assert story.review_status == "operator_review"
    assert story.approval_status == "pending" and story.version == 2
    assert any("by hand" in n for n in story.notes)
    correction.send_operator_alert.assert_awaited()


async def test_a_correction_that_fails_its_check_goes_to_the_operator(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    _, turn_id = await answer(real_db, f, "correction", "ഇല്ല")

    model = Recomposer(f, str(turn_id), fidelity="unsupported")
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    story = await get(real_db, Story, story_id)
    assert story.version == 1
    assert story.body_first_person.startswith("I was ten.")
    assert story.review_status == "operator_review"
    assert story.approval_status == "pending"


async def test_a_private_correction_turn_is_never_a_source(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    _, turn_id = await answer(real_db, f, "correction", "ഇല്ല")
    turn = await get(real_db, Turn, turn_id)
    turn.is_private = True
    await real_db.commit()

    model = Recomposer(f, str(turn_id))
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    assert model.prompts == []
    story = await get(real_db, Story, story_id)
    assert story.review_status == "operator_review" and story.version == 1


# ── privacy after the fact ───────────────────────────────────────────────────


@pytest.mark.parametrize("stage", ["pending", "approved"])
async def test_a_source_turn_made_private_later_hides_the_story(real_db, stage):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    if stage == "approved":
        story = await get(real_db, Story, story_id)
        story_asks.approve(story, heard_hash=story.readback_hash)
        await real_db.commit()

    await privacy.apply_private_request(
        {"share_restriction": "this_turn"},
        turn_id=f.turns[1],
        user_id=f.user_id,
        db=real_db,
        cards_on=False,
    )

    story = await get(real_db, Story, story_id)
    assert story.approval_status == "kept_private"
    assert (await plan(real_db, f)).offered is None
    assert (await export_data(current_user={"user_id": f.user_id}, db=real_db))[
        "first_person_stories"
    ] == []


# ── through a whole turn ─────────────────────────────────────────────────────


def _router(dialogue_reply: str, label=None):
    calls = []

    async def chat(messages, system=None, max_tokens=500, **kwargs):
        calls.append(kwargs["purpose"])
        if kwargs["purpose"] == "parent_answer_classify":
            content = json.dumps({"label": label[0], "span": label[1]})
        else:
            content = dialogue_reply
        return SimpleNamespace(
            content=content, input_tokens=5, output_tokens=5, stop_reason="end_turn"
        )

    return chat, calls


@contextlib.contextmanager
def a_voice_note(transcript, chat, tts):
    with (
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    transcript=transcript, language_code="ml-IN", language_probability=1
                )
            ),
        ),
        patch("adapters.llm.chat", new=chat),
        patch("core.orchestrator.tts.synthesize", new=tts),
        patch(
            "core.orchestrator.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda b: b),
        ),
    ):
        yield


async def test_offer_read_back_and_correction_through_process_voice_turn(real_db):
    f = await seed(real_db)
    state_ = await session_manager.start_session(f.user_id, real_db)
    story_id = await waiting_story(real_db, f)
    profile = UserProfile(
        name="Saraswathi",
        preferred_language="ml-IN",
        onboarding_context="",
        cards=CardsContext(child_name="Anand", parent_title="Amma"),
    )
    tts = AsyncMock(return_value=b"WAV")

    chat, calls = _router(
        "<response>നമസ്കാരം അമ്മേ! ... വായിച്ചു തരട്ടെ?</response>\n"
        "<asked>story_readback_offer</asked>"
    )
    with a_voice_note("നമസ്കാരം", chat, tts):
        first = await orchestrator.process_voice_turn(
            b"a", state_.session_id, profile, real_db, inbound_message_sid="SM1"
        )
    assert "<asked>" not in first.response_text
    assert calls == ["dialogue"]
    assert (await get(real_db, Story, story_id)).readback_offers == 1

    chat, calls = _router("<response>unused</response>", label=("yes", "വായിക്കൂ"))
    with a_voice_note("ശരി, വായിക്കൂ", chat, tts):
        second = await orchestrator.process_voice_turn(
            b"a", state_.session_id, profile, real_db, inbound_message_sid="SM2"
        )
    # The stored read-back, in her pinned voice, with no dialogue call.
    assert second.response_text == READBACK
    assert calls == ["parent_answer_classify"]
    assert tts.await_args.kwargs["purpose"] == "story_readback_tts"

    tasks = BackgroundTasks()
    chat, calls = _router(
        "<response>നന്ദി, ഞാൻ ശരിയാക്കാം.</response>",
        label=("correction", "പത്തല്ല, പന്ത്രണ്ട്"),
    )
    with a_voice_note("ഇല്ല, എനിക്ക് പത്തല്ല, പന്ത്രണ്ട്", chat, tts):
        third = await orchestrator.process_voice_turn(
            b"a",
            state_.session_id,
            profile,
            real_db,
            inbound_message_sid="SM3",
            background_tasks=tasks,
        )
    story = await get(real_db, Story, story_id)
    assert story.parent_answer == "correction"
    assert story.parent_answer_turn_id == third.turn_id
    # The rewrite waits for the correction turn's extraction, which settles
    # whether that turn is private.
    names = [t.func.__name__ for t in tasks.tasks]
    assert names.index("run_extraction_for_turn") < names.index(
        "rewrite_for_correction"
    )


class CutThenFixed:
    """First draft: the check cuts the sentence that moved an action to the
    wrong person. Second draft, told why: nothing cut."""

    WRONG = "My uncle woke us and carried my grandmother to the temple steps."
    RIGHT = "My uncle carried my grandmother to the temple steps."

    def __init__(self, f, correction_id: str):
        self.told = [str(t) for t in f.turns]
        self.correction_id = correction_id
        self.composes: list[str] = []

    async def __call__(self, prompt, *, step, max_tokens):
        if step == "compose":
            self.composes.append(prompt)
            moment = self.WRONG if len(self.composes) == 1 else self.RIGHT
            reply = {
                "title": "The night the river came in",
                "body_first_person": f"I was twelve. The water came in. {moment}",
                "quotes": [],
            }
        elif step == "fidelity":
            first = len(self.composes) == 1
            moment = self.WRONG if first else self.RIGHT
            reply = {
                "sentences": [
                    {
                        "text": "I was twelve.",
                        "verdict": "supported",
                        "turn_ids": [self.correction_id],
                    },
                    {
                        "text": "The water came in.",
                        "verdict": "supported",
                        "turn_ids": self.told,
                    },
                    {
                        "text": moment,
                        "verdict": "unsupported" if first else "supported",
                        "turn_ids": [self.correction_id],
                        "reason": "she never said the uncle woke them",
                    },
                ]
            }
            if first and "THEIR CORRECTION" in prompt and moment not in prompt:
                # The re-check of the cut draft: what remains is supported.
                reply["sentences"] = reply["sentences"][:2]
        elif step == "readback":
            reply = {"readback_text": "എനിക്ക് പന്ത്രണ്ട് വയസ്സായിരുന്നു. ... ശരിയാണോ?"}
        elif step == "readback_check":
            reply = {"sentences": [{"text": "x", "verdict": "supported"}]}
        else:
            raise AssertionError(f"unexpected step {step}")
        return json.dumps(reply, ensure_ascii=False), 10, 10


async def test_a_cut_correction_is_composed_once_more_told_what_was_cut(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    _, turn_id = await answer(real_db, f, "correction", "ഇല്ല, അമ്മാവനാണ്")

    model = CutThenFixed(f, str(turn_id))
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    assert len(model.composes) == 2
    assert "CUT THESE SENTENCES" not in model.composes[0]
    second = model.composes[1]
    assert "CUT THESE SENTENCES" in second
    assert CutThenFixed.WRONG in second and "never said the uncle woke" in second
    story = await get(real_db, Story, story_id)
    assert story.version == 2
    assert story.body_first_person.endswith(CutThenFixed.RIGHT)
    assert story.removed_sentences == []
    assert story.review_status == "operator_review"


async def test_no_second_try_when_nothing_was_cut(real_db):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    _, turn_id = await answer(real_db, f, "correction", "ഇല്ല")

    model = Recomposer(f, str(turn_id))
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    assert [s for s, _ in model.prompts].count("compose") == 1


# ── §6c: with no reviewer, she decides ───────────────────────────────────────


@pytest.fixture
def no_review(monkeypatch):
    monkeypatch.setattr("config.settings.STORIES_OPERATOR_REVIEW", False)
    monkeypatch.setattr("config.settings.CARDS_OPERATOR_REVIEW", False)


async def _stuck_story(db, f):
    """She corrected it, and the rewrite did not pass its check."""
    story_id = await waiting_story(db, f)
    await offered_and_heard(db, f, story_id)
    _, turn_id = await answer(db, f, "correction", "ഇല്ല, അമ്മാവനാണ്")
    model = Recomposer(f, str(turn_id), fidelity="unsupported")
    with patch.object(consolidate, "_call_llm", model):
        await correction.rewrite_for_correction(story_id, turn_id, db)
    return story_id, turn_id


async def _new_session(db, f, **values):
    s = Session(
        id=uuid.uuid4(),
        user_id=f.user_id,
        session_number=5,
        domain="childhood",
        **values,
    )
    db.add(s)
    await db.commit()
    f.session_id = str(s.id)


async def test_a_correction_that_passes_goes_straight_back_to_her(real_db, no_review):
    f = await seed(real_db)
    story_id = await waiting_story(real_db, f)
    await offered_and_heard(real_db, f, story_id)
    _, turn_id = await answer(real_db, f, "correction", "പന്ത്രണ്ട്")
    with patch.object(consolidate, "_call_llm", Recomposer(f, str(turn_id))):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    story = await get(real_db, Story, story_id)
    assert story.version == 2 and story.review_status == "passed"
    assert story_asks.readable(story)
    await _new_session(real_db, f)
    assert (await plan(real_db, f)).offered == asks.STORY_READBACK_OFFER


async def test_a_correction_katha_cannot_write_waits_for_her(real_db, no_review):
    f = await seed(real_db)
    story_id, turn_id = await _stuck_story(real_db, f)

    story = await get(real_db, Story, story_id)
    assert story.needs_retelling and story.failed_rewrites == 1
    assert story.version == 1 and story.approval_status == "pending"
    assert story.review_status == "passed"
    assert str(turn_id) in story.added_source_turn_ids
    assert not story_asks.readable(story)
    correction.send_operator_alert.assert_not_awaited()

    await _new_session(real_db, f)
    p = await plan(real_db, f)

    assert (p.offered, p.offered_ref) == (asks.RETELL_OFFER, story_id)
    said = " ".join(p.layer4)
    assert "own shortcoming" in said and "set this story aside" in said
    # Katha may name the part, but never says what the cut sentence said.
    assert "never repeat what the story said" in said
    assert "she did not say how old she was" in said
    assert "I was twelve." not in said
    assert "perfectly all right" not in said  # only from the second failure


async def test_she_tells_it_again_and_the_story_is_rewritten_from_it(
    real_db, no_review
):
    f = await seed(real_db)
    story_id, first = await _stuck_story(real_db, f)
    await _new_session(real_db, f)
    p = await plan(real_db, f)
    await asks.after_dialogue(
        p, {asks.RETELL_OFFER}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )

    with labels(("retell", "ഞാൻ പറയാം")):
        p2 = await plan(real_db, f, "ഞാൻ പറയാം")
    assert p2.offered == asks.RETELLING and p2.outcome == "retell_retell"
    await asks.after_dialogue(
        p2, {asks.RETELLING}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )
    session = await get(real_db, Session, uuid.UUID(f.session_id))
    assert session.pending_ask == asks.RETELLING

    retold = "എനിക്ക് പന്ത്രണ്ട് വയസ്സായിരുന്നു. അമ്മാവനാണ് മുത്തശ്ശിയെ എടുത്തത്."
    p3, turn_id = await answer(real_db, f, "unused", retold)
    assert p3.correction_story_id == story_id and p3.outcome == "retelling"

    with patch.object(consolidate, "_call_llm", Recomposer(f, str(turn_id))):
        await correction.rewrite_for_correction(story_id, turn_id, real_db)

    story = await get(real_db, Story, story_id)
    assert story.version == 2 and not story.needs_retelling
    assert {str(first), str(turn_id)} <= set(story.added_source_turn_ids)
    assert story.parent_answer_turn_id == turn_id
    assert story_asks.readable(story)


async def test_she_sets_it_aside_and_it_is_never_offered_again(real_db, no_review):
    f = await seed(real_db)
    story_id, _ = await _stuck_story(real_db, f)
    await _new_session(real_db, f)
    p = await plan(real_db, f)
    await asks.after_dialogue(
        p, {asks.RETELL_OFFER}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )

    with labels(("set_aside", "വിട്ടേക്കൂ")):
        p2 = await plan(real_db, f, "വിട്ടേക്കൂ")

    assert p2.outcome == "retell_set_aside"
    assert any("set aside" in line for line in p2.layer4)
    story = await get(real_db, Story, story_id)
    assert story.status == "retired" and story.retired_reason == "set_aside"
    await _new_session(real_db, f)
    assert (await plan(real_db, f)).offered is None
    assert (await export_data(current_user={"user_id": f.user_id}, db=real_db))[
        "first_person_stories"
    ] == []


@pytest.mark.parametrize("label", ["not_now", "unclear"])
async def test_not_now_keeps_asking_another_day(real_db, no_review, label):
    f = await seed(real_db)
    story_id, _ = await _stuck_story(real_db, f)
    await _new_session(real_db, f)
    p = await plan(real_db, f)
    await asks.after_dialogue(
        p, {asks.RETELL_OFFER}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )
    with labels((label, "പിന്നെ")):
        await plan(real_db, f, "പിന്നെ")

    await _new_session(real_db, f)
    again = await plan(real_db, f)
    assert (again.offered, again.offered_ref) == (asks.RETELL_OFFER, story_id)


async def test_from_the_second_failure_setting_aside_is_offered_gently(
    real_db, no_review
):
    f = await seed(real_db)
    story_id, _ = await _stuck_story(real_db, f)
    story = await get(real_db, Story, story_id)
    story.failed_rewrites = 2
    await real_db.commit()
    await _new_session(real_db, f)

    p = await plan(real_db, f)

    assert any("perfectly all right" in line for line in p.layer4)


async def test_the_retelling_ask_is_never_mid_story(real_db, no_review):
    f = await seed(real_db)
    await _stuck_story(real_db, f)
    await _new_session(real_db, f, exchange_count=3)

    assert (await plan(real_db, f, "അന്ന്...")).offered is None


async def test_a_card_katha_could_not_rewrite_can_be_set_aside(real_db, no_review):
    f = await seed(real_db)
    card = ConversationCard(id=uuid.uuid4(), user_id=f.user_id, status="draft")
    state.set_text(
        card,
        title="The walk to the talkies",
        hook="Did you know?",
        excerpt="We walked.",
        call_prompt="What film was it?",
        readback_text="A card read-back",
    )
    card.status = "correction"
    card.needs_retelling = True
    card.failed_rewrites = 1
    card.parent_answered_at = datetime.now(timezone.utc)
    db_id = card.id
    real_db.add(card)
    await real_db.commit()

    p = await plan(real_db, f)
    assert (p.offered, p.offered_ref) == (asks.RETELL_OFFER, db_id)
    assert any("set this card aside" in line for line in p.layer4)
    await asks.after_dialogue(
        p, {asks.RETELL_OFFER}, session_id=f.session_id, user_id=f.user_id, db=real_db
    )
    with labels(("set_aside", "വേണ്ട")):
        await plan(real_db, f, "വേണ്ട")

    card = await get(real_db, ConversationCard, db_id)
    assert card.status == "kept_private" and not card.needs_retelling
