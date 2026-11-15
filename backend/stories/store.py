"""
Reading and writing stories rows (docs/specs/STORIES.md §4), and the one
filter every family-facing query goes through.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from consolidation.arc import Arc
from consolidation.pipeline import Thread
from consolidation.prompts import MAX_OPEN_THREADS
from models.story import Story
from models.story_atom import StoryAtom


def approved_stories():
    """
    A WHERE clause keeping the Stories the family may see: she approved
    them. The database also refuses an approval without a matching
    fingerprint (ck_stories_parent_approved), so this is the second of two
    locks, not the only one. Every /family/* query on stories uses it.
    """
    return (Story.status == "story") & (Story.approval_status == "approved")


def fingerprint(story: Story) -> str:
    """What she is agreeing to: this version's English body and the read-back
    she hears. Either changes after her yes and approval no longer holds."""
    joined = "\n".join(
        [str(story.version), story.body_first_person or "", story.readback_text or ""]
    )
    return hashlib.sha256(joined.encode("utf-8")).hexdigest()


async def open_threads(user_id: str, db: AsyncSession) -> list[Story]:
    """Her open threads, most recently told first, capped as the grouping
    prompt caps them (proposal §6.2)."""
    rows = await db.execute(
        select(Story)
        .where(Story.user_id == user_id)
        .where(Story.status == "thread")
        .order_by(Story.last_seen_session.desc().nulls_last(), Story.updated_at.desc())
        .limit(MAX_OPEN_THREADS)
    )
    return list(rows.scalars().all())


async def atom_ids_by_story(
    story_ids: list[uuid.UUID], db: AsyncSession
) -> dict[uuid.UUID, list[str]]:
    if not story_ids:
        return {}
    rows = await db.execute(
        select(StoryAtom.story_id, StoryAtom.id)
        .where(StoryAtom.story_id.in_(story_ids))
        .order_by(StoryAtom.created_at)
    )
    found: dict[uuid.UUID, list[str]] = {}
    for story_id, atom_id in rows.all():
        found.setdefault(story_id, []).append(str(atom_id))
    return found


def to_thread(row: Story, atom_ids: list[str]) -> Thread:
    sessions = list(row.sessions_seen or [])
    return Thread(
        id=str(row.id),
        title=row.title,
        domain=row.domain,
        when_approx=row.when_approx,
        arc=Arc.from_json(row.arc),
        atom_ids=list(atom_ids),
        people=list(row.people or []),
        places=list(row.places or []),
        first_seen_session=min(sessions) if sessions else None,
        last_seen_session=row.last_seen_session,
        sessions_seen=sessions,
        status=row.status,
        source_language=row.source_language or "",
    )


def apply_thread(row: Story, thread: Thread) -> None:
    """Copy what the pass learned about an incident onto its row."""
    row.title = thread.title or row.title
    row.domain = thread.domain or row.domain
    row.when_approx = thread.when_approx or row.when_approx
    row.arc = thread.arc.to_json()
    row.people = list(thread.people)
    row.places = list(thread.places)
    row.sessions_seen = sorted(set(thread.sessions_seen))
    row.last_seen_session = thread.last_seen_session
    row.notes = list(thread.notes)
    if thread.fidelity_status:
        row.fidelity_status = thread.fidelity_status
        row.removed_sentences = list(thread.removed_sentences)


def promote(row: Story, thread: Thread, *, operator_review: bool) -> None:
    """A thread whose draft survived the fidelity check becomes a Story,
    waiting for the operator (D-a) and then for her."""
    row.status = "story"
    row.body_first_person = thread.body_first_person
    row.quotes = list(thread.quotes)
    row.sentence_sources = list(thread.sentence_sources)
    row.source_language = thread.source_language or None
    row.review_status = "operator_review" if operator_review else "passed"
    row.approval_status = "pending"
    row.promoted_at = datetime.now(timezone.utc)


async def assign_atoms(
    story_id: uuid.UUID,
    atom_ids: list[str],
    *,
    user_id: str,
    allowed: set[str],
    db: AsyncSession,
) -> None:
    """Point this session's atoms at their incident. Only atoms this session
    offered the model (`allowed`), only hers, and only ones not already
    placed: an atom belongs to at most one incident."""
    wanted = []
    for atom_id in atom_ids:
        if atom_id not in allowed:
            continue
        try:
            wanted.append(uuid.UUID(atom_id))
        except ValueError:
            continue
    if not wanted:
        return
    await db.execute(
        update(StoryAtom)
        .where(StoryAtom.id.in_(wanted))
        .where(StoryAtom.user_id == user_id)
        .where(StoryAtom.story_id.is_(None))
        .values(story_id=story_id)
    )


def parse_id(value: str) -> Optional[uuid.UUID]:
    try:
        return uuid.UUID(value)
    except (ValueError, TypeError):
        return None
