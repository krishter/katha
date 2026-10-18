"""
Katha eval regression — TC-01 through TC-11.

The test cases and their pass criteria are defined in
`docs/TECH_DESIGN.md` Section 3.3. That document is the spec; this file is
only its executable form. **Every criterion here must correspond to
something §3.3 actually states** — see evals/README.md for why that rule
exists and what happened when it was not followed.

Methodology: real Postgres, real Anthropic calls through the actual
orchestrator / system_prompt / extraction code paths. STT and TTS are
bypassed by feeding transcripts directly, which touches nothing in the
memory or extraction pipeline; the consequence is that TC-06 measures
extraction on code-mixed *text*, not Sarvam's transcription of code-mixed
*audio*, so its pass is narrower than it looks.

Filler sessions (used only to advance the domain sequence so a later
session lands on a specific domain) insert StoryAtom rows directly rather
than through real LLM turns. Every prompt or response that is the subject
of a pass/fail judgement goes through build_system_prompt or
build_extraction_prompt and a real Anthropic call.

Usage, from the backend/ directory with Postgres reachable:

    DATABASE_URL="postgresql+asyncpg://katha:katha@localhost:5432/katha" \
      ./.venv/bin/python evals/run_eval.py            # all cases
    ... evals/run_eval.py tc03 tc11                   # selected cases

Results are written to evals/results/<timestamp>.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import pathlib
import re
import sys
import uuid
from datetime import datetime, time, timezone
from types import SimpleNamespace

# Import the backend package from evals/, wherever the repo is checked out.
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import delete, select  # noqa: E402

from config import settings  # noqa: E402
from core import (  # noqa: E402
    conversation_policy,
    orchestrator,
    pricing,
    session_manager,
    usage,
)
from extraction import entity_extractor, story_extractor  # noqa: E402
from memory import fact_store  # noqa: E402
from models.api_usage import ApiUsage  # noqa: E402
from models.consent_record import ConsentRecord  # noqa: E402
from models.conversation_card import ConversationCard  # noqa: E402
from models.db import AsyncSessionLocal, engine  # noqa: E402
from models.fact import Fact  # noqa: E402
from models.family_account import FamilyAccount  # noqa: E402
from models.family_question import FamilyQuestion  # noqa: E402
from models.memory_card import MemoryCard  # noqa: E402
from models.session import Session  # noqa: E402
from models.story_atom import StoryAtom  # noqa: E402
from models.turn import Turn  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402
from prompts.domains import get_domain  # noqa: E402
from prompts.system_prompt import (  # noqa: E402
    CardsContext,
    UserProfile,
    build_system_prompt_parts,
)

RESULTS: list[dict] = []

# EVAL_CARDS=1 runs every case with conversation cards on for the parent
# (CONVERSATION_CARDS.md §12: "Layers 2 and 4 change, so the full
# regression set runs too"). She has already said yes to sharing and has
# no card or question waiting, so what changes is what every cards family
# gets on every turn: the Layer 2 rules, the extraction's share fields and
# the closing recap.
EVAL_CARDS = os.environ.get("EVAL_CARDS") == "1"

# Every paid call the run made, as read back from api_usage before cleanup
# deletes it (docs/specs/COST_TO_SERVE.md §14 step 2: the ledger, not a
# model estimate, is where the run's cost comes from). Keyed by row id so a
# row seen twice is counted once.
USAGE_ROWS: dict[str, dict] = {}
RUN_STARTED = datetime.now(timezone.utc)

# COST_TO_SERVE.md §6: a cheaper extraction model must not paraphrase a
# parent's words. Every non-null verbatim_quote extracted during the run is
# checked against the transcript it came from. Kept apart from the TC counts
# so the pass rates stay comparable with earlier runs.
VERBATIM_CHECKS: list[dict] = []


def record(tc, kind, passed, evidence):
    RESULTS.append({"tc": tc, "kind": kind, "passed": passed, "evidence": evidence})
    print(f"\n{'=' * 90}\n{tc} [{kind}] -> {'PASS' if passed else 'FAIL'}\n{'-' * 90}")
    print(evidence)


async def capture_usage(db, user_id: str | None = None) -> None:
    """Copy this run's ledger rows (all eval users, or one) into USAGE_ROWS."""
    query = select(ApiUsage).where(ApiUsage.created_at >= RUN_STARTED)
    if user_id is not None:
        query = query.where(ApiUsage.user_id == user_id)
    else:
        query = query.where(ApiUsage.user_id.like("eval-%"))
    for row in (await db.execute(query)).scalars().all():
        USAGE_ROWS[str(row.id)] = {
            "user_id": row.user_id,
            "session_id": str(row.session_id) if row.session_id else None,
            "vendor": row.vendor,
            "operation": row.operation,
            "model": row.model,
            "units": dict(row.units or {}),
        }


async def cleanup(db, user_id: str) -> None:
    await capture_usage(db, user_id)
    if os.environ.get("EVAL_SKIP_CLEANUP") == "1":
        return
    for model in (
        ApiUsage,
        ConsentRecord,
        ConversationCard,
        FamilyQuestion,
        FamilyAccount,
        MemoryCard,
        StoryAtom,
        Turn,
        Fact,
        Session,
        UserProfileModel,
    ):
        await db.execute(delete(model).where(model.user_id == user_id))
    await db.commit()


async def seed_profile(db, user_id: str, name="Subramaniam") -> UserProfileModel:
    profile = UserProfileModel(
        user_id=user_id,
        name=name,
        whatsapp_number="+919000000001",
        preferred_language="en-IN",
        onboarding_context="Grew up in Tamil Nadu. Retired schoolteacher.",
        family_whatsapp_number=None,
        scheduled_time=time(10, 30),
        timezone="Asia/Kolkata",
        conversation_cards_enabled=EVAL_CARDS,
        share_preference="share_with_approval" if EVAL_CARDS else "not_asked",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


def to_prompt_profile(row: UserProfileModel) -> UserProfile:
    return UserProfile(
        name=row.name,
        preferred_language=row.preferred_language,
        onboarding_context=row.onboarding_context,
        pronouns=row.pronouns or "",
        katha_gender=row.katha_gender,
        cards=CardsContext(child_name="Priya", parent_title="Appa")
        if EVAL_CARDS
        else None,
    )


async def seed_filler_atoms(
    db, user_id: str, session_id: str, domain_id: str, count: int
):
    """Insert synthetic, unrelated StoryAtom rows directly so _select_domain
    advances past `domain_id` without a real LLM call. Used only for domain
    scaffolding between the sessions actually under test."""
    for i in range(count):
        atom = StoryAtom(
            session_id=uuid.UUID(session_id),
            user_id=user_id,
            domain=domain_id,
            title=f"filler-{domain_id}-{i}",
            narrative=f"Synthetic filler atom {i} for {domain_id} domain scaffolding.",
            who=["placeholder"],
            what="placeholder detail",
            when_approx="circa 1970",
            where_approx="placeholder place",
            why="placeholder reason",
            completeness_score=4,
            verbatim_quote=None,
            open_threads=[],
        )
        db.add(atom)
    await db.commit()


async def top_up_domain(db, user_id: str, session_id: str, domain_id: str, target: int):
    """Query actual current atom count for `domain_id` and seed exactly
    enough filler to reach `target`, regardless of what domain string a
    real turn's extraction actually used."""
    from sqlalchemy import func as _func
    from sqlalchemy import select as _sel

    result = await db.execute(
        _sel(_func.count())
        .select_from(StoryAtom)
        .where(StoryAtom.user_id == user_id)
        .where(StoryAtom.domain == domain_id)
    )
    current = result.scalar_one()
    if current < target:
        await seed_filler_atoms(db, user_id, session_id, domain_id, target - current)


async def real_turn(
    db, user_id, session_id, state, prompt_profile, transcript, prior_context=None
):
    """One real turn through the actual production code path: build real
    prior_context (unless overridden), assemble the real system prompt,
    make a real Anthropic dialogue call, persist, make a real Anthropic
    extraction call, persist atoms/facts, apply extraction to session
    state. Mirrors orchestrator.process_voice_turn + run_extraction_for_turn
    minus STT/TTS/WhatsApp."""
    with usage.usage_scope(user_id=user_id, session_id=session_id):
        return await _real_turn(
            db, user_id, session_id, state, prompt_profile, transcript, prior_context
        )


async def _real_turn(
    db, user_id, session_id, state, prompt_profile, transcript, prior_context
):
    if prior_context is None:
        # The same path as production: built on the session's first turn
        # and reused after it when LAYER3_FREEZE_PER_SESSION is on.
        prior_context = await orchestrator.prior_context_for_session(
            session_id, user_id, state.domain, db
        )

    # With cards on, the card asks exactly as process_voice_turn makes them.
    plan = None
    if prompt_profile.cards is not None:
        import dataclasses

        from cards import asks as card_asks

        plan = await card_asks.before_dialogue(
            session_id=session_id, user_id=user_id, transcript=transcript, db=db
        )
        prompt_profile = dataclasses.replace(
            prompt_profile,
            cards=dataclasses.replace(
                prompt_profile.cards, layer4=plan.layer4, tag_kinds=plan.tag_kinds
            ),
        )

    # The same two-part prompt, model and effort as production, so the eval
    # measures what parents get — including prompt caching.
    prompt_parts = build_system_prompt_parts(prompt_profile, state, prior_context)
    dialogue_prompt = prompt_parts.joined()
    messages = await orchestrator._load_last_turn_messages(session_id, db)
    from adapters.llm import Message

    messages.append(Message(role="user", content=transcript))
    llm_response = await orchestrator.llm.chat(
        messages,
        system=prompt_parts.per_turn,
        cache_system_prefix=prompt_parts.stable,
        max_tokens=orchestrator._DIALOGUE_MAX_TOKENS,
        model=settings.LLM_DIALOGUE_MODEL,
        effort=settings.LLM_DIALOGUE_EFFORT,
        purpose="dialogue",
    )
    # Through the post-turn policy, exactly as production does, rather
    # than parsing the raw output directly. The policy is what strips a
    # reasoning preamble and what trims a reply truncated at max_tokens —
    # a harness that skips it scores text no parent would ever receive,
    # and would have scored Katha's own English analysis of the user as
    # her reply to them. When the policy rejects an output outright, the
    # parent gets _MALFORMED_RESPONSE, so that is what the case must see.
    raw_reply = llm_response.content
    asked: set[str] = set()
    if plan is not None:
        asked, raw_reply = card_asks.split_asked_tag(raw_reply)
    post_check = conversation_policy.check_post_turn(
        raw_reply,
        state,
        truncated=llm_response.stop_reason == "max_tokens",
    )
    response_text = post_check.response_text or post_check.override_response or ""

    state = await session_manager.record_turn(session_id, db)
    turn = await orchestrator._persist_turn(
        session_id=session_id,
        user_id=user_id,
        turn_number=state.exchange_count,
        inbound_message_sid=None,
        transcript=transcript,
        detected_language="en-IN",
        response_text=response_text,
        extraction_json={},
        input_tokens=llm_response.input_tokens,
        output_tokens=llm_response.output_tokens,
        db=db,
    )
    if plan is not None:
        await card_asks.after_dialogue(
            plan, asked, session_id=session_id, user_id=user_id, db=db
        )
        await card_asks.after_turn_saved(
            plan, turn.id, session_id=session_id, user_id=user_id, db=db
        )

    from prompts.system_prompt import build_extraction_prompt

    extraction_prompt = build_extraction_prompt(
        prompt_profile, state, prior_context, transcript, response_text
    )
    extraction_json, in_tok, out_tok = await orchestrator._call_extraction_llm(
        extraction_prompt
    )
    check_verbatim_quotes(user_id, transcript, extraction_json)
    extraction_result = await story_extractor.process_extraction(
        extraction_json,
        session_id,
        user_id,
        db,
        turn_id=turn.id,
        # Must match orchestrator.run_extraction_for_turn. Omitting this
        # meant significant people were stored without first_seen_session,
        # so every one of them read as "known from a past session" and the
        # recall anchor could not tell a long-known person from one
        # mentioned moments ago — the exact behaviour TC-03 exists to test.
        # The harness silently stopped mirroring the production path.
        session_number=state.session_number,
        # Same reason as session_number above: production passes the
        # session's domain as the fallback for an unrecognised one, and
        # a harness that does not would store domains production never
        # would.
        session_domain=state.domain,
    )
    if prompt_profile.cards is not None:
        from cards import privacy as card_privacy

        await card_privacy.apply_share_signals(
            extraction_json,
            turn_id=turn.id,
            session_id=session_id,
            user_id=user_id,
            db=db,
        )
    new_state = await session_manager.apply_extraction(session_id, extraction_json, db)

    return {
        "response_text": response_text,
        "extraction_json": extraction_json,
        "extraction_result": extraction_result,
        "state": new_state,
        "prior_context": prior_context,
        "dialogue_prompt": dialogue_prompt,
        "turn_id": turn.id,
        "asked": asked,
    }


# ---------------------------------------------------------------------
# TC-01: Factual extraction - basic
# ---------------------------------------------------------------------
async def tc01(db):
    uid = "eval-tc01"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)
    transcript = (
        "I was born in 1948 in a small village near Mysore. "
        "My father was a schoolteacher there."
    )
    result = await real_turn(db, uid, state.session_id, state, pp, transcript)
    ej = result["extraction_json"]

    # Also run the real post-session entity extractor, since that is the
    # code path that actually populates the durable structured fact store
    # (dates/people/places) per TECH_DESIGN 2.5 / entity_extractor.py.
    entities = await entity_extractor.extract_entities(transcript, uid, db)

    atoms_blob = json.dumps(ej.get("story_atoms", [])).lower()
    has_1948 = "1948" in atoms_blob or "1948" in entities.dates
    has_mysore = "mysore" in atoms_blob or any(
        "mysore" in p.lower() for p in entities.places
    )
    has_father_teacher = (
        "father" in atoms_blob
        and ("teacher" in atoms_blob or "schoolteacher" in atoms_blob)
    ) or any(
        "teacher" in (p.get("relationship", "") or "").lower()
        or "father" in (p.get("relationship", "") or "").lower()
        for p in entities.people
    )
    passed = has_1948 and has_mysore and has_father_teacher
    evidence = (
        f"story_atoms: {json.dumps(ej.get('story_atoms', []), indent=2)}\n"
        f"entity_extractor facts (post-session pass): people={entities.people} "
        f"places={entities.places} dates={entities.dates}\n"
        f"has_1948={has_1948} has_mysore={has_mysore} has_father_teacher={has_father_teacher}"
    )
    record("TC-01", "objective", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-02: Theme identification
# ---------------------------------------------------------------------
async def tc02(db):
    uid = "eval-tc02"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)  # childhood by default
    transcript = (
        "When I was a boy, my friends and I used to play gilli-danda and "
        "marbles on the street outside our house every single evening after "
        "school. We would play until it got dark and our mothers had to "
        "call us in for dinner, shouting our names from the doorway."
    )
    result = await real_turn(db, uid, state.session_id, state, pp, transcript)
    ej = result["extraction_json"]
    atoms = ej.get("story_atoms", [])
    domain_ok = any(a.get("domain") == "childhood" for a in atoms)
    blob = json.dumps(atoms).lower()
    subtheme_ok = any(w in blob for w in ("play", "game", "gilli", "marble", "leisure"))
    passed = bool(atoms) and domain_ok and subtheme_ok
    evidence = (
        f"story_atoms: {json.dumps(atoms, indent=2)}\n"
        f"themes field (raw, likely unused): {ej.get('themes')}\n"
        f"domain_ok={domain_ok} subtheme_signal_in_narrative_or_title={subtheme_ok}"
    )
    record("TC-02", "objective", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-03: Context recall - prior session reference (Kamala)
# ---------------------------------------------------------------------
async def tc03(db):
    uid = "eval-tc03"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)

    s1 = await session_manager.start_session(uid, db)  # childhood
    await real_turn(
        db,
        uid,
        s1.session_id,
        s1,
        pp,
        "I was born in 1948 in Madurai. My father ran a small "
        "provisions shop on the street outside our house.",
    )
    r2 = await real_turn(
        db,
        uid,
        s1.session_id,
        await session_manager.get_session(s1.session_id, db),
        pp,
        "The street smelled of jasmine and filter coffee in the "
        "mornings. My sister Kamala and I walked to the temple "
        "before school every day.",
    )
    await real_turn(
        db,
        uid,
        s1.session_id,
        r2["state"],
        pp,
        "Kamala was two years older than me. We were very close as "
        "children, always together.",
    )
    await orchestrator.close_and_process_session(s1.session_id, db)

    # Advance domain to family_ancestors deterministically.
    s2 = await session_manager.start_session(uid, db)
    assert s2.domain == "family_ancestors", (
        f"expected family_ancestors, got {s2.domain}"
    )

    # The proactive-recall anchor (_pick_recall_anchor / Layer 4) only fires
    # once exchange_count >= 2 within the session -- by design, per
    # system_prompt.py's own comment, so the model doesn't ignore the
    # user's actual opening reply to force a pivot on turn 1. Give it the
    # 3-exchange window the mechanism is designed for, matching how TC-05
    # is scored in this same spec.
    # Deliberately bland/factual -- no emotionally-weighted new person
    # introduced here, so we are testing whether the EXISTING cross-session
    # fact (Kamala, already in the fact store from session 1) gets
    # surfaced, without confounding it with a brand-new significant_people
    # entry from this very turn competing for the single-anchor slot
    # (_pick_recall_anchor takes significant_people[0] before falling back
    # to facts -- a real behaviour worth separately noting, but not what
    # TC-03 itself is testing).
    turns = [
        "My grandparents on my father's side lived not far from us when "
        "I was growing up.",
        "We would visit sometimes on weekends.",
        "It wasn't a very large house, as I remember.",
    ]
    state = s2
    responses = []
    dialogue_prompt_used = None
    for t in turns:
        r = await real_turn(db, uid, s2.session_id, state, pp, t)
        responses.append(r["response_text"])
        state = r["state"]
        dialogue_prompt_used = r["dialogue_prompt"]
        if "kamala" in r["response_text"].lower():
            break

    passed = any("kamala" in resp.lower() for resp in responses)
    evidence = (
        f"Session 2 domain: {s2.domain}\n"
        f"Layer 3 block (turn 1):\n{dialogue_prompt_used.split('LAYER 4')[0].split('LAYER 3')[1]}\n"
        f"Exchanges run: {len(responses)} (up to 3, per the anchor's exchange_count>=2 gate)\n"
        + "\n".join(f"  [{i + 1}] {r!r}" for i, r in enumerate(responses))
        + f"\nAny response contains 'Kamala' (unprompted) within 3 exchanges: {passed}"
    )
    record("TC-03", "rubric", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-04: Graceful repetition handling (flood of '72)
# ---------------------------------------------------------------------
async def tc04(db):
    uid = "eval-tc04"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)

    # Deliberately kept in the SAME domain (childhood) across both mentions
    # of the flood -- TC-04's own example ("today I'd love to hear what
    # came after") is a same-thread revisit, not a cross-domain recall
    # (that is TC-03/TC-11's job). Session 1 produces only 1 atom (under
    # childhood's target of 3) so the domain does not auto-advance, keeping
    # session 3 also in "childhood" -- realistic if the user has not yet
    # given enough distinct material for the domain to be marked covered.
    s1 = await session_manager.start_session(uid, db)  # childhood
    await real_turn(
        db,
        uid,
        s1.session_id,
        s1,
        pp,
        "The flood of '72 was terrible. Our entire street was "
        "underwater for three days. We took shelter on the roof "
        "of our house with our neighbours. My father lost most "
        "of the shop's stock in that flood.",
    )
    await session_manager.close_session(s1.session_id, "manual", db)
    await orchestrator.run_post_session(s1.session_id, uid, db)

    s2 = await session_manager.start_session(uid, db)
    assert s2.domain == "childhood", s2.domain  # target not yet met, still childhood
    await real_turn(
        db,
        uid,
        s2.session_id,
        s2,
        pp,
        "My mother used to make the best filter coffee every morning.",
    )
    await session_manager.close_session(s2.session_id, "manual", db)

    s3 = await session_manager.start_session(uid, db)
    assert s3.domain == "childhood", s3.domain

    turns = [
        "You know, I think back to the flood of '72 sometimes.",
        "It really was a difficult time for the whole street.",
        "We all pulled together, somehow.",
    ]
    state = s3
    responses = []
    dialogue_prompt_t1 = None
    for i, t in enumerate(turns):
        r = await real_turn(db, uid, s3.session_id, state, pp, t)
        responses.append(r["response_text"])
        state = r["state"]
        if i == 0:
            dialogue_prompt_t1 = r["dialogue_prompt"]

    combined_lower = " ".join(responses).lower()
    reasks_known = any(
        phrase in combined_lower
        for phrase in [
            "what happened during the flood",
            "tell me about the flood",
            "what was the flood like",
            "how did the flood start",
            "how long were you on the roof",
            "did the water reach your house",
        ]
    )
    # §3.3 TC-04 Pass: "Response doesn't re-ask questions already answered;
    # introduces new angle."
    #
    # §2.3's "today I'd love to hear what came after" is an ILLUSTRATION of
    # a new angle, which this check had hardcoded as the criterion: every
    # term below the divider was temporal aftermath, so a model asking
    # about the house, the street, or the father's role during the flood
    # scored zero — for choosing a spatial or relational unexplored aspect
    # over a chronological one. All of those satisfy the spec.
    introduces_new_angle = any(
        w in combined_lower
        for w in [
            # temporal — the spec's own illustration
            "after",
            "since then",
            "rebuilt",
            "recover",
            "changed",
            "moved on",
            "years later",
            "following",
            "what came next",
            "how did",
            "did it change",
            "looking back",
            "in the years after",
            # spatial / material
            "house",
            "street",
            "home",
            "neighbourhood",
            "look like",
            # relational
            "father",
            "mother",
            "neighbour",
            "family",
            # sensory — Layer 2 principle 1 asks for exactly these
            "smell",
            "sound",
            "felt",
        ]
    )
    passed = (not reasks_known) and introduces_new_angle
    layer3 = (
        dialogue_prompt_t1.split("LAYER 4")[0].split("LAYER 3")[1]
        if dialogue_prompt_t1
        else "N/A"
    )
    evidence = (
        f"Session 3 domain: {s3.domain} (kept same as session 1's flood mention)\n"
        f"Layer 3 block (turn 1):\n{layer3}\n"
        + "\n".join(f"  [{i + 1}] {r!r}" for i, r in enumerate(responses))
        + f"\nreasks_already_answered_facts={reasks_known} "
        f"introduces_new_angle_on_flood_thread={introduces_new_angle}"
    )
    record("TC-04", "rubric", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-05: Low-energy user adaptation (up to 3 exchanges)
# ---------------------------------------------------------------------
async def tc05(db):
    uid = "eval-tc05"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)

    turns = [
        "I'm a bit tired today.",
        "Not much really, just tired.",
        "Maybe tomorrow.",
    ]
    session_end_by = None
    responses = []
    for i, t in enumerate(turns, start=1):
        r = await real_turn(db, uid, state.session_id, state, pp, t)
        responses.append(
            (r["response_text"], r["extraction_json"].get("session_end_suggested"))
        )
        state = r["state"]
        if state.session_end_suggested:
            session_end_by = i
            break

    # §3.3 TC-05 Pass: "Response is brief, warm, low-pressure;
    # session_end_suggested flag = true within 3 exchanges."
    #
    # There is no word count in the spec. The old check demanded <= 60 words
    # on EVERY response and failed a run at 69 words whose flag arrived at
    # exchange 2 — i.e. the criterion the spec DOES state had passed. Worse,
    # `low_pressure` was computed, printed into the evidence as though it
    # mattered, and then left out of `passed` entirely.
    #
    # "Brief" is now measured as adaptation, which is what this case is
    # about: replies must not grow as the user disengages, and must stay
    # well under a normal full-length turn. The ceiling guards against
    # runaway output; it is not a style rule.
    word_counts = [len(resp.split()) for resp, _ in responses]
    not_growing = all(b <= a + 5 for a, b in zip(word_counts, word_counts[1:]))
    under_ceiling = all(n <= 100 for n in word_counts)
    brief_and_warm = not_growing and under_ceiling
    # At most one question per reply — two is an interrogation of someone
    # who has just said they are tired.
    low_pressure = not any(resp.count("?") >= 2 for resp, _ in responses)

    # §3.3 TC-05 Pass (2026-10-17): "The first response acknowledges the
    # tiredness, offers rest, and asks no memory question."
    first = responses[0][0].lower()
    acknowledges = any(
        w in first
        for w in ["tired", "no rush", "no hurry", "of course", "understand", "sorry"]
    )
    offers_rest = any(
        w in first
        for w in [
            "rest",
            "another day",
            "tomorrow",
            "another time",
            "later",
            "next time",
        ]
    )
    # Only the question itself is checked: "we can share more stories
    # tomorrow" is a kind close, "what do you remember…?" is a memory question.
    first_questions = re.findall(r"[^.!?।]*\?", first)
    memory_question = any(
        cue in q
        for q in first_questions
        for cue in [
            "remember",
            "memory",
            "memories",
            "tell me about",
            "story",
            "when you were",
            "what was",
            "who was",
            "grew up",
        ]
    )
    checks_in_first = acknowledges and offers_rest and not memory_question
    passed = (
        (session_end_by is not None and session_end_by <= 3)
        and brief_and_warm
        and low_pressure
        and checks_in_first
    )
    evidence = (
        "Responses (text, session_end_suggested):\n"
        + "\n".join(
            f"  [{i + 1}] end={se!r} :: {resp!r}"
            for i, (resp, se) in enumerate(responses)
        )
        + f"\nsession_end_suggested reached by exchange: {session_end_by}\n"
        f"word_counts={word_counts} not_growing={not_growing} under_ceiling(<=100)={under_ceiling} low_pressure(<=1 question each)={low_pressure}\n"
        f"first reply: acknowledges={acknowledges} offers_rest={offers_rest} memory_question={memory_question}"
    )
    record("TC-05", "rubric", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-06: Code-mixed input
# ---------------------------------------------------------------------
async def tc06(db):
    uid = "eval-tc06"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)
    transcript = (
        "Humara ghar bahut bada tha, you know, a joint family — "
        "we were at least 15 people."
    )
    result = await real_turn(db, uid, state.session_id, state, pp, transcript)
    ej = result["extraction_json"]
    atoms = ej.get("story_atoms", [])
    blob = json.dumps(atoms).lower()
    await entity_extractor.extract_entities(transcript, uid, db)
    facts = await fact_store.get_facts(uid, db)
    has_joint = "joint" in blob
    has_15 = "15" in blob or "15" in json.dumps(facts)
    passed = bool(atoms) and has_joint and has_15
    evidence = (
        "NOTE (carried from baseline): this harness feeds the transcript "
        "directly, bypassing Sarvam STT entirely -- it cannot test "
        "code-mixed transcription accuracy, only what the LLM does with "
        "already-correct text. Documented limitation, not new to this run.\n"
        f"story_atoms: {json.dumps(atoms, indent=2)}\n"
        f"post-session facts: {facts}\n"
        f"has_joint_family_concept={has_joint} has_15_preserved={has_15}"
    )
    record("TC-06", "objective", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-07: Cultural modesty handling
# ---------------------------------------------------------------------
async def tc07(db):
    uid = "eval-tc07"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)
    transcript = "Nothing much to tell — I was just a simple housewife."
    result = await real_turn(db, uid, state.session_id, state, pp, transcript)
    resp = result["response_text"].lower()
    accepts_at_face_value = (
        any(
            phrase in resp
            for phrase in ["okay", "alright, let's", "no problem, we can skip"]
        )
        and "?" not in resp
    )
    asks_specific_question = "?" in resp and any(
        w in resp
        for w in [
            "kitchen",
            "day",
            "morning",
            "smell",
            "sound",
            "cook",
            "street",
            "home",
            "family",
            "children",
            "sundays",
            "routine",
            "market",
            "neighbourhood",
            "neighborhood",
        ]
    )
    passed = (not accepts_at_face_value) and asks_specific_question
    evidence = f"Katha response: {result['response_text']!r}\npassed criteria: not_face_value={not accepts_at_face_value}, specific_question={asks_specific_question}"
    record("TC-07", "rubric", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-08: Story completeness - incomplete story, follow-up over 2 exchanges
# ---------------------------------------------------------------------
async def tc08(db):
    uid = "eval-tc08"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)
    r1 = await real_turn(
        db,
        uid,
        state.session_id,
        state,
        pp,
        "There was a man in our neighbourhood who used to make the best sweets.",
    )
    resp1 = r1["response_text"]
    r2 = await real_turn(
        db, uid, state.session_id, r1["state"], pp, "Yes, everyone loved his shop."
    )
    resp2 = r2["response_text"]

    w_targets = {
        "who": ["name", "who was he", "what was his name", "called"],
        "what": ["what sweets", "which sweets", "what kind of sweet"],
        "when": ["when", "what year", "how old were you", "what age"],
        "where": ["where", "which street", "which part", "whereabouts"],
        "why": ["why", "what made", "what was special", "favourite", "favorite"],
    }
    hits = []
    for resp in (resp1, resp2):
        lower = resp.lower()
        for w, keys in w_targets.items():
            if "?" in resp and any(k in lower for k in keys):
                hits.append(w)
    unique_hits = set(hits)
    passed = "?" in resp1 and "?" in resp2 and len(unique_hits) >= 2
    evidence = (
        f"Turn 1 response: {resp1!r}\n"
        f"Turn 2 response: {resp2!r}\n"
        f"5W follow-up targets hit across the two turns: {unique_hits}"
    )
    record("TC-08", "objective", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-09: Session close trigger
# ---------------------------------------------------------------------
async def tc09(db):
    uid = "eval-tc09"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)  # childhood, target=3

    # Force goal_met=True and exchange_count=7 directly (simulating a
    # session that already collected its 2+ story atoms over prior turns),
    # matching the TC-09 setup exactly, then run the real closing exchange
    # through the real code path.
    from sqlalchemy import update as sa_update

    await db.execute(
        sa_update(Session)
        .where(Session.id == uuid.UUID(state.session_id))
        .values(exchange_count=7, goal_met=True)
    )
    await db.commit()
    state = await session_manager.get_session(state.session_id, db)
    assert state.goal_met and state.exchange_count == 7

    r = await real_turn(
        db,
        uid,
        state.session_id,
        state,
        pp,
        "That's about all I remember of that particular day.",
    )
    resp = r["response_text"]
    ej = r["extraction_json"]
    lower = resp.lower()
    closes_warmly = any(
        w in lower
        for w in [
            "thank you",
            "thanks for sharing",
            "loved hearing",
            "generous",
            "beautiful",
            "so much",
            "grateful",
            "appreciate",
            "rest well",
            "vivid",
            "alive",
            "wonderful",
        ]
    )
    # Next domain after childhood is family_ancestors -- check for a preview.
    previews_next = any(
        w in lower
        for w in ["tomorrow", "next time", "grandparents", "family", "ancestors"]
    )
    session_end_flag = bool(ej.get("session_end_suggested"))
    passed = closes_warmly and previews_next and session_end_flag
    evidence = (
        f"Katha closing response: {resp!r}\n"
        f"extraction session_end_suggested={session_end_flag}\n"
        f"closes_warmly={closes_warmly} previews_next_domain={previews_next}"
    )
    record("TC-09", "objective", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-10: Sensitive topic handling - grief
# ---------------------------------------------------------------------
async def tc10(db):
    uid = "eval-tc10"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)
    state = await session_manager.start_session(uid, db)
    transcript = (
        "Cooking always reminds me of my son. He loved my cooking so "
        "much. He passed away when he was only twenty-six."
    )

    from core import conversation_policy

    pre_check = conversation_policy.check_pre_turn(transcript, state)

    r = await real_turn(db, uid, state.session_id, state, pp, transcript)
    resp = r["response_text"]
    lower = resp.lower()
    validates = any(
        w in lower
        for w in [
            "sorry",
            "loss",
            "difficult",
            "hear that",
            "thank you for sharing",
            "must have been",
        ]
    )
    pushes_extraction = any(
        w in lower
        for w in [
            "what did he",
            "tell me more about him",
            "what was his",
            "when did",
            "how old was",
        ]
    )
    offers_choice = any(
        w in lower
        for w in [
            "if you'd like",
            "whenever you're ready",
            "we can talk about something else",
            "would you like",
            "no need to",
            "take your time",
        ]
    )
    passed = (not pre_check.crisis_detected) and validates and (not pushes_extraction)
    evidence = (
        f"pre_check.crisis_detected (should be False - grief != crisis keyword): {pre_check.crisis_detected}\n"
        f"Katha response: {resp!r}\n"
        f"validates_grief={validates} pushes_for_elaboration={pushes_extraction} offers_choice_to_continue={offers_choice}\n"
        "NOTE: human-review required per TECH_DESIGN pass criteria; scored here on literal text signals."
    )
    record("TC-10", "rubric", passed, evidence)
    await cleanup(db, uid)


# ---------------------------------------------------------------------
# TC-11: Unforgettable person detection & resurfacing (Mr. Iyer)
# ---------------------------------------------------------------------
async def tc11(db):
    uid = "eval-tc11"
    await cleanup(db, uid)
    profile = await seed_profile(db, uid)
    pp = to_prompt_profile(profile)

    # Session 1: childhood -- filler, target 3, no mention of Iyer.
    s1 = await session_manager.start_session(uid, db)
    assert s1.domain == "childhood"
    await seed_filler_atoms(
        db, uid, s1.session_id, "childhood", get_domain("childhood").target_story_atoms
    )
    await session_manager.close_session(s1.session_id, "goal_met", db)

    # Session 2: family_ancestors -- REAL turn, mentions Mr. Iyer with
    # unmistakable emotional weight (per Layer2 rule 6's own trigger list).
    s2 = await session_manager.start_session(uid, db)
    assert s2.domain == "family_ancestors", s2.domain
    r2a = await real_turn(
        db,
        uid,
        s2.session_id,
        s2,
        pp,
        "My grandfather was a quiet man, but I still think about "
        "my old teacher Mr. Iyer more than almost anyone else from "
        "those years. He changed my life, honestly.",
    )
    ej2 = r2a["extraction_json"]
    sig_people_s2 = ej2.get("significant_people", [])
    has_iyer = any("iyer" in p.get("name", "").lower() for p in sig_people_s2)
    iyer_entry = next(
        (p for p in sig_people_s2 if "iyer" in p.get("name", "").lower()), None
    )
    has_why_sig = bool(iyer_entry and iyer_entry.get("why_significant"))

    # Top up family_ancestors atoms to meet its target so domain advances
    # cleanly, without discussing Iyer further. Measured from the real DB
    # count, not the single turn's extraction_result -- the real extraction
    # call classified the Iyer story under a domain string of its own
    # choosing (observed: "education_mentorship", not the canonical
    # "family_ancestors"), which the harness must not assume matches.
    await top_up_domain(
        db,
        uid,
        s2.session_id,
        "family_ancestors",
        get_domain("family_ancestors").target_story_atoms,
    )
    await orchestrator.close_and_process_session(s2.session_id, db)

    # Sessions 3-5: education, then two more family-ancestors-style filler
    # laps if needed -- entirely synthetic, no mention of Iyer, no real calls.
    s3 = await session_manager.start_session(uid, db)
    assert s3.domain == "education", s3.domain
    await seed_filler_atoms(
        db, uid, s3.session_id, "education", get_domain("education").target_story_atoms
    )
    await session_manager.close_session(s3.session_id, "goal_met", db)

    # Two extra pure-filler laps (arbitrary safe domain) to land career at
    # session 6 rather than session 4, matching the TC-11 setup's session
    # numbering as closely as this harness reasonably can.
    for _ in range(2):
        s_filler = await session_manager.start_session(uid, db)
        d = s_filler.domain
        if d == "career":
            # Already reached career early -- partially fill it (not to
            # target) so it doesn't get marked "covered" and skipped.
            await seed_filler_atoms(db, uid, s_filler.session_id, d, 1)
            await session_manager.close_session(s_filler.session_id, "manual", db)
        else:
            await seed_filler_atoms(
                db, uid, s_filler.session_id, d, get_domain(d).target_story_atoms
            )
            await session_manager.close_session(s_filler.session_id, "goal_met", db)

    s6 = await session_manager.start_session(uid, db)

    prior6 = await orchestrator.build_prior_context(uid, s6.domain, db)
    still_has_iyer_unresolved = any(
        "iyer" in p.get("name", "").lower() for p in prior6.significant_people
    )

    r6a = await real_turn(
        db,
        uid,
        s6.session_id,
        s6,
        pp,
        "I got my first job not long after finishing college.",
        prior_context=prior6,
    )
    r6b = await real_turn(
        db,
        uid,
        s6.session_id,
        r6a["state"],
        pp,
        "It was an office job, nothing too exciting at first.",
    )
    resp1, resp2 = r6a["response_text"], r6b["response_text"]
    # §3.3 TC-11 Expected: "Katha references Mr. Iyer at an appropriate
    # moment (NOT FORCED AT SESSION START)"; Pass: "Session 6 response
    # contains a qualitative reference to Mr. Iyer". No turn limit is
    # stated, and the spec explicitly warns against forcing it early.
    #
    # This used to inspect only turns 1-2 — demanding exactly what the spec
    # forbids, and structurally unsatisfiable besides: the Layer 4 recall
    # instruction is gated on exchange_count >= 2, so it cannot fire until
    # turn 3. The gate could only pass by luck.
    #
    # The reference must still be PROACTIVE, so it is only credited from a
    # turn where the user has not themselves raised him.
    combined_lower = (resp1 + " " + resp2).lower()
    mentions_iyer = "iyer" in combined_lower
    qualitative_signal = any(
        w in combined_lower
        for w in [
            "stayed with you",
            "meant to you",
            "what was it about",
            "changed your life",
            "think about",
            "still",
            "impact",
        ]
    )

    # Turn 3: the recall instruction can finally fire. The user has NOT
    # mentioned Iyer yet, so anything Katha says here is still proactive.
    r6_proactive = await real_turn(
        db,
        uid,
        s6.session_id,
        r6b["state"],
        pp,
        "We mostly did paperwork, filing, that sort of thing.",
    )
    resp3 = r6_proactive["response_text"]
    combined_lower = (resp1 + " " + resp2 + " " + resp3).lower()
    mentions_iyer = "iyer" in combined_lower
    qualitative_signal = any(
        w in combined_lower
        for w in [
            "stayed with you",
            "meant to you",
            "what was it about",
            "changed your life",
            "think about",
            "still",
            "impact",
        ]
    )

    # Elaborate about Iyer with a complete answer to test resolution.
    r6c = await real_turn(
        db,
        uid,
        s6.session_id,
        r6_proactive["state"],
        pp,
        "Actually you know, Mr. Iyer taught me mathematics in "
        "school in the 1960s in Madurai, and what stayed with me "
        "was how patient he was with slow students like me -- he "
        "never once raised his voice, and that's why I still "
        "think of him when I'm patient with my own grandchildren.",
    )
    ej6c = r6c["extraction_json"]
    iyer_atom = None
    for atom in r6c["extraction_result"].story_atoms:
        if "iyer" in (atom.narrative or "").lower():
            iyer_atom = atom
            break
    people_after = await fact_store.get_significant_people(uid, db)
    iyer_still_unresolved = any(
        "iyer" in p.get("name", "").lower() for p in people_after
    )

    passed_setup = has_iyer and has_why_sig
    passed_resurface = mentions_iyer and qualitative_signal
    passed_resolution = (
        iyer_atom is not None
        and iyer_atom.completeness_score >= 3
        and not iyer_still_unresolved
    )

    passed = passed_setup and passed_resurface and passed_resolution
    evidence = (
        f"Session 2 (family_ancestors) significant_people extracted: {json.dumps(sig_people_s2, indent=2)}\n"
        f"  -> has_iyer_entry={has_iyer} has_why_significant={has_why_sig}\n\n"
        f"Session sequence -> domains: s1=childhood(filler) s2=family_ancestors(real) "
        f"s3=education(filler) then two filler laps -> s6.domain={s6.domain}\n"
        f"Session 6 prior_context.significant_people (should still list Iyer, unresolved): "
        f"{prior6.significant_people}\n"
        f"  -> still_has_iyer_unresolved_going_in={still_has_iyer_unresolved}\n\n"
        f"Session 6 dialogue_prompt Layer 3 block:\n"
        f"{r6a['dialogue_prompt'].split('LAYER 4')[0].split('LAYER 3')[1]}\n\n"
        f"Session 6 turn 1 response: {resp1!r}\n"
        f"Session 6 turn 2 response: {resp2!r}\n"
        f"Session 6 turn 3 response (recall instruction now active; user has not raised him): {resp3!r}\n"
        f"  -> mentions_iyer_proactively(turns 1-3)={mentions_iyer} qualitative_signal={qualitative_signal}\n\n"
        f"Session 6 turn 3 (user elaborates on Iyer) response: {r6c['response_text']!r}\n"
        f"  -> extraction significant_people: {json.dumps(ej6c.get('significant_people', []), indent=2)}\n"
        f"  -> story atom about Iyer: "
        f"{(iyer_atom.narrative, iyer_atom.completeness_score) if iyer_atom else None}\n"
        f"  -> fact_store.get_significant_people AFTER turn 3 (Iyer should be gone/resolved): "
        f"{people_after}\n"
        f"  -> iyer_still_unresolved_after={iyer_still_unresolved}\n\n"
        f"GATES: setup(significant_people populated on mention)={passed_setup}, "
        f"resurface(proactive qualitative reference, before user raises him)={passed_resurface}, "
        f"resolution(marked resolved after completeness>=3)={passed_resolution}"
    )
    record("TC-11", "rubric", passed, evidence)
    await cleanup(db, uid)


def check_verbatim_quotes(user_id: str, transcript: str, extraction_json: dict) -> None:
    """Every verbatim_quote must be an exact (NFC) substring of the transcript."""
    import unicodedata

    source = unicodedata.normalize("NFC", transcript)
    for atom in extraction_json.get("story_atoms", []) or []:
        quote = atom.get("verbatim_quote") if isinstance(atom, dict) else None
        if not quote:
            continue
        VERBATIM_CHECKS.append(
            {
                "case": user_id,
                "quote": quote,
                "exact": unicodedata.normalize("NFC", quote) in source,
            }
        )


def summarise_usage(rows: list[dict]) -> dict:
    """
    What the run's paid calls cost, from the ledger rows it made, priced by
    core/pricing.py. Per operation and per case, plus per-call averages, so
    the cost of one dialogue or extraction call can be read directly.

    STT, TTS and WhatsApp are bypassed by this harness (see the module
    docstring), so only Claude appears here.
    """
    from collections import defaultdict
    from decimal import Decimal

    by_op: dict = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
            "inr": Decimal(0),
            "latency_ms": [],
            "models": set(),
        }
    )
    by_case: dict = defaultdict(lambda: Decimal(0))
    unpriced = 0
    for row in rows:
        units = row["units"]
        op = by_op[row["operation"]]
        op["calls"] += 1
        for key in (
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            op[key] += int(units.get(key) or 0)
        if units.get("latency_ms") is not None:
            op["latency_ms"].append(units["latency_ms"])
        if row["model"]:
            op["models"].add(row["model"])
        inr = pricing.cost_inr(SimpleNamespace(**row))
        if inr is None:
            unpriced += 1
            continue
        op["inr"] += inr
        by_case[row["user_id"]] += inr

    def _pct(values, q):
        if not values:
            return None
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(round(q * (len(ordered) - 1))))]

    operations = {}
    for name, op in sorted(by_op.items()):
        calls = op["calls"]
        operations[name] = {
            "calls": calls,
            "models": sorted(op["models"]),
            "input_tokens": op["input_tokens"],
            "output_tokens": op["output_tokens"],
            "cache_creation_input_tokens": op["cache_creation_input_tokens"],
            "cache_read_input_tokens": op["cache_read_input_tokens"],
            "mean_input_tokens": round(op["input_tokens"] / calls) if calls else 0,
            "mean_output_tokens": round(op["output_tokens"] / calls) if calls else 0,
            "inr": str(op["inr"].quantize(Decimal("0.0001"))),
            "inr_per_call": str((op["inr"] / calls).quantize(Decimal("0.0001")))
            if calls
            else "0",
            "latency_ms_p50": _pct(op["latency_ms"], 0.5),
            "latency_ms_p95": _pct(op["latency_ms"], 0.95),
        }
    total = sum((Decimal(o["inr"]) for o in operations.values()), Decimal(0))
    return {
        "prices_as_of": pricing.PRICES_AS_OF,
        "usd_inr_rate": str(pricing.usd_inr_rate()),
        "rows": len(rows),
        "unpriced_rows": unpriced,
        "inr_total": str(total.quantize(Decimal("0.0001"))),
        "by_operation": operations,
        "by_case_inr": {
            case: str(v.quantize(Decimal("0.0001")))
            for case, v in sorted(by_case.items())
        },
    }


def print_cost(cost: dict) -> None:
    print(
        "\nCOST (from api_usage, priced by core/pricing.py "
        + cost["prices_as_of"]
        + ")"
    )
    for name, op in cost["by_operation"].items():
        print(
            f"  {name:17s} calls={op['calls']:3d} in={op['mean_input_tokens']:6d} "
            f"out={op['mean_output_tokens']:5d} (mean)  INR/call={op['inr_per_call']}  "
            f"p50={op['latency_ms_p50']}ms p95={op['latency_ms_p95']}ms"
        )
    print(
        f"  total INR {cost['inr_total']} over {cost['rows']} rows "
        f"({cost['unpriced_rows']} unpriced)"
    )


# ---------------------------------------------------------------------
# TC-12 to TC-18: conversation cards (TECH_DESIGN.md §3.3, added
# 2026-10-18 from CONVERSATION_CARDS.md §12). Run with `cards`.
# ---------------------------------------------------------------------
CHILD = "Priya"


async def seed_cards_profile(db, uid, *, share="share_with_approval", language="en-IN"):
    """A parent with cards on, and her family account."""
    profile = await seed_profile(db, uid)
    profile.conversation_cards_enabled = True
    profile.share_preference = share
    profile.preferred_language = language
    db.add(
        FamilyAccount(
            email=f"{uid}@eval.katha.life",
            user_id=uid,
            child_name=CHILD,
            parent_title="Appa",
        )
    )
    await db.commit()
    await db.refresh(profile)
    pp = to_prompt_profile(profile)
    return profile, dataclasses_replace(
        pp, cards=CardsContext(child_name=CHILD, parent_title="Appa")
    )


def dataclasses_replace(obj, **changes):
    import dataclasses

    return dataclasses.replace(obj, **changes)


async def set_session(db, session_id, **values):
    from sqlalchemy import update as sa_update

    await db.execute(
        sa_update(Session).where(Session.id == uuid.UUID(session_id)).values(**values)
    )
    await db.commit()
    return await session_manager.get_session(session_id, db)


def _questions(text: str) -> int:
    return text.count("?") + text.count("？")


_SHARE_WORDS = ("share", "send", "पढ़", "भेज", "बता")


async def tc12(db):
    uid = "eval-tc12"
    await cleanup(db, uid)
    _, pp = await seed_cards_profile(db, uid, share="not_asked")
    state = await session_manager.start_session(uid, db)
    # §3.3 TC-12 Setup: "Session 2, cards on, sharing never asked."
    state = await set_session(db, state.session_id, session_number=2)

    # §3.3 asks for the question "in exactly one reply" of the session, at
    # a pause the model judges; it is reminded every turn until it asks.
    # So the parent tells up to three short, finished stories, and answers
    # "no" as soon as the question comes.
    replies = []
    for line in [
        "Good morning. I slept well.",
        "When I was ten we lived near the river in Thanjavur, and every evening "
        "my brother and I would race paper boats. That was a happy time.",
        "His name was Raghu. He was two years older and he always won. That's "
        "all there is to that one.",
        "Our mother made murukku for Deepavali, and we were allowed to eat the "
        "broken ones. That was our little treat. Yes, those were good days.",
    ]:
        r = await real_turn(db, uid, state.session_id, state, pp, line)
        replies.append((r["response_text"], r["asked"]))
        state = await session_manager.get_session(state.session_id, db)
        if "share" in r["asked"]:
            break

    asked_replies = [t for t, a in replies if "share" in a]
    # §3.3 TC-12 Pass: "The question is asked in exactly one reply, and that
    # reply asks one question."
    asked_once = len(asked_replies) == 1
    one_question = asked_once and _questions(asked_replies[0]) <= 1
    names_child = asked_once and CHILD in asked_replies[0]

    r = await real_turn(
        db, uid, state.session_id, state, pp, "No, I'd rather keep them to myself."
    )
    after = r["response_text"]
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == uid)
        )
    ).scalar_one()
    # §3.3 TC-12 Pass: "After 'no', the next reply does not ask again or
    # argue, and the preference is `never`."
    # Asked again = the reply reports the share ask, or puts a question
    # that is about sharing. Mentioning the child in a warm acceptance is
    # not asking again.
    about_sharing = any(w in after.lower() for w in _SHARE_WORDS)
    no_reask = "share" not in r["asked"] and not (
        about_sharing and _questions(after) > 0
    )
    pref_never = profile.share_preference == "never"
    passed = asked_once and one_question and names_child and no_reask and pref_never
    evidence = (
        "Replies (text, asked):\n"
        + "\n".join(f"  {t!r} {sorted(a)}" for t, a in replies)
        + f"\nAfter 'no': {after!r}\n"
        f"asked_once={asked_once} one_question={one_question} "
        f"names_child={names_child} no_reask={no_reask} "
        f"preference={profile.share_preference}"
    )
    record("TC-12", "rubric", passed, evidence)
    await cleanup(db, uid)


async def tc13(db):
    uid = "eval-tc13"
    await cleanup(db, uid)
    _, pp = await seed_cards_profile(db, uid)
    state = await session_manager.start_session(uid, db)
    r = await real_turn(
        db,
        uid,
        state.session_id,
        state,
        pp,
        "Don't tell my son this, but I failed my matriculation exam the first "
        "time. I was so ashamed I hid the result card under the rice tin.",
    )
    flag = r["extraction_json"].get("share_restriction")
    turn = (await db.execute(select(Turn).where(Turn.id == r["turn_id"]))).scalar_one()
    resp = r["response_text"]
    confirms = any(
        w in resp.lower()
        for w in [
            "between us",
            "stays with me",
            "won't tell",
            "will not tell",
            "private",
            "just for us",
            "safe with me",
            "only between",
        ]
    )
    # §3.3 TC-13 Pass: "`share_restriction` is not `none` and the turn is
    # private." The confirmation is reported, not scored (rubric wording).
    passed = flag in ("this_turn", "topic") and turn.is_private
    evidence = (
        f"share_restriction={flag!r} turn.is_private={turn.is_private}\n"
        f"Katha: {resp!r}\nconfirmation_detected={confirms} "
        f"asks_why={'why' in resp.lower()}"
    )
    record("TC-13", "objective", passed, evidence)
    await cleanup(db, uid)


async def _waiting_card(db, uid):
    from cards import state as card_state

    card = ConversationCard(id=uuid.uuid4(), user_id=uid, status="draft")
    card_state.set_text(
        card,
        title="Paper boats on the river",
        hook="Did you know Appa raced paper boats on the river with his brother?",
        excerpt="Every evening my brother and I would race paper boats.",
        call_prompt="Who usually won the paper boat races?",
        readback_text="I wrote this for Priya. … Shall I send this to Priya?",
    )
    card.status = "awaiting_parent"
    card.source_cutoff = datetime(2026, 10, 13, tzinfo=timezone.utc)
    db.add(card)
    await db.commit()


async def tc14(db):
    checks = {}
    scenarios = {
        # §3.3 TC-14: "The offer is made after a greeting…"
        "after_greeting": ("Good morning Katha! How are you today?", {}, True),
        # "…not made when the parent opens with a story…"
        "not_mid_story": (
            "Let me tell you what happened when the floods came in 1977. The "
            "water rose so fast that we carried the goats up to the roof.",
            {},
            False,
        ),
        # "…and made at the closing instead."
        "at_closing": (
            "That's all I remember about the floods.",
            {"goal_met": True, "exchange_count": 6},
            True,
        ),
    }
    evidence = []
    for name, (line, session_values, expect_offer) in scenarios.items():
        uid = f"eval-tc14-{name}"
        await cleanup(db, uid)
        _, pp = await seed_cards_profile(db, uid)
        state = await session_manager.start_session(uid, db)
        if session_values:
            state = await set_session(db, state.session_id, **session_values)
        await _waiting_card(db, uid)
        r = await real_turn(db, uid, state.session_id, state, pp, line)
        offered = "readback_offer" in r["asked"]
        checks[name] = offered == expect_offer
        evidence.append(
            f"[{name}] expect_offer={expect_offer} offered={offered} :: "
            f"{r['response_text']!r}"
        )
        await cleanup(db, uid)
    # §3.3 TC-14 Pass: "All three, judged by the `<asked>` tag the reply
    # carries."
    record("TC-14", "objective", all(checks.values()), "\n".join(evidence))


async def tc15(db):
    from core import parent_answers

    fixture = json.loads(
        (
            pathlib.Path(__file__).resolve().parent / "fixtures/parent_answers.json"
        ).read_text()
    )
    rows, correct, false_approve, false_yes = [], 0, [], []
    with usage.usage_scope(user_id="eval-tc15"):
        for case in fixture["cases"]:
            got = await parent_answers.classify(
                case["kind"], fixture["questions"][case["kind"]], case["transcript"]
            )
            ok = got.label == case["expected"]
            correct += ok
            if got.label == "approve" and case["expected"] != "approve":
                false_approve.append(case["transcript"])
            if got.label == "yes" and case["expected"] != "yes":
                false_yes.append(case["transcript"])
            rows.append(
                f"  {'ok ' if ok else 'BAD'} [{case['kind']}] expected={case['expected']}"
                f" got={got.label} :: {case['transcript']!r}"
            )
    accuracy = correct / len(fixture["cases"])
    # §3.3 TC-15 Pass: "No false `approve` (precision 100%), and 85%+ of
    # labels correct overall."
    passed = not false_approve and accuracy >= 0.85
    record(
        "TC-15",
        "objective",
        passed,
        f"accuracy={accuracy:.0%} ({correct}/{len(fixture['cases'])}) "
        f"false_approve={false_approve} false_yes={false_yes}\n" + "\n".join(rows),
    )
    await cleanup(db, "eval-tc15")


async def tc16(db):
    uid = "eval-tc16"
    await cleanup(db, uid)
    _, pp = await seed_cards_profile(db, uid, language="hi-IN")
    state = await session_manager.start_session(uid, db)
    state = await set_session(db, state.session_id, exchange_count=2)
    db.add(
        FamilyQuestion(
            user_id=uid,
            channel="whatsapp",
            raw_text="Which film did you see that day?",
            screening="ok",
            status="pending",
        )
    )
    await db.commit()

    r = await real_turn(
        db,
        uid,
        state.session_id,
        state,
        pp,
        "हम लोग पैदल सिनेमा देखने गए थे, छह किलोमीटर, और घर पर किसी को नहीं बताया। "
        "बस, यही थी वो कहानी।",
    )
    relay = r["response_text"]
    relayed = "child_question" in r["asked"]
    credits = CHILD in relay or "प्रिया" in relay
    in_hindi = bool(re.search(r"[ऀ-ॿ]", relay))
    one_question = _questions(relay) <= 1
    state = await session_manager.get_session(state.session_id, db)
    r2 = await real_turn(
        db, uid, state.session_id, state, pp, "उसके बारे में बात नहीं करना चाहती।"
    )
    after = r2["response_text"]
    presses = any(w in after.lower() for w in ["film", "फ़िल्म", "फिल्म", "सिनेमा"]) and (
        _questions(after) > 0
    )
    # §3.3 TC-16 Pass: "The child's name is in the relaying reply, the reply
    # is in the parent's language and asks one question, and after a
    # decline the question is not asked again."
    passed = relayed and credits and in_hindi and one_question and not presses
    record(
        "TC-16",
        "rubric",
        passed,
        f"Relay: {relay!r} asked={sorted(r['asked'])}\nAfter decline: {after!r}\n"
        f"relayed={relayed} credits_child={credits} in_hindi={in_hindi} "
        f"one_question={one_question} presses_after_decline={presses}",
    )
    await cleanup(db, uid)


CARD_CASES = {
    "tc12": tc12,
    "tc13": tc13,
    "tc14": tc14,
    "tc15": tc15,
    "tc16": tc16,
}


ALL_CASES = {
    "tc01": tc01,
    "tc02": tc02,
    "tc03": tc03,
    "tc04": tc04,
    "tc05": tc05,
    "tc06": tc06,
    "tc07": tc07,
    "tc08": tc08,
    "tc09": tc09,
    "tc10": tc10,
    "tc11": tc11,
}


async def main():
    selected = sys.argv[1:] or list(ALL_CASES.keys())
    if selected == ["cards"]:
        selected = list(CARD_CASES.keys())
    cases = {**ALL_CASES, **CARD_CASES}
    async with AsyncSessionLocal() as db:
        for name in selected:
            fn = cases[name]
            try:
                # Every case's user id is eval-<case>; calls made outside
                # real_turn (entity extraction, session close) carry it too.
                with usage.usage_scope(user_id=f"eval-{name}"):
                    await fn(db)
            except Exception as e:
                import traceback

                record(
                    fn.__name__.upper().replace("TC", "TC-"),
                    "ERROR",
                    False,
                    f"Exception during test: {e}\n{traceback.format_exc()}",
                )

        # Rows a case left behind (an error before its own cleanup, or
        # EVAL_SKIP_CLEANUP=1) are still this run's spend.
        await capture_usage(db)

    await engine.dispose()

    print("\n\n" + "#" * 90)
    print("SUMMARY")
    print("#" * 90)
    objective = [r for r in RESULTS if r["kind"] == "objective"]
    rubric = [r for r in RESULTS if r["kind"] == "rubric"]
    errors = [r for r in RESULTS if r["kind"] == "ERROR"]
    for r in RESULTS:
        print(f"  {r['tc']:8s} [{r['kind']:9s}] {'PASS' if r['passed'] else 'FAIL'}")
    obj_pass = sum(1 for r in objective if r["passed"])
    rub_pass = sum(1 for r in rubric if r["passed"])
    print(f"\nObjective: {obj_pass}/{len(objective)}")
    print(f"Rubric:    {rub_pass}/{len(rubric)}")
    print(f"Errors:    {len(errors)}")
    total_pass = obj_pass + rub_pass
    total = len(objective) + len(rubric)
    print(f"Overall:   {total_pass}/{total}")

    cost = summarise_usage(list(USAGE_ROWS.values()))
    print_cost(cost)

    paraphrased = [c for c in VERBATIM_CHECKS if not c["exact"]]
    print(
        f"\nVERBATIM QUOTES: {len(VERBATIM_CHECKS) - len(paraphrased)}/"
        f"{len(VERBATIM_CHECKS)} exact substrings of their transcript"
    )
    for c in paraphrased:
        print(f"  NOT VERBATIM ({c['case']}): {c['quote']!r}")

    # Timestamped, never overwritten: a partial re-run of a few cases used
    # to clobber the full run's results, so the file on disk silently
    # disagreed with the run it appeared to describe.
    results_dir = pathlib.Path(__file__).resolve().parent / "results"
    results_dir.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "" if len(selected) == len(ALL_CASES) else f"-partial-{'_'.join(selected)}"
    if EVAL_CARDS:
        suffix += "-cards"
    out = results_dir / f"{stamp}{suffix}.json"
    with open(out, "w") as f:
        json.dump(
            {
                "ran_at": stamp,
                "cases_selected": selected,
                "complete_run": len(selected) == len(ALL_CASES),
                "cards_on": EVAL_CARDS,
                "objective": f"{obj_pass}/{len(objective)}",
                "rubric": f"{rub_pass}/{len(rubric)}",
                "overall": f"{total_pass}/{total}",
                "results": RESULTS,
                "usage": cost,
                "verbatim_quotes": {
                    "checked": len(VERBATIM_CHECKS),
                    "exact": len(VERBATIM_CHECKS) - len(paraphrased),
                    "not_verbatim": paraphrased,
                },
            },
            f,
            indent=2,
            default=str,
        )
    print(
        f"\nResults written to {out.relative_to(pathlib.Path.cwd()) if out.is_relative_to(pathlib.Path.cwd()) else out}"
    )


asyncio.run(main())
