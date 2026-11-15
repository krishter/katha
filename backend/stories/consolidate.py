"""
The consolidation pass after a session (docs/specs/STORIES.md §2.1).

Groups what she told today by incident, carrying her open threads forward
so an incident begun last week can be finished today. A thread whose arc is
complete, and whose draft survives the fidelity check, becomes a Story with
a read-back in her language, waiting for the operator (D-a).

Private turns are never sent to the model, and nothing here reaches the
family: the furthest a Story gets is "waiting for the operator". Runs off
the critical path after a session. Logs and never raises.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import false, select
from sqlalchemy.ext.asyncio import AsyncSession

import stories
from adapters import llm
from adapters.llm import Message
from cards.context import family_of
from config import settings
from consolidation.pipeline import (
    STEP_GROUP,
    Thread,
    consolidate_session,
    parse_json_object,
)
from memory import fact_store
from models.session import Session
from models.story import Story
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from stories import readback, store

logger = logging.getLogger(__name__)


@dataclass
class Outcome:
    """What happened, for logs, tests and the backfill script."""

    result: str
    incidents: int = 0
    new_threads: int = 0
    promoted: list[uuid.UUID] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


def _model_for(step: str) -> str:
    return (
        settings.LLM_STORY_GROUP_MODEL
        if step == STEP_GROUP
        else settings.LLM_STORY_MODEL
    )


async def _call_llm(prompt: str, *, step: str, max_tokens: int):
    """The pipeline's injection point. Each step is its own api_usage
    operation, so the cost of stories shows up by step (§2.1)."""
    response = await llm.chat(
        [Message(role="user", content=prompt)],
        max_tokens=max_tokens,
        model=_model_for(step),
        purpose=f"story_{step}",
    )
    if getattr(response, "stop_reason", None) == "max_tokens":
        logger.warning("story_%s reply was cut off at %d tokens", step, max_tokens)
    return response.content, response.input_tokens, response.output_tokens


async def _ask(prompt: str, step: str, max_tokens: int) -> dict:
    text, _, _ = await _call_llm(prompt, step=step, max_tokens=max_tokens)
    return parse_json_object(text)


def _turn_dict(turn: Turn, session_number: int) -> dict:
    return {
        "id": str(turn.id),
        "session_number": session_number,
        "turn_number": turn.turn_number,
        "transcript": turn.transcript,
        "response_text": turn.response_text,
        "detected_language": turn.detected_language,
    }


def _readable():
    """Turns the pass may read: not private, and with privacy settled. A turn
    whose extraction has not finished may yet be marked private (P5)."""
    return (Turn.is_private.is_(False)) & (Turn.extraction_status == "done")


async def _session_material(session: Session, db: AsyncSession):
    turns = (
        (
            await db.execute(
                select(Turn)
                .where(Turn.session_id == session.id)
                .where(_readable())
                .order_by(Turn.turn_number)
            )
        )
        .scalars()
        .all()
    )
    readable = {t.id for t in turns}
    atoms = (
        (
            await db.execute(
                select(StoryAtom)
                .where(StoryAtom.session_id == session.id)
                .where(StoryAtom.turn_id.in_(readable) if readable else false())
                .order_by(StoryAtom.created_at)
            )
        )
        .scalars()
        .all()
    )
    return [_turn_dict(t, session.session_number) for t in turns], [
        {
            "id": str(a.id),
            "turn_id": str(a.turn_id) if a.turn_id else None,
            "domain": a.domain,
            "narrative": a.narrative,
            "who": list(a.who or []),
            "when_approx": a.when_approx,
            "where_approx": a.where_approx,
            "completeness_score": a.completeness_score,
        }
        for a in atoms
    ]


async def _history(turn_ids: set[str], db: AsyncSession) -> list[dict]:
    """The earlier turns her open threads were told in, so a thread finished
    today is composed from every telling (proposal §6.2)."""
    wanted = [i for i in (store.parse_id(t) for t in turn_ids) if i]
    if not wanted:
        return []
    rows = await db.execute(
        select(Turn, Session.session_number)
        .join(Session, Session.id == Turn.session_id)
        .where(Turn.id.in_(wanted))
        .where(_readable())
        .order_by(Session.session_number, Turn.turn_number)
    )
    return [_turn_dict(turn, number) for turn, number in rows.all()]


async def consolidate_for_session(
    session_id: str, user_id: str, db: AsyncSession, *, backfill: bool = False
) -> Outcome:
    """`backfill` runs the pass for a family before their switch is on
    (scripts/backfill_stories.py, D-c). Nothing it makes is offered to her
    until the switch is on."""
    try:
        return await _consolidate(session_id, user_id, db, backfill=backfill)
    except Exception:
        logger.exception("Story consolidation failed for session %s", session_id)
        try:
            await db.rollback()
        except Exception:
            pass
        return Outcome("error")


async def _consolidate(
    session_id: str, user_id: str, db: AsyncSession, *, backfill: bool
) -> Outcome:
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None or not (backfill or stories.enabled_for(profile)):
        return Outcome("off")

    session = await db.get(Session, uuid.UUID(str(session_id)))
    if session is None or session.user_id != user_id:
        return Outcome("no_session")

    # Both close paths can reach a session's outputs; a second pass over the
    # same session would make its incidents twice.
    already = (
        await db.execute(
            select(Story.id)
            .where(Story.user_id == user_id)
            .where(Story.sessions_seen.contains([session.session_number]))
            .limit(1)
        )
    ).scalar_one_or_none()
    if already is not None:
        return Outcome("already_done")

    turns, atoms = await _session_material(session, db)
    if not turns:
        return Outcome("nothing_readable")

    rows = await store.open_threads(user_id, db)
    by_id = {str(r.id): r for r in rows}
    atom_ids = await store.atom_ids_by_story([r.id for r in rows], db)
    threads: dict[str, Thread] = {
        str(r.id): store.to_thread(r, atom_ids.get(r.id, [])) for r in rows
    }
    earlier = {t for th in threads.values() for t in th.arc.source_turn_ids}
    history = await _history(earlier, db)
    today = {t["id"] for t in turns}
    history = [t for t in history if t["id"] not in today] + turns

    result = await consolidate_session(
        session_id=str(session.id),
        session_number=session.session_number,
        turns=turns,
        atoms=atoms,
        threads=threads,
        significant_people=await fact_store.get_significant_people(user_id, db),
        call_llm=_call_llm,
        history_turns=history,
        private_topics=list(profile.private_topics or []),
    )
    outcome = Outcome(
        "done", incidents=result.incidents_found, errors=list(result.errors)
    )

    offered = {a["id"] for a in atoms}
    promoted: list[tuple[Story, Thread]] = []
    for thread in threads.values():
        if thread.last_seen_session != session.session_number:
            continue
        row = by_id.get(thread.id)
        if row is None:
            row = Story(id=uuid.uuid4(), user_id=user_id, status="thread")
            db.add(row)
            outcome.new_threads += 1
        was_thread = row.status == "thread"
        store.apply_thread(row, thread)
        if was_thread and thread.status == "story":
            store.promote(row, thread, operator_review=settings.STORIES_OPERATOR_REVIEW)
            promoted.append((row, thread))
        await db.flush()
        await store.assign_atoms(
            row.id, thread.atom_ids, user_id=user_id, allowed=offered, db=db
        )
    await db.commit()

    for row, thread in promoted:
        await write_readback_for(row, user_id, profile, db)
        outcome.promoted.append(row.id)
    logger.info(
        "Stories for session %s: %d incident(s), %d new thread(s), %d promoted",
        session_id,
        outcome.incidents,
        outcome.new_threads,
        len(outcome.promoted),
    )
    return outcome


async def write_readback_for(
    row: Story, user_id: str, profile: UserProfileModel, db: AsyncSession
) -> None:
    """Write and check her read-back, then fingerprint what she will hear.
    A failed check leaves the Story with the operator; it cannot be passed
    until a read-back passes (stories/review.py)."""
    family = await family_of(user_id, db)
    parent_title = (family.parent_title if family else None) or profile.name
    child_name = (family.child_name if family else None) or "their family"
    language = row.source_language or profile.preferred_language or "en-IN"
    await apply_readback(
        row,
        language=language,
        parent_title=parent_title,
        child_name=child_name,
        db=db,
    )


async def apply_readback(
    row: Story,
    *,
    language: str,
    parent_title: str,
    child_name: str,
    db: AsyncSession,
    ask: Optional[readback.Ask] = None,
) -> None:
    text, check = await readback.write_readback(
        title=row.title,
        body=row.body_first_person or "",
        quotes=list(row.quotes or []),
        language=language,
        parent_title=parent_title,
        child_name=child_name,
        ask=ask or _ask,
    )
    row.readback_text = text
    row.readback_language = language
    row.readback_check = check
    row.readback_hash = store.fingerprint(row)
    await db.commit()
