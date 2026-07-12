from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.fact import Fact

logger = logging.getLogger(__name__)


async def get_facts(user_id: str, db: AsyncSession) -> dict:
    """Return structured_facts dict. Empty dict if user has no record yet."""
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    if fact is None:
        return {}
    return fact.structured_facts or {}


async def update_facts(user_id: str, new_facts: dict, db: AsyncSession) -> None:
    """
    Upsert structured_facts for the user.
    Merge strategy: new_facts keys overwrite existing; existing keys not in new_facts
    are preserved.
    """
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()

    if fact is None:
        fact = Fact(user_id=user_id, structured_facts=new_facts, significant_people=[])
        db.add(fact)
    else:
        merged = dict(fact.structured_facts or {})
        merged.update(new_facts)
        fact.structured_facts = merged

    await db.commit()


async def get_significant_people(user_id: str, db: AsyncSession) -> list[dict]:
    """Return significant_people list. Excludes entries where resolved=True."""
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    if fact is None:
        return []
    people = fact.significant_people or []
    return [p for p in people if not p.get("resolved", False)]


async def upsert_significant_person(
    user_id: str, person: dict, db: AsyncSession
) -> None:
    """
    Add a new significant person, or update if the same name already exists.
    Never adds duplicates. Match by name (case-insensitive).
    """
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()

    if fact is None:
        fact = Fact(user_id=user_id, structured_facts={}, significant_people=[person])
        db.add(fact)
        await db.commit()
        return

    people = list(fact.significant_people or [])
    incoming_name = person.get("name", "").lower()

    for i, existing in enumerate(people):
        if existing.get("name", "").lower() == incoming_name:
            people[i] = {**existing, **person}
            fact.significant_people = people
            await db.commit()
            return

    people.append(person)
    fact.significant_people = people
    await db.commit()


async def mark_resolved(user_id: str, person_name: str, db: AsyncSession) -> None:
    """Set resolved=True for a significant person by name (case-insensitive)."""
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    if fact is None:
        return

    people = list(fact.significant_people or [])
    name_lower = person_name.lower()
    for i, p in enumerate(people):
        if p.get("name", "").lower() == name_lower:
            people[i] = {**p, "resolved": True}
            break

    fact.significant_people = people
    await db.commit()
