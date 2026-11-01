from __future__ import annotations

import logging
import re

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
    user_id: str,
    person: dict,
    db: AsyncSession,
    session_number: int | None = None,
) -> None:
    """
    Add a new significant person, or update if the same name already exists.
    Never adds duplicates. Match by name (case-insensitive).

    `session_number` is stamped as `first_seen_session` on NEW entries only,
    never overwritten on update — it records when Katha first learned of
    this person, so the Layer 4 recall anchor can prefer someone known from
    a past session over someone mentioned moments ago. Entries written
    before this existed simply lack the key, which reads as "old", and that
    is correct for them. `significant_people` is JSONB, so no migration.
    """
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()

    new_entry = dict(person)
    if session_number is not None:
        new_entry.setdefault("first_seen_session", session_number)

    if fact is None:
        fact = Fact(
            user_id=user_id, structured_facts={}, significant_people=[new_entry]
        )
        db.add(fact)
        await db.commit()
        return

    people = list(fact.significant_people or [])
    incoming_name = person.get("name", "").lower()

    for i, existing in enumerate(people):
        if existing.get("name", "").lower() == incoming_name:
            # Merge incoming detail over the existing entry, but never move
            # first_seen_session — re-mentioning someone does not make them
            # newly discovered, and overwriting it would let a long-known
            # person keep losing the anchor to whoever spoke last.
            merged = {**existing, **person}
            if "first_seen_session" in existing:
                merged["first_seen_session"] = existing["first_seen_session"]
            people[i] = merged
            fact.significant_people = people
            await db.commit()
            return

    people.append(new_entry)
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


_PARENTHETICAL_RE = re.compile(r"\(([^)]*)\)")


def name_keys(name: str) -> set[str]:
    """
    Every form of a name worth matching on, lowercased.

    The extractor writes the same person several ways: "Divya" in one
    pass, "ദിവ്യ (Divya)" in the next, sometimes "ദിവ്യ" alone. Matching
    on the whole string treated those as three people, so a parent's "I
    don't know who that is" could retire one spelling and leave the other
    two to be raised again. The name outside the brackets and each name
    inside them all count.
    """
    name = (name or "").strip()
    keys = {_PARENTHETICAL_RE.sub("", name).strip().lower()}
    keys.update(m.strip().lower() for m in _PARENTHETICAL_RE.findall(name))
    return {k for k in keys if k}


def same_person(a: str, b: str) -> bool:
    return bool(name_keys(a) & name_keys(b))


async def mark_rejected(user_id: str, person_name: str, db: AsyncSession) -> None:
    """
    Record that the parent said she does not know this person, or never
    mentioned them, so Katha never raises them again.

    A parent was asked twice in one session about a "Divya" she had named
    once, two weeks earlier, in passing. She said "I never said Divya",
    Katha apologised, and then promised to ask about Divya tomorrow,
    because nothing stopped the name being injected. Rejection is stored on
    significant_people, as `rejected` alongside `resolved` so every reader
    that already skips resolved people skips this one too, and is added as
    a bare entry when the name only ever lived in the fact store's people.
    """
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    entry = {"name": person_name, "resolved": True, "rejected": True}
    if fact is None:
        db.add(Fact(user_id=user_id, structured_facts={}, significant_people=[entry]))
        await db.commit()
        return

    people = list(fact.significant_people or [])
    matched = False
    for i, p in enumerate(people):
        if same_person(p.get("name", ""), person_name):
            people[i] = {**p, "resolved": True, "rejected": True}
            matched = True
    if not matched:
        people.append(entry)

    fact.significant_people = people
    await db.commit()


async def get_rejected_names(user_id: str, db: AsyncSession) -> list[str]:
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    if fact is None:
        return []
    return [
        p.get("name", "")
        for p in fact.significant_people or []
        if p.get("rejected") and p.get("name")
    ]


def without_rejected(people: list, rejected: list[str]) -> list:
    """`people` minus anyone the parent has rejected, by any spelling."""
    if not rejected:
        return people
    return [
        p
        for p in people
        if not (
            isinstance(p, dict)
            and any(same_person(p.get("name", ""), r) for r in rejected)
        )
    ]


def threads_without_rejected(threads: list[str], rejected: list[str]) -> list[str]:
    """Open threads minus any that name someone she has rejected. A thread
    like "What happened to Divya and Neha?" would raise her as surely
    as her own entry would."""
    keys = set().union(*(name_keys(r) for r in rejected)) if rejected else set()
    if not keys:
        return threads
    return [t for t in threads if not any(k in t.lower() for k in keys)]


# What she has set aside: a person she barely remembers, a period that did
# not matter to her, something she never did. Kept in structured_facts
# under this key, and never rendered as a fact (render_facts skips it).
SET_ASIDE_KEY = "set_aside"


async def add_set_aside(user_id: str, items: list[dict], db: AsyncSession) -> None:
    """
    Record what the parent has played down, so Katha stops steering back.

    A parent said four times that her Nagpur childhood was a faint, unhappy
    time and that she barely remembered Rani; Katha kept bringing both
    back, and promised Rani for "tomorrow". Rejection only covered "I don't
    know who that is", which is the rarer signal.

    Each item is {"kind": "person" | "topic", "name": str}. A person is
    matched against everyone on file and flagged like a rejected one, so
    every filter that drops rejected people drops them too. A topic cannot
    be matched mechanically ("work" would hit "homework"), so it is only
    shown to the model, in Layer 3.
    """
    clean = [
        {
            "kind": i.get("kind") if i.get("kind") in ("person", "topic") else "topic",
            "name": str(i.get("name")).strip(),
        }
        for i in items
        if isinstance(i, dict) and str(i.get("name") or "").strip()
    ]
    if not clean:
        return
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    if fact is None:
        fact = Fact(user_id=user_id, structured_facts={}, significant_people=[])
        db.add(fact)

    facts = dict(fact.structured_facts or {})
    stored = list(facts.get(SET_ASIDE_KEY) or [])
    for item in clean:
        if not any(
            s.get("kind") == item["kind"]
            and same_person(s.get("name", ""), item["name"])
            for s in stored
        ):
            stored.append(item)
    facts[SET_ASIDE_KEY] = stored
    fact.structured_facts = facts

    people = list(fact.significant_people or [])
    for i, p in enumerate(people):
        if any(
            item["kind"] == "person" and same_person(p.get("name", ""), item["name"])
            for item in clean
        ):
            people[i] = {**p, "resolved": True, "set_aside": True}
    fact.significant_people = people
    await db.commit()


async def get_set_aside(user_id: str, db: AsyncSession) -> list[dict]:
    result = await db.execute(select(Fact).where(Fact.user_id == user_id))
    fact = result.scalar_one_or_none()
    if fact is None:
        return []
    return list((fact.structured_facts or {}).get(SET_ASIDE_KEY) or [])


async def get_dropped_names(user_id: str, db: AsyncSession) -> list[str]:
    """Everyone Katha must not raise: people she rejected, and people she
    set aside. The input to every mechanical filter (without_rejected,
    threads_without_rejected)."""
    rejected = await get_rejected_names(user_id, db)
    people = [
        s["name"] for s in await get_set_aside(user_id, db) if s.get("kind") == "person"
    ]
    return rejected + [
        p for p in people if not any(same_person(p, r) for r in rejected)
    ]
