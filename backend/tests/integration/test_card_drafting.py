"""
Drafting a card after a session, against real Postgres
(CONVERSATION_CARDS.md §7.1–§7.4, §12 "candidate selection"). The three
model calls are mocked by purpose; TC-17 measures the real ones.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from cards import candidates, state, write
from models.conversation_card import ConversationCard
from models.family_account import FamilyAccount
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

GOOD_CARD = {
    "title": "The walk to the talkies",
    "hook": "Did you know Amma walked six kilometres to see her first film?",
    "excerpt": "We walked all the way to the talkies, six kilometres, and told nobody.",
    "original_quote": "யாருக்கும் சொல்லல",
    "original_quote_gloss": "I told no one",
    "call_prompt": "What do you remember about that first film?",
    "held_back_note": "Left out which film it was, for the call.",
    "readback_text": "நீங்கள் ஆறு கிலோமீட்டர் நடந்து போனீர்கள்…",
}


def reply(payload) -> SimpleNamespace:
    return SimpleNamespace(
        content=json.dumps(payload, ensure_ascii=False),
        input_tokens=10,
        output_tokens=10,
    )


def all_supported(turn_id: str) -> dict:
    return {
        "sentences": [
            {
                "field": f,
                "text": GOOD_CARD[f],
                "verdict": "supported",
                "turn_ids": [turn_id],
            }
            for f in ("hook", "excerpt", "call_prompt", "original_quote")
        ]
    }


class Model:
    """A stand-in for llm.chat that answers by purpose, in order."""

    def __init__(self, **by_purpose):
        self.by_purpose = {k: list(v) for k, v in by_purpose.items()}
        self.calls: list[str] = []

    async def __call__(self, messages, **kwargs):
        purpose = kwargs["purpose"]
        self.calls.append(purpose)
        return reply(self.by_purpose[purpose].pop(0))


@pytest.fixture(autouse=True)
def cards_on(monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    monkeypatch.setattr("config.settings.CARDS_OPERATOR_REVIEW", True)


@pytest.fixture(autouse=True)
def s3(monkeypatch):
    """Card images go to this dict, never to S3."""
    stored: dict[str, bytes] = {}

    async def upload(data, key, content_type="audio/ogg"):
        stored[key] = data
        return key

    monkeypatch.setattr("cards.image.storage.upload_media", upload)
    return stored


@pytest.fixture
def alert():
    with patch("cards.write.send_operator_alert", new=AsyncMock()) as mock:
        yield mock


async def seed(db, *, share="share_with_approval", enabled=True, atoms=None):
    """A parent with one session; each atom spec is (transcript, quote,
    private). Returns plain ids (committed rows' attributes expire)."""
    user_id = f"draft-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9191{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ta-IN",
            pronouns="she/her",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=enabled,
            share_preference=share,
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
        Session(id=session_id, user_id=user_id, session_number=3, domain="childhood")
    )
    await db.commit()

    atom_ids, turn_ids = [], []
    specs = atoms or [
        ("We walked six kilometres to the talkies.", "told nobody", False)
    ]
    for i, (transcript, quote, private) in enumerate(specs):
        turn_id, atom_id = uuid.uuid4(), uuid.uuid4()
        db.add(
            Turn(
                id=turn_id,
                session_id=session_id,
                user_id=user_id,
                turn_number=i + 1,
                transcript=transcript,
                detected_language="ta-IN",
                response_text="And then?",
                is_private=private,
            )
        )
        await db.flush()
        db.add(
            StoryAtom(
                id=atom_id,
                session_id=session_id,
                turn_id=turn_id,
                user_id=user_id,
                domain="childhood",
                title=f"Atom {i}",
                narrative=transcript,
                verbatim_quote=quote,
            )
        )
        atom_ids.append(atom_id)
        turn_ids.append(turn_id)
    await db.commit()
    return SimpleNamespace(
        user_id=user_id, session_id=str(session_id), atoms=atom_ids, turns=turn_ids
    )


async def cards_of(db, user_id) -> list[ConversationCard]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(ConversationCard).where(ConversationCard.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


async def draft(db, seeded, model):
    with patch("cards.write.llm.chat", new=model):
        return await write.draft_for_session(seeded.session_id, seeded.user_id, db)


# ── the happy path ───────────────────────────────────────────────────────────


async def test_a_good_card_goes_to_the_operator_and_nobody_else(real_db, alert, s3):
    s = await seed(real_db)
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])], "why": "a first film"}],
        card_write=[GOOD_CARD],
        card_fidelity=[all_supported(str(s.turns[0]))],
    )
    outcome = await draft(real_db, s, model)

    assert outcome.result == "drafted"
    assert model.calls == ["card_select", "card_write", "card_fidelity"]
    [card] = await cards_of(real_db, s.user_id)
    assert card.status == "operator_review"
    assert card.hook == GOOD_CARD["hook"]
    assert card.source_atom_ids == [s.atoms[0]]
    assert card.source_turn_ids == [s.turns[0]]
    assert card.readback_language == "ta-IN"
    assert card.readback_hash == state.fingerprint(card)
    assert card.parent_answer is None and card.approved_hash is None
    assert card.fidelity_json["result"] == "passed"
    assert card.fidelity_json["sentences"][0]["turn_ids"] == [str(s.turns[0])]
    alert.assert_awaited_once()
    # The image shows these exact words, and is private.
    assert card.image_s3_key == f"cards/conv/{card.id}-{card.readback_hash[:12]}.png"
    assert s3[card.image_s3_key].startswith(b"\x89PNG")


async def test_with_review_off_the_card_waits_for_her(real_db, alert, monkeypatch):
    monkeypatch.setattr("config.settings.CARDS_OPERATOR_REVIEW", False)
    s = await seed(real_db)
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])]}],
        card_write=[GOOD_CARD],
        card_fidelity=[all_supported(str(s.turns[0]))],
    )
    await draft(real_db, s, model)
    [card] = await cards_of(real_db, s.user_id)
    assert card.status == "awaiting_parent"
    alert.assert_not_awaited()


async def test_none_is_a_good_answer(real_db, alert):
    s = await seed(real_db)
    model = Model(card_select=[{"atom_ids": [], "why": "nothing surprising"}])
    outcome = await draft(real_db, s, model)
    assert outcome.result == "none_selected"
    assert await cards_of(real_db, s.user_id) == []


async def test_an_atom_id_the_model_invents_is_ignored(real_db, alert):
    s = await seed(real_db)
    model = Model(card_select=[{"atom_ids": [str(uuid.uuid4())]}])
    assert (await draft(real_db, s, model)).result == "none_selected"


# ── fidelity ─────────────────────────────────────────────────────────────────


async def test_an_unsupported_sentence_gets_one_rewrite(real_db, alert):
    s = await seed(real_db)
    t = str(s.turns[0])
    invented = dict(
        GOOD_CARD, hook="Did you know Amma was terrified on her walk to the talkies?"
    )
    bad_check = {
        "sentences": [
            {
                "field": "hook",
                "text": invented["hook"],
                "verdict": "unsupported",
                "turn_ids": [],
                "reason": "she never said she was terrified",
            },
            {
                "field": "excerpt",
                "text": GOOD_CARD["excerpt"],
                "verdict": "supported",
                "turn_ids": [t],
            },
        ]
    }
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])]}],
        card_write=[invented, GOOD_CARD],
        card_fidelity=[bad_check, all_supported(t)],
    )
    outcome = await draft(real_db, s, model)

    assert outcome.result == "drafted"
    assert model.calls == [
        "card_select",
        "card_write",
        "card_fidelity",
        "card_write",
        "card_fidelity",
    ]
    [card] = await cards_of(real_db, s.user_id)
    assert card.hook == GOOD_CARD["hook"]
    assert card.fidelity_json["rewritten"] is True
    assert "terrified" in card.fidelity_json["passes"][0]["problems"][0]


async def test_still_unsupported_after_the_rewrite_is_dropped(real_db, alert):
    s = await seed(real_db)
    bad = {
        "sentences": [
            {
                "field": "hook",
                "text": GOOD_CARD["hook"],
                "verdict": "unsupported",
                "turn_ids": [],
                "reason": "invented",
            },
            {
                "field": "excerpt",
                "text": GOOD_CARD["excerpt"],
                "verdict": "supported",
                "turn_ids": [str(s.turns[0])],
            },
        ]
    }
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])]}],
        card_write=[GOOD_CARD, GOOD_CARD],
        card_fidelity=[bad, bad],
    )
    outcome = await draft(real_db, s, model)

    assert outcome.result == "failed_fidelity"
    [card] = await cards_of(real_db, s.user_id)
    assert card.status == "failed_fidelity"
    alert.assert_not_awaited()
    # Its atoms go back into the pool once.
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == s.user_id)
        )
    ).scalar_one()
    assert [a.id for a in await candidates.candidate_atoms(profile, real_db)] == s.atoms


@pytest.mark.parametrize(
    "check",
    [
        {"sentences": []},
        {"no": "sentences"},
        # Supported, but citing a turn the check was never shown.
        {
            "sentences": [
                {
                    "field": "hook",
                    "text": GOOD_CARD["hook"],
                    "verdict": "supported",
                    "turn_ids": ["not-a-turn"],
                }
            ]
        },
    ],
)
async def test_a_check_that_proves_nothing_is_a_failure(real_db, alert, check):
    s = await seed(real_db)
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])]}],
        card_write=[GOOD_CARD, GOOD_CARD],
        card_fidelity=[check, check],
    )
    assert (await draft(real_db, s, model)).result == "failed_fidelity"


async def test_length_limits_are_enforced_in_code(real_db, alert):
    s = await seed(real_db)
    long_hook = dict(GOOD_CARD, hook="Did you know " + "very " * 40 + "far?")
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])]}],
        card_write=[long_hook, long_hook],
        card_fidelity=[all_supported(str(s.turns[0]))] * 2,
    )
    outcome = await draft(real_db, s, model)
    assert outcome.result == "failed_fidelity"
    assert "hook is" in outcome.reason


# ── when no card is drafted (§7.1) ───────────────────────────────────────────


@pytest.mark.parametrize("share", ["not_asked", "unsure", "never"])
async def test_no_card_without_her_yes_to_sharing(real_db, alert, share):
    s = await seed(real_db, share=share)
    model = Model()
    outcome = await draft(real_db, s, model)
    assert outcome.result == "blocked"
    assert model.calls == []


async def test_no_card_when_cards_are_off_for_her(real_db, alert):
    s = await seed(real_db, enabled=False)
    model = Model()
    assert (await draft(real_db, s, model)).result == "off"
    assert model.calls == []


@pytest.mark.parametrize("status", sorted(state.IN_PIPELINE))
async def test_one_card_in_the_pipeline_at_a_time(real_db, alert, status):
    s = await seed(real_db)
    real_db.add(ConversationCard(user_id=s.user_id, status=status))
    await real_db.commit()
    assert (await draft(real_db, s, Model())).result == "blocked"


async def _leaving_card(db, user_id, sent_at):
    card = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        card,
        hook="Did you know…",
        excerpt="…",
        call_prompt="…?",
        title="t",
        readback_text="r",
    )
    card.status = "awaiting_parent"
    state.record_parent_answer(
        card, "approve", turn_id=None, heard_hash=card.readback_hash
    )
    state.transition(card, "scheduled")
    state.transition(card, "sent")
    card.email_sent_at = sent_at
    db.add(card)
    await db.commit()


async def test_two_cards_in_seven_days_is_the_limit(real_db, alert):
    s = await seed(real_db)
    now = datetime.now(timezone.utc)
    await _leaving_card(real_db, s.user_id, now - timedelta(days=1))
    await _leaving_card(real_db, s.user_id, now - timedelta(days=6))
    assert (await draft(real_db, s, Model())).result == "blocked"


async def test_a_card_older_than_seven_days_does_not_count(real_db, alert):
    s = await seed(real_db)
    now = datetime.now(timezone.utc)
    await _leaving_card(real_db, s.user_id, now - timedelta(days=1))
    await _leaving_card(real_db, s.user_id, now - timedelta(days=8))
    model = Model(card_select=[{"atom_ids": []}])
    assert (await draft(real_db, s, model)).result == "none_selected"


# ── candidates ───────────────────────────────────────────────────────────────


async def test_candidates_leave_out_private_turns_quoteless_and_used_atoms(real_db):
    s = await seed(
        real_db,
        atoms=[
            ("We walked to the talkies.", "told nobody", False),
            ("My brother fell ill.", "he was so thin", True),
            ("We had a cow.", None, False),
            ("We had a mango tree.", "  ", False),
            ("Ammachi's jasmine.", "jasmine in a brass bowl", False),
        ],
    )
    used = ConversationCard(
        user_id=s.user_id, status="kept_private", source_atom_ids=[s.atoms[4]]
    )
    real_db.add(used)
    await real_db.commit()
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == s.user_id)
        )
    ).scalar_one()
    picked = [a.id for a in await candidates.candidate_atoms(profile, real_db)]
    assert picked == [s.atoms[0]]


async def test_an_atom_that_failed_twice_stays_out(real_db):
    s = await seed(real_db)
    for _ in range(2):
        real_db.add(
            ConversationCard(
                user_id=s.user_id,
                status="failed_fidelity",
                source_atom_ids=[s.atoms[0]],
            )
        )
    await real_db.commit()
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == s.user_id)
        )
    ).scalar_one()
    assert await candidates.candidate_atoms(profile, real_db) == []


async def test_private_topics_reach_the_selection_prompt(real_db, alert):
    s = await seed(real_db)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == s.user_id)
        )
    ).scalar_one()
    profile.private_topics = ["my brother's illness"]
    await real_db.commit()
    model = Model(card_select=[{"atom_ids": []}])
    seen = []

    async def spy(messages, **kwargs):
        seen.append(messages[0].content)
        return await model(messages, **kwargs)

    with patch("cards.write.llm.chat", new=spy):
        await write.draft_for_session(s.session_id, s.user_id, real_db)
    assert "my brother's illness" in seen[0]
    assert "Priya" in seen[0] and "Amma" in seen[0]
    assert "she/her" in seen[0]


async def test_a_model_failure_drafts_nothing_and_does_not_raise(real_db, alert):
    s = await seed(real_db)
    with patch("cards.write.llm.chat", new=AsyncMock(side_effect=RuntimeError("down"))):
        outcome = await write.draft_for_session(s.session_id, s.user_id, real_db)
    assert outcome.result == "error"
    assert await cards_of(real_db, s.user_id) == []


async def test_an_image_failure_does_not_lose_the_card(real_db, alert, monkeypatch):
    async def broken(*args, **kwargs):
        raise RuntimeError("S3 is down")

    monkeypatch.setattr("cards.image.storage.upload_media", broken)
    s = await seed(real_db)
    model = Model(
        card_select=[{"atom_ids": [str(s.atoms[0])]}],
        card_write=[GOOD_CARD],
        card_fidelity=[all_supported(str(s.turns[0]))],
    )
    assert (await draft(real_db, s, model)).result == "drafted"
    [card] = await cards_of(real_db, s.user_id)
    assert card.status == "operator_review"
    assert card.image_s3_key is None
