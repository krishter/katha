from __future__ import annotations

import asyncio
import logging
import uuid
from dataclasses import dataclass, field

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
) -> ExtractionResult:
    """
    1. Parse extraction_json['story_atoms'] → list[StoryAtom]
    2. Compute completeness_score for each atom
    3. Insert all story atoms to DB
    4. Schedule embed_and_store fire-and-forget for each atom
    5. Parse significant_people and upsert to fact store
    6. Mark resolved if a story atom about this person scores >= 3
    """
    raw_atoms = extraction_json.get("story_atoms", [])
    significant_people = extraction_json.get("significant_people", [])

    session_uuid = uuid.UUID(session_id)
    created_atoms: list[StoryAtom] = []

    for raw in raw_atoms:
        score = compute_completeness(raw)
        atom = StoryAtom(
            session_id=session_uuid,
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

        # Fire-and-forget embedding for each atom
        for atom in created_atoms:
            asyncio.create_task(
                _embed_atom_safe(atom, db),
                name=f"embed-{atom.id}",
            )

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
    """Wrapper that logs but never raises — fire-and-forget safe."""
    try:
        await vector_store.embed_and_store(atom, db)
    except Exception:
        logger.exception("Failed to embed story atom %s", atom.id)
