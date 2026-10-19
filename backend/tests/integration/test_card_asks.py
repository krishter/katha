"""
The one card ask a session carries, and what her answer does, against real
Postgres (CONVERSATION_CARDS.md §5.1, §6, §9.2, §10.3). Model calls are
mocked by purpose; TC-12 to TC-16 measure the real ones.
"""

from __future__ import annotations

import contextlib
import json
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from cards import asks, state
from core import orchestrator, session_manager
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.family_account import FamilyAccount
from models.family_question import FamilyQuestion
from models.session import Session
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import CardsContext, UserProfile

pytestmark = pytest.mark.integration

READBACK = "Ithu naan ezhuthinathu… Priya-kku anuppattuma?"


@pytest.fixture(autouse=True)
def cards_on(monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    monkeypatch.setattr("cards.privacy.llm.chat", AsyncMock())


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
    db,
    *,
    session_number=2,
    exchange_count=1,
    goal_met=False,
    share="share_with_approval",
    asks_made=0,
):
    user_id = f"ask-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9192{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ta-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=True,
            share_preference=share,
            share_preference_asks=asks_made,
        )
    )
    db.add(
        FamilyAccount(
            email=f"{user_id}@example.com",
            user_id=user_id,
            child_name="Priya",
            parent_title="Amma",
        )
    )
    session_id = uuid.uuid4()
    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=session_number,
            domain="childhood",
            exchange_count=exchange_count,
            goal_met=goal_met,
        )
    )
    await db.commit()
    return user_id, str(session_id)


async def waiting_card(db, user_id, offers=0) -> uuid.UUID:
    card = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        card,
        title="The walk to the talkies",
        hook="Did you know Amma walked to the talkies?",
        excerpt="We walked.",
        call_prompt="What film was it?",
        readback_text=READBACK,
    )
    card.status = "awaiting_parent"
    card.readback_offers = offers
    card.source_cutoff = datetime(2026, 10, 13, tzinfo=timezone.utc)  # a Tuesday
    db.add(card)
    await db.commit()
    return card.id


async def get(db, model, id_):
    db.expire_all()
    return (await db.execute(select(model).where(model.id == id_))).scalar_one()


async def a_turn(db, session_id, user_id, transcript="…") -> uuid.UUID:
    turn_id = uuid.uuid4()
    db.add(
        Turn(
            id=turn_id,
            session_id=uuid.UUID(session_id),
            user_id=user_id,
            turn_number=9,
            transcript=transcript,
            response_text="…",
        )
    )
    await db.commit()
    return turn_id


async def plan(db, session_id, user_id, transcript="Vanakkam"):
    return await asks.before_dialogue(
        session_id=session_id, user_id=user_id, transcript=transcript, db=db
    )


# ── the <asked> tag ──────────────────────────────────────────────────────────


def test_the_tag_is_read_and_never_spoken():
    kinds, text = asks.split_asked_tag(
        "<response>Shall I read it to you?</response>\n"
        "<asked>readback_offer, reaction</asked>"
    )
    assert kinds == {"readback_offer", "reaction"}
    assert "<asked>" not in text and "readback_offer" not in text
    assert asks.split_asked_tag("<response>Hi</response>") == (
        set(),
        "<response>Hi</response>",
    )


# ── the sharing question (§5.1) ──────────────────────────────────────────────


async def test_session_one_is_for_trust(real_db):
    user_id, session_id = await seed(real_db, session_number=1, share="not_asked")
    p = await plan(real_db, session_id, user_id)
    assert p.offered is None and p.layer4 == []


async def test_session_two_asks_at_a_pause_and_records_her_yes(real_db):
    user_id, session_id = await seed(real_db, share="not_asked")
    p = await plan(real_db, session_id, user_id)
    assert p.offered == asks.SHARE
    assert "Priya" in p.layer4[0] and "read anything to them first" in p.layer4[0]

    await asks.after_dialogue(
        p, {"share"}, session_id=session_id, user_id=user_id, db=real_db
    )
    session = await get(real_db, Session, uuid.UUID(session_id))
    assert (session.pending_ask, session.ask_made) == ("share", True)

    with labels(("yes", "haan")):
        answer = await plan(real_db, session_id, user_id, "Haan, zaroor")
    assert answer.outcome == "share_yes"
    assert answer.offered is None  # one ask a session
    turn_id = await a_turn(real_db, session_id, user_id, "Haan, zaroor")
    with patch("core.sharing.send_operator_alert", new=AsyncMock()):
        await asks.after_turn_saved(
            answer, turn_id, session_id=session_id, user_id=user_id, db=real_db
        )
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert (profile.share_preference, profile.share_preference_asks) == (
        "share_with_approval",
        1,
    )
    record = (
        await real_db.execute(
            select(ConsentRecord).where(ConsentRecord.user_id == user_id)
        )
    ).scalar_one()
    assert (record.consent_version, record.evidence_ref) == ("share-1.0", turn_id)


async def test_unclear_twice_is_never(real_db):
    user_id, session_id = await seed(real_db, share="unsure", asks_made=2)
    session = await get(real_db, Session, uuid.UUID(session_id))
    session.pending_ask = "share"
    await real_db.commit()
    with labels(("unclear", "")):
        answer = await plan(real_db, session_id, user_id, "Hmm, then my father…")
    assert answer.share_decision == "never"


@pytest.mark.parametrize(
    "share, asks_made, session_number, due",
    [
        ("not_asked", 0, 2, True),
        ("unsure", 1, 3, False),
        ("unsure", 1, 4, True),
        ("unsure", 2, 6, False),
        ("never", 1, 9, False),
    ],
)
async def test_when_the_share_question_is_due(
    real_db, share, asks_made, session_number, due
):
    user_id, session_id = await seed(
        real_db, share=share, asks_made=asks_made, session_number=session_number
    )
    p = await plan(real_db, session_id, user_id)
    assert (p.offered == asks.SHARE) is due


# ── the read-back (§6) ───────────────────────────────────────────────────────


async def test_offer_at_the_opening_then_read_then_her_yes(real_db):
    user_id, session_id = await seed(real_db, exchange_count=0)
    card_id = await waiting_card(real_db, user_id)

    p = await plan(real_db, session_id, user_id, "Vanakkam!")
    assert p.offered == asks.READBACK_OFFER and p.offered_ref == card_id
    assert "greeting or small talk" in p.layer4[0]
    assert "Tuesday" in p.layer4[0] and "never read, quote or describe" in p.layer4[0]
    await asks.after_dialogue(
        p, {"readback_offer"}, session_id=session_id, user_id=user_id, db=real_db
    )
    assert (await get(real_db, ConversationCard, card_id)).readback_offers == 1

    with labels(("yes", "sollunga")):
        read = await plan(real_db, session_id, user_id, "Sari, sollunga")
    assert read.readback_text == READBACK
    assert (
        await get(real_db, Session, uuid.UUID(session_id))
    ).pending_ask == "readback"

    with labels(("approve", "anuppunga")):
        answer = await plan(real_db, session_id, user_id, "Aamaa, anuppunga")
    turn_id = await a_turn(real_db, session_id, user_id)
    await asks.after_turn_saved(
        answer, turn_id, session_id=session_id, user_id=user_id, db=real_db
    )
    card = await get(real_db, ConversationCard, card_id)
    # Approved, and given a send time before the child's next call.
    assert card.status == "scheduled" and card.scheduled_for is not None
    assert card.parent_answer == "approve"
    assert card.approved_hash == card.readback_hash
    assert card.parent_answer_turn_id == turn_id
    assert "send it to Priya" in answer.layer4[0]


async def test_an_offer_the_model_did_not_make_is_not_counted(real_db):
    user_id, session_id = await seed(real_db, exchange_count=0)
    card_id = await waiting_card(real_db, user_id)
    p = await plan(real_db, session_id, user_id)
    await asks.after_dialogue(
        p, set(), session_id=session_id, user_id=user_id, db=real_db
    )
    session = await get(real_db, Session, uuid.UUID(session_id))
    assert (session.pending_ask, session.ask_made) == (None, False)
    assert (await get(real_db, ConversationCard, card_id)).readback_offers == 0


async def test_never_offered_mid_story(real_db):
    user_id, session_id = await seed(real_db, exchange_count=3, goal_met=False)
    await waiting_card(real_db, user_id)
    assert (await plan(real_db, session_id, user_id)).offered is None


async def test_offered_at_the_close_with_the_recap(real_db):
    user_id, session_id = await seed(real_db, exchange_count=5, goal_met=True)
    await waiting_card(real_db, user_id)
    p = await plan(real_db, session_id, user_id)
    assert p.offered == asks.READBACK_OFFER
    assert "closing exchange" in p.layer4[0]
    assert any("kept it safe" in line for line in p.layer4)


@pytest.mark.parametrize(
    "label, status",
    [
        ("correction", "correction"),
        ("keep_private", "kept_private"),
        ("not_now", "awaiting_parent"),
        ("unclear", "awaiting_parent"),
    ],
)
async def test_her_other_answers_to_the_read_back(real_db, label, status):
    user_id, session_id = await seed(real_db)
    card_id = await waiting_card(real_db, user_id, offers=1)
    session = await get(real_db, Session, uuid.UUID(session_id))
    session.pending_ask, session.pending_ask_ref = "readback", card_id
    await real_db.commit()
    with labels((label, "")):
        answer = await plan(real_db, session_id, user_id, "…")
    card = await get(real_db, ConversationCard, card_id)
    assert card.status == status
    assert card.parent_answer == label
    if label == "correction":
        assert answer.correction_card_id == card_id


async def test_a_yes_the_classifier_cannot_quote_does_not_approve(real_db):
    user_id, session_id = await seed(real_db)
    card_id = await waiting_card(real_db, user_id, offers=1)
    session = await get(real_db, Session, uuid.UUID(session_id))
    session.pending_ask, session.pending_ask_ref = "readback", card_id
    await real_db.commit()
    with labels(("approve", "yes send it")):
        await plan(real_db, session_id, user_id, "Hmm, okay okay, so my father…")
    card = await get(real_db, ConversationCard, card_id)
    assert card.status == "awaiting_parent"
    assert card.parent_answer == "unclear"


async def test_three_offers_without_an_answer_expire_the_card(real_db):
    user_id, session_id = await seed(real_db)
    card_id = await waiting_card(real_db, user_id, offers=3)
    session = await get(real_db, Session, uuid.UUID(session_id))
    session.pending_ask, session.pending_ask_ref = "readback_offer", card_id
    await real_db.commit()
    with labels(("not_now", "")):
        await plan(real_db, session_id, user_id, "Not today")
    assert (await get(real_db, ConversationCard, card_id)).status == "expired"


# ── child questions and reactions (§10.3) ────────────────────────────────────


async def test_a_childs_question_is_relayed_credited_and_marked(real_db):
    user_id, session_id = await seed(real_db, exchange_count=2)
    q = FamilyQuestion(
        id=uuid.uuid4(),
        user_id=user_id,
        channel="whatsapp",
        raw_text="Which film was it?",
        screening="ok",
        status="pending",
    )
    real_db.add(q)
    await real_db.commit()
    p = await plan(real_db, session_id, user_id)
    assert p.offered == asks.CHILD_QUESTION
    assert "Which film was it?" in p.layer4[0] and "from Priya" in p.layer4[0]
    assert "never present it as your own" in p.layer4[0].lower()
    await asks.after_dialogue(
        p, {"child_question"}, session_id=session_id, user_id=user_id, db=real_db
    )
    asked = await get(real_db, FamilyQuestion, q.id)
    assert asked.status == "asked"
    assert str(asked.asked_in_session_id) == session_id


async def test_a_held_question_is_never_relayed(real_db):
    user_id, session_id = await seed(real_db, exchange_count=2)
    real_db.add(
        FamilyQuestion(
            user_id=user_id,
            channel="whatsapp",
            raw_text="Who gets the house?",
            screening="sensitive",
            status="held",
        )
    )
    await real_db.commit()
    assert (await plan(real_db, session_id, user_id)).offered is None


async def test_an_old_question_expires(real_db):
    user_id, session_id = await seed(real_db, exchange_count=2)
    q = FamilyQuestion(
        id=uuid.uuid4(),
        user_id=user_id,
        channel="email",
        raw_text="What was school like?",
        screening="ok",
        status="pending",
        created_at=datetime.now(timezone.utc) - timedelta(days=22),
    )
    real_db.add(q)
    await real_db.commit()
    assert (await plan(real_db, session_id, user_id)).offered is None
    assert (await get(real_db, FamilyQuestion, q.id)).status == "expired"


async def test_the_older_of_card_and_question_goes_first(real_db):
    user_id, session_id = await seed(real_db, exchange_count=0)
    real_db.add(
        FamilyQuestion(
            user_id=user_id,
            channel="whatsapp",
            raw_text="Which film?",
            screening="ok",
            status="pending",
            created_at=datetime.now(timezone.utc) - timedelta(days=5),
        )
    )
    await real_db.commit()
    await waiting_card(real_db, user_id)  # newer
    p = await plan(real_db, session_id, user_id)
    assert p.offered == asks.CHILD_QUESTION


async def test_a_reaction_rides_along_with_an_ask(real_db):
    user_id, session_id = await seed(real_db, exchange_count=0)
    await waiting_card(real_db, user_id)
    r = FamilyQuestion(
        id=uuid.uuid4(),
        user_id=user_id,
        channel="whatsapp",
        kind="reaction",
        raw_text="I didn't know this!",
        screening="reaction",
        status="pending",
    )
    real_db.add(r)
    await real_db.commit()
    p = await plan(real_db, session_id, user_id)
    assert p.offered == asks.READBACK_OFFER
    assert any("never knew" in line for line in p.layer4)
    await asks.after_dialogue(
        p, {"reaction"}, session_id=session_id, user_id=user_id, db=real_db
    )
    assert (await get(real_db, FamilyQuestion, r.id)).status == "asked"


# ── through a whole turn ─────────────────────────────────────────────────────


async def _profile_for(user_id) -> UserProfile:
    return UserProfile(
        name="Kamala",
        preferred_language="ta-IN",
        onboarding_context="",
        cards=CardsContext(child_name="Priya", parent_title="Amma"),
    )


def _router(dialogue_reply: str, label: tuple[str, str] | None = None):
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
                    transcript=transcript, language_code="ta-IN", language_probability=1
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


async def test_a_whole_offer_and_read_back_through_process_voice_turn(real_db):
    user_id, _ = await seed(real_db)
    state_ = await session_manager.start_session(user_id, real_db)
    card_id = await waiting_card(real_db, user_id)
    profile = await _profile_for(user_id)
    tts = AsyncMock(return_value=b"WAV")

    chat, calls = _router(
        "<response>Vanakkam Amma! Shall I read it to you?</response>\n"
        "<asked>readback_offer</asked>"
    )
    with a_voice_note("Vanakkam", chat, tts):
        first = await orchestrator.process_voice_turn(
            b"a", state_.session_id, profile, real_db, inbound_message_sid="SM1"
        )
    assert first.response_text == "Vanakkam Amma! Shall I read it to you?"
    assert calls == ["dialogue"]
    # Layer 4 told the model to offer, and how to report it.
    assert (await get(real_db, ConversationCard, card_id)).readback_offers == 1

    chat, calls = _router("<response>unused</response>", label=("yes", "sollunga"))
    with a_voice_note("Sari, sollunga", chat, tts):
        second = await orchestrator.process_voice_turn(
            b"a", state_.session_id, profile, real_db, inbound_message_sid="SM2"
        )
    # The read-back is the stored text, and no dialogue call was made.
    assert second.response_text == READBACK
    assert calls == ["parent_answer_classify"]
    assert tts.await_args.kwargs["purpose"] == "card_readback_tts"
    turn = (
        await real_db.execute(select(Turn).where(Turn.id == second.turn_id))
    ).scalar_one()
    assert turn.response_text == READBACK


async def test_with_cards_off_the_turn_never_touches_card_state(real_db, monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", False)
    user_id, _ = await seed(real_db)
    state_ = await session_manager.start_session(user_id, real_db)
    profile = UserProfile(
        name="Kamala", preferred_language="ta-IN", onboarding_context=""
    )
    chat, calls = _router("<response>Tell me more.</response>")
    with (
        a_voice_note("Vanakkam", chat, AsyncMock(return_value=b"WAV")),
        patch("cards.asks.before_dialogue", new=AsyncMock()) as before,
    ):
        result = await orchestrator.process_voice_turn(
            b"a", state_.session_id, profile, real_db, inbound_message_sid="SM3"
        )
    before.assert_not_awaited()
    assert result.response_text == "Tell me more."
