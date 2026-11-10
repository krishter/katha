"""
Stories Stage 1 against real Postgres (docs/specs/STORIES.md §2.1, §2.2,
§2.4, §4, D-f). The model is scripted per step, so these test what the code
does with its answers: what reaches the prompt, what is written, what the
family can see, and what the database refuses. Nothing leaves the machine.
"""

from __future__ import annotations

import json
import uuid
from datetime import time
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from api.routes.family import export_data
from cards import candidates
from core import erasure
from models.family_account import FamilyAccount
from models.session import Session
from models.story import Story, StoryVersion
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from stories import consolidate, review, store

pytestmark = pytest.mark.integration

PRIVATE_WORDS = "my brother's debts, which nobody must know about"


@pytest.fixture(autouse=True)
def stories_on(monkeypatch):
    monkeypatch.setattr("config.settings.STORIES_ENABLED", True)
    monkeypatch.setattr("config.settings.STORIES_OPERATOR_REVIEW", True)


async def seed_family(db, *, enabled=True):
    user_id = f"story-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Saraswathi",
            whatsapp_number=f"+9190{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ml-IN",
            scheduled_time=time(10, 0),
            stories_enabled=enabled,
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
    await db.commit()
    return user_id


async def seed_session(db, user_id, number, said: list[tuple[str, bool]]):
    """One closed session; `said` is (transcript, is_private) per turn. Each
    turn gets one atom with a quote, as extraction would leave it."""
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=number,
        domain="childhood",
        status="completed",
    )
    db.add(session)
    await db.flush()
    turns, atoms = [], []
    for i, (transcript, private) in enumerate(said, start=1):
        turn = Turn(
            id=uuid.uuid4(),
            session_id=session.id,
            user_id=user_id,
            turn_number=i,
            transcript=transcript,
            detected_language="ml-IN",
            response_text="എന്നിട്ട്?",
            extraction_status="done",
            is_private=private,
        )
        db.add(turn)
        await db.flush()
        atom = StoryAtom(
            id=uuid.uuid4(),
            session_id=session.id,
            turn_id=turn.id,
            user_id=user_id,
            domain="childhood",
            narrative=f"Summary of turn {i}",
            who=[],
            completeness_score=3,
            verbatim_quote=transcript[:40],
        )
        db.add(atom)
        turns.append(turn)
        atoms.append(atom)
    await db.commit()
    return SimpleNamespace(session=session, turns=turns, atoms=atoms)


def _arc(setting=(), event=(), turn=(), resolution=()):
    def element(ids):
        return {"present": bool(ids), "summary": "x" if ids else "", "turn_ids": ids}

    return {
        "setting": element([str(i) for i in setting]),
        "event": element([str(i) for i in event]),
        "turn": element([str(i) for i in turn]),
        "resolution": element([str(i) for i in resolution]),
    }


class ScriptedModel:
    """Answers each consolidation step from a script, and keeps every
    prompt it was shown."""

    def __init__(self, grouping: list[dict], cited: list[str]):
        self.grouping = list(grouping)
        self.cited = cited
        self.prompts: list[tuple[str, str]] = []

    async def __call__(self, prompt, *, step, max_tokens):
        self.prompts.append((step, prompt))
        if step == "group":
            reply = self.grouping.pop(0)
        elif step == "compose":
            reply = {
                "title": "The night the river came in",
                "body_first_person": "I was ten. The water came in. "
                "Appa carried my grandmother out.",
                "quotes": [{"original": "വെള്ളം കയറി", "gloss": "the water came in"}],
            }
        elif step == "fidelity":
            reply = {
                "sentences": [
                    {"text": t, "verdict": "supported", "turn_ids": self.cited}
                    for t in (
                        "I was ten.",
                        "The water came in.",
                        "Appa carried my grandmother out.",
                    )
                ]
            }
        elif step == "readback":
            reply = {"readback_text": "ഇത് അമ്മ പറഞ്ഞ കഥയാണ്. ... ഇത് ശരിയാണോ?"}
        elif step == "readback_check":
            reply = {
                "sentences": [{"text": "ഇത് അമ്മ പറഞ്ഞ കഥയാണ്.", "verdict": "framing"}],
                "missing_from_readback": [],
            }
        else:
            raise AssertionError(f"unexpected step {step}")
        return json.dumps(reply, ensure_ascii=False), 100, 50

    def shown(self) -> str:
        return "\n".join(p for _, p in self.prompts)


async def _run(db, user_id, session, model):
    with patch.object(consolidate, "_call_llm", model):
        return await consolidate.consolidate_for_session(str(session.id), user_id, db)


async def _stories(db, user_id) -> list[Story]:
    rows = await db.execute(select(Story).where(Story.user_id == user_id))
    return list(rows.scalars().all())


async def _one_story_session(db, user_id):
    s = await seed_session(
        db,
        user_id,
        1,
        [
            ("We lived by the river. I was ten.", False),
            (PRIVATE_WORDS, True),
            ("The water came in and Appa carried my grandmother.", False),
        ],
    )
    t1, t2, t3 = (str(t.id) for t in s.turns)
    a1, a2, a3 = (str(a.id) for a in s.atoms)
    model = ScriptedModel(
        [
            {
                "incidents": [
                    {
                        "working_title": "The flood",
                        "atom_ids": [a1, a2, a3],
                        "extends_thread_id": None,
                        "domain": "childhood",
                        "when_approx": "when I was ten",
                        "arc": _arc(setting=[t1], event=[t3, t2], turn=[t3]),
                    }
                ],
                "unassigned_atom_ids": [],
            }
        ],
        cited=[t1, t3],
    )
    return s, model


async def test_a_told_incident_becomes_a_story_waiting_for_the_operator(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)

    outcome = await _run(real_db, user_id, s.session, model)

    assert outcome.result == "done" and len(outcome.promoted) == 1
    (story,) = await _stories(real_db, user_id)
    assert story.status == "story"
    assert story.review_status == "operator_review"
    assert story.approval_status == "pending"
    assert story.body_first_person.startswith("I was ten.")
    assert story.readback_check["result"] == "passed"
    assert story.readback_hash == store.fingerprint(story)
    assert story.sessions_seen == [1]
    # The private turn was never shown to the model, and its atom is not
    # part of the story even though the model listed it.
    assert PRIVATE_WORDS not in model.shown()
    placed = {
        a.id
        for a in (
            await real_db.execute(
                select(StoryAtom).where(StoryAtom.story_id == story.id)
            )
        ).scalars()
    }
    assert placed == {s.atoms[0].id, s.atoms[2].id}


async def test_the_same_session_is_never_consolidated_twice(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    await _run(real_db, user_id, s.session, model)

    again = await _run(real_db, user_id, s.session, ScriptedModel([], cited=[]))

    assert again.result == "already_done"
    assert len(await _stories(real_db, user_id)) == 1


async def test_with_her_switch_off_nothing_runs(real_db):
    user_id = await seed_family(real_db, enabled=False)
    s, _ = await _one_story_session(real_db, user_id)
    model = ScriptedModel([], cited=[])

    outcome = await _run(real_db, user_id, s.session, model)

    assert outcome.result == "off"
    assert model.prompts == []
    assert await _stories(real_db, user_id) == []


async def test_a_thread_from_last_week_is_finished_and_told_from_both(real_db):
    user_id = await seed_family(real_db)
    first = await seed_session(
        real_db, user_id, 1, [("We lived by the river when I was ten.", False)]
    )
    t1 = str(first.turns[0].id)
    await _run(
        real_db,
        user_id,
        first.session,
        ScriptedModel(
            [
                {
                    "incidents": [
                        {
                            "working_title": "The flood",
                            "atom_ids": [str(first.atoms[0].id)],
                            "extends_thread_id": None,
                            "arc": _arc(setting=[t1], event=[t1]),
                        }
                    ]
                }
            ],
            cited=[t1],
        ),
    )
    (thread,) = await _stories(real_db, user_id)
    assert thread.status == "thread" and thread.approval_status is None

    second = await seed_session(
        real_db, user_id, 2, [("Appa carried my grandmother to the temple.", False)]
    )
    t2 = str(second.turns[0].id)
    model = ScriptedModel(
        [
            {
                "incidents": [
                    {
                        "working_title": "The flood",
                        "atom_ids": [str(second.atoms[0].id)],
                        "extends_thread_id": str(thread.id),
                        "arc": _arc(turn=[t2]),
                    }
                ]
            }
        ],
        cited=[t1, t2],
    )
    outcome = await _run(real_db, user_id, second.session, model)

    assert outcome.promoted == [thread.id]
    await real_db.refresh(thread)
    assert thread.status == "story" and thread.sessions_seen == [1, 2]
    compose = next(p for step, p in model.prompts if step == "compose")
    assert "We lived by the river" in compose and "temple" in compose


async def test_the_database_refuses_an_approval_she_did_not_give(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    await _run(real_db, user_id, s.session, model)
    (story,) = await _stories(real_db, user_id)
    story_id = story.id

    story.review_status = "passed"
    story.approval_status = "approved"  # no approved_hash: she never said yes
    with pytest.raises(IntegrityError):
        await real_db.commit()
    await real_db.rollback()

    story = (
        await real_db.execute(
            select(Story)
            .where(Story.id == story_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    story.review_status = "passed"
    story.approval_status = "approved"
    story.approved_hash = story.readback_hash
    await real_db.commit()


async def test_the_family_export_has_only_stories_she_approved(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    await _run(real_db, user_id, s.session, model)
    me = {"user_id": user_id}

    pending = await export_data(current_user=me, db=real_db)
    assert pending["first_person_stories"] == []

    (story,) = await _stories(real_db, user_id)
    story.review_status = "passed"
    story.approval_status = "approved"
    story.approved_hash = story.readback_hash
    await real_db.commit()
    approved = await export_data(current_user=me, db=real_db)
    assert [x["title"] for x in approved["first_person_stories"]] == [story.title]


async def test_a_storys_material_leaves_the_card_pool(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    before = {a.id for a in await candidates.candidate_atoms(profile, real_db)}
    assert s.atoms[0].id in before

    await _run(real_db, user_id, s.session, model)

    after = {a.id for a in await candidates.candidate_atoms(profile, real_db)}
    assert s.atoms[0].id not in after and s.atoms[2].id not in after


async def test_the_operator_can_pass_only_a_checked_readback(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    await _run(real_db, user_id, s.session, model)
    (story,) = await _stories(real_db, user_id)

    story.readback_check = {"result": "failed", "passes": []}
    await real_db.commit()
    with pytest.raises(review.ReviewError):
        await review.pass_story(story.id, real_db)

    story.readback_check = {"result": "passed", "passes": []}
    await real_db.commit()
    passed = await review.pass_story(story.id, real_db)
    assert passed.review_status == "passed"
    assert passed.approval_status == "pending"


async def test_retiring_a_story_returns_its_atoms(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    await _run(real_db, user_id, s.session, model)
    (story,) = await _stories(real_db, user_id)

    await review.retire(story.id, "two incidents merged", real_db)

    await real_db.refresh(story)
    assert story.status == "retired" and story.retired_reason == "operator"
    linked = (
        await real_db.execute(
            select(func.count(StoryAtom.id)).where(StoryAtom.story_id == story.id)
        )
    ).scalar_one()
    assert linked == 0


async def test_erasure_removes_her_stories_and_versions(real_db):
    user_id = await seed_family(real_db)
    s, model = await _one_story_session(real_db, user_id)
    await _run(real_db, user_id, s.session, model)
    (story,) = await _stories(real_db, user_id)
    real_db.add(
        StoryVersion(story_id=story.id, user_id=user_id, version=1, title="old")
    )
    await real_db.commit()

    report = await erasure.erase_user(user_id, real_db, actor=erasure.ACTOR_FAMILY)

    assert "table:stories" not in report.failures
    assert await _stories(real_db, user_id) == []
    left = (
        await real_db.execute(
            select(func.count(StoryVersion.id)).where(StoryVersion.user_id == user_id)
        )
    ).scalar_one()
    assert left == 0
