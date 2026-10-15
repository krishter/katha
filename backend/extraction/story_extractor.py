from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from memory import fact_store
from models.story_atom import StoryAtom
from prompts.domains import (
    get_domain,
    get_domain_sequence,
    is_known_domain,
)

logger = logging.getLogger(__name__)


@dataclass
class ExtractionResult:
    story_atoms: list[StoryAtom]
    significant_people_detected: list[dict] = field(default_factory=list)
    resolved_people: list[str] = field(default_factory=list)


_PARENTHETICAL_RE = re.compile(r"\s*\([^)]*\)")


def _normalise_person_name(name: str) -> str:
    """
    Lowercase, and drop a trailing parenthetical qualifier.

    The extractor routinely returns names like "Grandfather (unnamed)" or
    "Mr. Iyer (school teacher)". Matching those literally against narrative
    text never succeeds, which used to be the *only* reason anyone survived
    long enough to be resurfaced — survival by failed string match rather
    than by design.
    """
    return _PARENTHETICAL_RE.sub("", name or "").strip().lower()


def _atom_elaborates_on(atoms: list[StoryAtom], name: str) -> bool:
    """
    True if one of these atoms is a substantially complete story about this
    person — the signal that they have been explored rather than merely
    mentioned. `who` is the structured field for exactly this and is
    checked first; the narrative is a fallback for when the extractor put
    the person in the prose but not in `who`.
    """
    for atom in atoms:
        if atom.completeness_score < 3:
            continue
        who = [_normalise_person_name(w) for w in (atom.who or [])]
        if any(name == w or name in w or w in name for w in who if w):
            return True
        if name in (atom.narrative or "").lower():
            return True
    return False


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


def coerce_domain(raw_domain: object, session_domain: Optional[str]) -> str:
    """
    Settle on a domain id the rest of the system can actually look up.

    What the model emits used to be stored verbatim, with "unknown" as the
    default when it emitted nothing at all. Neither is in the registry, and
    one such row returned a 500 for the whole of GET /family/stories.
    Display is now defensive (prompts.domains.domain_label), but storing a
    value nothing recognises is still wrong at the source: it breaks the
    domain filter, the coverage count, and the next session's choice of
    topic, all of which match on the id.

    Tries, in order: the id as given; a loosened match on the id or on the
    domain's display name; then the session's own domain, which is
    canonical by construction because start_session picked it from the
    registry. Falling back to the session is a better guess than a
    placeholder — an atom extracted during a childhood session is far more
    likely to be about childhood than about nothing.
    """
    candidate = raw_domain if isinstance(raw_domain, str) else ""
    candidate = candidate.strip()

    if is_known_domain(candidate):
        return candidate

    if candidate:
        loose = candidate.lower().replace(" ", "_").replace("-", "_")
        if is_known_domain(loose):
            return loose
        for domain_id in get_domain_sequence():
            domain = get_domain(domain_id)
            if loose == domain_id or loose == domain.name.lower().replace(" ", "_"):
                return domain_id

    if session_domain and is_known_domain(session_domain):
        logger.warning(
            "Extraction returned domain %r, which is not a known id — "
            "storing the session's own domain %r instead",
            raw_domain,
            session_domain,
        )
        return session_domain

    logger.warning(
        "Extraction returned domain %r and the session's domain is also "
        "unusable — storing it as-is",
        raw_domain,
    )
    return candidate or "unknown"


async def process_extraction(
    extraction_json: dict,
    session_id: str,
    user_id: str,
    db: AsyncSession,
    turn_id: Optional[uuid.UUID] = None,
    session_number: Optional[int] = None,
    session_domain: Optional[str] = None,
) -> ExtractionResult:
    """
    1. Skip entirely if atoms already exist for this turn_id (idempotency —
       guards against the same turn being processed twice)
    2. Parse extraction_json['story_atoms'] → list[StoryAtom]
    3. Compute completeness_score for each atom
    4. Insert all story atoms to DB
    5. Parse significant_people and upsert to fact store
    6. Mark resolved only for people already on file BEFORE this pass, once
       an atom here elaborates on them (>= 3 completeness). Someone first
       flagged in this same pass is never resolved by it — they have not
       been resurfaced yet, which is the whole point of tracking them.

    `session_number` stamps first-seen provenance on new significant
    people so the Layer 4 recall anchor can tell a long-known person from
    one mentioned thirty seconds ago. Optional so existing callers and
    tests keep working; when absent, provenance is simply not recorded.
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
        if not isinstance(raw, dict):
            logger.warning(
                "Skipping malformed story atom for turn %s (expected object, got %s)",
                turn_id,
                type(raw).__name__,
            )
            continue
        score = compute_completeness(raw)
        atom = StoryAtom(
            session_id=session_uuid,
            turn_id=turn_id,
            user_id=user_id,
            domain=coerce_domain(raw.get("domain"), session_domain),
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

    # Process significant people.
    #
    # Who was already on file BEFORE this pass upserts anyone. This is the
    # difference between "we just met this person" and "we have known about
    # them and now the user has finally elaborated".
    known_before = {
        _normalise_person_name(p.get("name", ""))
        for p in await fact_store.get_significant_people(user_id, db)
    }
    known_before.discard("")

    resolved: list[str] = []
    for person in significant_people:
        await fact_store.upsert_significant_person(
            user_id, person, db, session_number=session_number
        )

        # Resolution retires someone from future Layer 3 injection, so it
        # must only happen once they have actually been resurfaced and
        # explored. Previously this scanned created_atoms with no such
        # guard, so a person flagged and written about in the same
        # extraction was retired before Katha had ever raised them —
        # TECH_DESIGN 3.3 TC-11 specifies session 2 flags and session 6
        # resolves. Nobody was ever resurfaced.
        name = _normalise_person_name(person.get("name", ""))
        if not name or name not in known_before:
            continue

        if _atom_elaborates_on(created_atoms, name):
            await fact_store.mark_resolved(user_id, person.get("name", ""), db)
            resolved.append(person.get("name", ""))

    return ExtractionResult(
        story_atoms=created_atoms,
        significant_people_detected=significant_people,
        resolved_people=resolved,
    )
