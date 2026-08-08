from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory import fact_store, vector_store
from models.story_atom import StoryAtom

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    story_atoms: list[StoryAtom]
    significant_people_detected: list[dict] = field(default_factory=list)
    resolved_people: list[str] = field(default_factory=list)


def compute_completeness(atom: dict) -> int:
    """Count how many of {who, what, when_approx, where_approx, why} are populated."""
    score = 0
    who = atom.get("who")
    if who and (isinstance(who, list) and len(who) > 0 or isinstance(who, str) and who):
        score += 1
    for field_name in ("what", "when_approx", "where_approx", "why"):
        if atom.get(field_name):
            score += 1
    return score


async def process_extraction(
    extraction_json: dict,
    session_id: str,
    user_id: str,
    db: AsyncSession,
    turn_id: Optional[uuid.UUID] = None,
) -> ExtractionResult:
    """
    1. Skip entirely if atoms already exist for this turn_id (idempotency —
       guards against the same turn being processed twice)
    2. Parse extraction_json['story_atoms'] → list[StoryAtom]
    3. Compute completeness_score for each atom
    4. Insert all story atoms to DB
    5. Embed each atom inline, within this same DB session
    6. Parse significant_people and upsert to fact store
    7. Mark resolved if a story atom about this person scores >= 3
    """
    if turn_id is not None:
        existing = await db.execute(
            select(StoryAtom.id).where(StoryAtom.turn_id == turn_id).limit(1)
        )
        if existing.scalar_one_or_none() is not None:
            logger.info(
                "Story atoms already exist for turn %s — skipping re-extraction",
                turn_id,
            )
            return ExtractionResult(story_atoms=[])

    raw_atoms = extraction_json.get("story_atoms", [])
    significant_people = extraction_json.get("significant_people", [])

    session_uuid = uuid.UUID(session_id)
    created_atoms: list[StoryAtom] = []

    for raw in raw_atoms:
        score = compute_completeness(raw)
        atom = StoryAtom(
            session_id=session_uuid,
            turn_id=turn_id,
            user_id=user_id,
            domain=raw.get("domain", "unknown"),
            title=raw.get("title"),
            narrative=raw.get("narrative", ""),
            who=raw.get("who") or [],
            what=raw.get("what"),
            when_approx=raw.get("when_approx"),
            where_approx=raw.get("where_approx"),
            why=raw.get("why"),
            completeness_score=score,
            verbatim_quote=raw.get("verbatim_quote"),
            open_threads=raw.get("open_threads") or [],
            audio_timestamp_start=raw.get("audio_timestamp", {}).get("start"),
            audio_timestamp_end=raw.get("audio_timestamp", {}).get("end"),
        )
        db.add(atom)
        created_atoms.append(atom)

    if created_atoms:
        await db.commit()
        # Refresh to get DB-generated ids
        for atom in created_atoms:
            await db.refresh(atom)

        # Embed inline, in this same DB session — a fire-and-forget task here
        # resumes after FastAPI tears down the request-scoped session and
        # fails with a silent use-after-close error (see C3 in the review).
        for atom in created_atoms:
            await _embed_atom_safe(atom, db)

    # Process significant people
    resolved: list[str] = []
    for person in significant_people:
        await fact_store.upsert_significant_person(user_id, person, db)

        # Check if any atom in this session is about this person and fully explored
        person_name = person.get("name", "").lower()
        for atom in created_atoms:
            narrative_lower = atom.narrative.lower()
            if person_name in narrative_lower and atom.completeness_score >= 3:
                await fact_store.mark_resolved(user_id, person.get("name", ""), db)
                resolved.append(person.get("name", ""))
                break

    return ExtractionResult(
        story_atoms=created_atoms,
        significant_people_detected=significant_people,
        resolved_people=resolved,
    )


async def _embed_atom_safe(atom: StoryAtom, db: AsyncSession) -> None:
    """
    Embed the atom, awaited inline. Never raises — a failed embedding must
    not lose the story atom that was already committed. Failures are flagged
    on the row (embedding_failed) so they are queryable, not just logged.
    """
    try:
        await vector_store.embed_and_store(atom, db)
    except Exception:
        logger.error("Failed to embed story atom %s", atom.id, exc_info=True)
        atom.embedding_failed = True
        db.add(atom)
        await db.commit()
