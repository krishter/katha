"""
End-to-end pilot rehearsal (REMEDIATION_PLAN WS5.1) — drives a full,
realistic session through orchestrator.process_voice_turn against a real
Postgres database, mocking only the paid external APIs (Sarvam STT/TTS,
Anthropic, OpenAI embeddings) and the Twilio send (via the stub adapter,
already the test-time default). Everything else — turn persistence, story
atom extraction and embedding, session lifecycle, domain progression, and
memory card generation — runs for real.

Scenario: 5 turns, one elderly user, one session, domain "childhood"
(target_story_atoms=3):
  1. Opening exchange — no story content yet.
  2-4. A story unfolds across three turns, one story atom each — by turn 4
     the cumulative count (3) reaches the domain target and goal_met
     flips true. Per the fix in this same PR (discovered by writing this
     very test — the session used to close immediately on this turn,
     before the dialogue call ever got a chance to deliver a closing
     message), closing defers to the next turn.
  5. The closing exchange — the dialogue call now sees goal_met=True and
     (per Layer 4) wraps up; the session closes at the end of this turn.
"""

from __future__ import annotations

import json
import uuid
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from core import orchestrator, session_manager
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import PriorContext, UserProfile

pytestmark = pytest.mark.integration

_FAKE_WAV = b"RIFF" + b"\x00" * 40
_DIALOGUE_CONTENT = "<response>Tell me more about that.</response>"


def _atom(title: str) -> dict:
    return {
        "domain": "childhood",
        "title": title,
        "narrative": f"A story about {title}.",
        "who": ["father"],
        "what": title,
        "when_approx": "circa 1958",
        "where_approx": "Madurai",
        "why": "A core childhood memory",
        "verbatim_quote": f"I remember {title} so clearly.",
        "open_threads": [],
    }


def _extraction_content(atoms: list[dict]) -> str:
    payload = {
        "story_atoms": atoms,
        "named_entities": {},
        "significant_people": [],
        "themes": ["childhood"],
        "energy_signal": "high",
        "gaps_remaining": [],
        "session_end_suggested": False,
    }
    return f"<extraction>{json.dumps(payload)}</extraction>"


# One extraction result per turn, in order. Turns 2-4 each add one atom;
# by turn 4 the cumulative count (3) meets the domain target.
_EXTRACTIONS_BY_TURN = {
    1: _extraction_content([]),
    2: _extraction_content([_atom("the street outside our house")]),
    3: _extraction_content([_atom("my mother's kitchen")]),
    4: _extraction_content([_atom("the neighbour who made sweets")]),
    5: _extraction_content([]),
}


async def test_full_session_pilot_rehearsal(real_db):
    db = real_db
    user_id = f"pilot-test-{uuid.uuid4().hex[:8]}"

    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Subramaniam",
            whatsapp_number="+919876500000",
            preferred_language="ta-IN",
            onboarding_context="Grew up in Madurai.",
            family_whatsapp_number="+919876511111",
            scheduled_time=time(10, 30),
        )
    )
    await db.commit()

    profile = UserProfile(
        name="Subramaniam", preferred_language="ta-IN", onboarding_context=""
    )
    state = await session_manager.start_session(user_id, db)
    session_id = state.session_id
    assert state.domain == "childhood"

    extraction_queue = list(_EXTRACTIONS_BY_TURN[i] for i in range(1, 6))

    async def fake_llm_chat(messages, system=None, max_tokens=500):
        if system is not None:
            # The dialogue call.
            return SimpleNamespace(
                content=_DIALOGUE_CONTENT, input_tokens=100, output_tokens=50
            )
        # The extraction call — one queued result per turn, in order.
        content = extraction_queue.pop(0)
        return SimpleNamespace(content=content, input_tokens=50, output_tokens=100)

    turn_ids = []
    with (
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                side_effect=lambda audio_bytes: SimpleNamespace(
                    transcript=f"Transcript for turn (len={len(audio_bytes)})",
                    language_code="ta-IN",
                    language_probability=0.95,
                )
            ),
        ),
        patch("core.orchestrator.llm.chat", new=AsyncMock(side_effect=fake_llm_chat)),
        patch(
            "core.orchestrator.sarvam_tts.synthesize",
            new=AsyncMock(return_value=_FAKE_WAV),
        ),
        patch(
            "core.orchestrator.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda audio_bytes: audio_bytes),
        ),
        patch(
            "core.orchestrator.entity_extractor.extract_entities",
            new=AsyncMock(),
        ),
        patch(
            "core.orchestrator.storage.upload_media",
            new=AsyncMock(return_value="cards/pilot-test.png"),
        ),
    ):
        for turn_num in range(1, 6):
            result = await orchestrator.process_voice_turn(
                f"audio-bytes-turn-{turn_num}".encode(),
                session_id,
                profile,
                db,
                inbound_message_sid=f"SM_PILOT_{turn_num}",
            )
            assert result.turn_id is not None
            turn_ids.append(result.turn_id)

            await orchestrator.run_extraction_for_turn(
                result.turn_id,
                session_id,
                result.session_state,
                profile,
                PriorContext(),
                f"transcript for turn {turn_num}",
                result.response_text,
                db,
            )

    # ── 6 turns rows persisted with transcripts ──────────────────────────
    # (5 conversational turns in this rehearsal — see module docstring for
    # why the deferred-close fix means the session closes after exactly
    # one turn past goal_met, not two.)
    turns_result = await db.execute(
        select(Turn).where(Turn.session_id == uuid.UUID(session_id))
    )
    turns = turns_result.scalars().all()
    assert len(turns) == 5
    assert {t.turn_number for t in turns} == {1, 2, 3, 4, 5}
    assert all(t.transcript for t in turns)
    assert len(set(t.id for t in turns)) == 5  # no duplicate rows

    # ── Story atoms from turns 2, 3, AND 4 present — not just the last ───
    atoms_result = await db.execute(
        select(StoryAtom).where(StoryAtom.session_id == uuid.UUID(session_id))
    )
    atoms = atoms_result.scalars().all()
    assert len(atoms) == 3
    atom_turn_ids = {a.turn_id for a in atoms}
    assert atom_turn_ids == set(turn_ids[1:4])  # turns 2, 3, 4 (0-indexed 1,2,3)

    # ── The embedding column is retained but never written ───────────────
    # S1.5 removed the OpenAI embedding call. story_atoms.embedding and the
    # pgvector extension are deliberately kept for a Phase 2 reinstatement,
    # so this asserts the current contract rather than the old one: nothing
    # writes to the column, and nothing flags a failure to.
    for atom in atoms:
        assert atom.embedding is None
        assert atom.embedding_failed is False

    # ── Exactly one memory card generated and delivered ──────────────────
    cards_result = await db.execute(
        select(MemoryCard).where(MemoryCard.user_id == user_id)
    )
    cards = cards_result.scalars().all()
    assert len(cards) == 1
    assert cards[0].delivered_at is not None

    # ── Session completed with ended_reason set ───────────────────────────
    session_result = await db.execute(
        select(Session).where(Session.id == uuid.UUID(session_id))
    )
    session_row = session_result.scalar_one()
    assert session_row.status == "completed"
    assert session_row.ended_reason == "goal_met"
    assert session_row.goal_met is True


# ── S2.5: older domains must survive retrieval ───────────────────────────────


@pytest.mark.integration
async def test_layer3_still_carries_the_earliest_domain(real_db):
    """
    A user several weeks into the interview has atoms across many domains.
    Retrieval is ordered by recency, so if the window is narrow the domains
    they covered first fall out of Layer 3 entirely — a probe at top_k=5
    showed `childhood` disappearing completely once six domains existed.

    Seeds six domains, three atoms each, oldest first, and asserts the
    earliest one still reaches the prompt.
    """
    import uuid as _uuid
    from datetime import datetime, timedelta, timezone

    from core import orchestrator
    from models.story_atom import StoryAtom
    from prompts.system_prompt import (
        PriorContext,
        UserProfile,
        build_system_prompt,
    )

    user_id = f"s25-{_uuid.uuid4().hex[:8]}"
    domains = [
        "childhood",
        "family_ancestors",
        "education",
        "career",
        "love_marriage",
        "historical_events",
    ]

    base = datetime.now(timezone.utc) - timedelta(days=len(domains) * 2)
    for day, domain in enumerate(domains):
        # story_atoms.session_id is a real FK, so each domain needs the
        # session it was captured in.
        session_id = _uuid.uuid4()
        real_db.add(
            Session(
                id=session_id,
                user_id=user_id,
                session_number=day + 1,
                domain=domain,
                exchange_count=3,
                status="completed",
            )
        )
        await real_db.flush()
        for n in range(3):
            real_db.add(
                StoryAtom(
                    session_id=session_id,
                    user_id=user_id,
                    domain=domain,
                    title=f"{domain} story {n}",
                    narrative=f"A story about {domain}, number {n}.",
                    who=["someone"],
                    completeness_score=3,
                    open_threads=[f"{domain.upper()}-THREAD-{n}"],
                    created_at=base + timedelta(days=day * 2, minutes=n),
                )
            )
    await real_db.commit()

    prior = await orchestrator.build_prior_context(user_id, "wisdom", real_db)

    profile = UserProfile(
        name="Subramaniam",
        preferred_language="en-IN",
        onboarding_context="",
    )
    state = SimpleNamespace(
        session_id="s25-session",
        user_id=user_id,
        session_number=7,
        domain="wisdom",
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    prompt = build_system_prompt(profile, state, prior)

    assert isinstance(prior, PriorContext)
    # The oldest domain must not have been starved out of retrieval.
    assert any("CHILDHOOD-THREAD" in t for t in prior.open_threads), (
        f"earliest domain missing from retrieved threads: {prior.open_threads}"
    )
    # ...and must survive the render cap, which trims breadth-last.
    assert "CHILDHOOD-THREAD" in prompt, (
        "earliest domain was retrieved but trimmed out of the rendered prompt"
    )

    # Every domain should get a look in, rather than one filling the budget.
    rendered_domains = {d for d in domains if f"{d.upper()}-THREAD" in prompt}
    assert len(rendered_domains) >= 5, (
        f"only {len(rendered_domains)} of {len(domains)} domains reached the "
        f"prompt: {rendered_domains}"
    )

    # And the list stays bounded.
    thread_lines = [
        line for line in prompt.splitlines() if line.strip().startswith("- ")
    ]
    assert len(thread_lines) <= 20, len(thread_lines)


# ── significant-person lifecycle (TC-11 shape) ───────────────────────────────


@pytest.mark.integration
async def test_significant_person_survives_to_be_resurfaced_then_resolves(real_db):
    """
    The full arc TECH_DESIGN 3.3 TC-11 describes, against real Postgres:
    flagged in session 2, still present in session 6's prior_context, and
    only then retired once an atom elaborates on them.

    Before this, `mark_resolved` scanned the atoms of the pass that flagged
    the person, so Mr. Iyer was retired in session 2 and session 6 never saw
    him. The mocked unit tests could not catch it — one of them asserted the
    behaviour was correct.
    """
    import uuid as _uuid

    from core import orchestrator
    from extraction import story_extractor
    from memory import fact_store

    user_id = f"tc11-{_uuid.uuid4().hex[:8]}"
    session_2 = _uuid.uuid4()
    session_6 = _uuid.uuid4()
    for sid, number, domain in (
        (session_2, 2, "family_ancestors"),
        (session_6, 6, "career"),
    ):
        real_db.add(
            Session(
                id=sid,
                user_id=user_id,
                session_number=number,
                domain=domain,
                exchange_count=2,
                status="completed",
            )
        )
    await real_db.commit()

    iyer = {
        "name": "Mr. Iyer",
        "relationship": "school teacher",
        "why_significant": "Said he had it in him when he failed twice",
    }

    # Session 2 — flagged, alongside a complete atom about him. This is
    # exactly the shape that used to retire him on the spot.
    await story_extractor.process_extraction(
        {
            "story_atoms": [
                {
                    "domain": "education",
                    "narrative": "Mr. Iyer never let me feel small.",
                    "who": ["Mr. Iyer"],
                    "what": "Encouragement through repeated failure",
                    "when_approx": "1960s",
                    "where_approx": "Madurai",
                    "why": "Lasting source of confidence",
                }
            ],
            "significant_people": [iyer],
        },
        str(session_2),
        user_id,
        real_db,
        session_number=2,
    )

    after_s2 = await fact_store.get_significant_people(user_id, real_db)
    assert any("iyer" in p["name"].lower() for p in after_s2), (
        f"Mr. Iyer was retired in the very session that flagged him: {after_s2}"
    )

    # He must reach session 6's prompt, which is what "resurfacing" needs.
    prior = await orchestrator.build_prior_context(user_id, "career", real_db)
    assert any("iyer" in p["name"].lower() for p in prior.significant_people)

    # Session 6 — the user elaborates. NOW he retires.
    result = await story_extractor.process_extraction(
        {
            "story_atoms": [
                {
                    "domain": "career",
                    "narrative": "Mr. Iyer's belief is why I kept going at work.",
                    "who": ["Mr. Iyer"],
                    "what": "Belief carried into working life",
                    "when_approx": "1970s",
                    "where_approx": "Bangalore",
                    "why": "Explains his persistence",
                }
            ],
            "significant_people": [iyer],
        },
        str(session_6),
        user_id,
        real_db,
        session_number=6,
    )

    assert "Mr. Iyer" in result.resolved_people
    after_s6 = await fact_store.get_significant_people(user_id, real_db)
    assert not any("iyer" in p["name"].lower() for p in after_s6), (
        f"resolved people must drop out of Layer 3 injection: {after_s6}"
    )


@pytest.mark.integration
async def test_first_seen_session_is_not_moved_by_a_later_mention(real_db):
    """Re-mentioning someone must not make them newly discovered, or a
    long-known person keeps losing the anchor to whoever spoke last."""
    import uuid as _uuid

    from memory import fact_store

    user_id = f"prov-{_uuid.uuid4().hex[:8]}"
    person = {"name": "Kamala", "relationship": "sister"}

    await fact_store.upsert_significant_person(
        user_id, person, real_db, session_number=2
    )
    await fact_store.upsert_significant_person(
        user_id,
        {**person, "why_significant": "mentioned again"},
        real_db,
        session_number=9,
    )

    people = await fact_store.get_significant_people(user_id, real_db)
    assert len(people) == 1
    assert people[0]["first_seen_session"] == 2
    assert people[0]["why_significant"] == "mentioned again"
