from unittest.mock import AsyncMock, MagicMock

from memory.fact_store import (
    get_facts,
    get_significant_people,
    mark_resolved,
    update_facts,
    upsert_significant_person,
)


def _make_fact(structured_facts=None, significant_people=None):
    from models.fact import Fact

    f = MagicMock(spec=Fact)
    f.structured_facts = structured_facts or {}
    f.significant_people = significant_people or []
    return f


def _make_db(fact=None):
    """Return a mock AsyncSession whose execute returns the given Fact (or None)."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = fact
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()  # synchronous in SQLAlchemy
    return db


async def test_get_facts_unknown_user_returns_empty():
    db = _make_db(fact=None)
    result = await get_facts("unknown-user", db)
    assert result == {}


async def test_get_facts_returns_stored_facts():
    fact = _make_fact(structured_facts={"birth_year": 1948, "home_town": "Mysore"})
    db = _make_db(fact=fact)
    result = await get_facts("user-1", db)
    assert result["birth_year"] == 1948
    assert result["home_town"] == "Mysore"


async def test_update_facts_merges_without_dropping_existing():
    fact = _make_fact(structured_facts={"birth_year": 1948})
    db = _make_db(fact=fact)

    await update_facts("user-1", {"home_town": "Mysore"}, db)

    # The merged dict should contain both keys
    assert fact.structured_facts["birth_year"] == 1948
    assert fact.structured_facts["home_town"] == "Mysore"
    db.commit.assert_called_once()


async def test_update_facts_new_user_creates_record():
    db = _make_db(fact=None)
    await update_facts("new-user", {"key": "value"}, db)
    db.add.assert_called_once()
    db.commit.assert_called_once()


async def test_get_significant_people_excludes_resolved():
    people = [
        {"name": "Mr. Iyer", "resolved": False},
        {"name": "Kamala", "resolved": True},
        {"name": "Rajan", "resolved": False},
    ]
    fact = _make_fact(significant_people=people)
    db = _make_db(fact=fact)

    result = await get_significant_people("user-1", db)
    names = [p["name"] for p in result]
    assert "Mr. Iyer" in names
    assert "Rajan" in names
    assert "Kamala" not in names


async def test_upsert_significant_person_no_duplicate():
    person = {"name": "Mr. Iyer", "relationship": "teacher", "resolved": False}
    fact = _make_fact(significant_people=[person])
    db = _make_db(fact=fact)

    # Upsert same person again — should update, not duplicate
    await upsert_significant_person(
        "user-1",
        {"name": "Mr. Iyer", "why_significant": "Inspired teaching career"},
        db,
    )

    people = fact.significant_people
    assert len(people) == 1
    assert people[0]["why_significant"] == "Inspired teaching career"
    assert people[0]["relationship"] == "teacher"  # original field preserved


async def test_upsert_significant_person_case_insensitive():
    person = {"name": "mr. iyer", "resolved": False}
    fact = _make_fact(significant_people=[person])
    db = _make_db(fact=fact)

    await upsert_significant_person("user-1", {"name": "Mr. Iyer", "signal": "new"}, db)

    people = fact.significant_people
    assert len(people) == 1


async def test_mark_resolved_flips_flag():
    people = [
        {"name": "Mr. Iyer", "resolved": False},
        {"name": "Kamala", "resolved": False},
    ]
    fact = _make_fact(significant_people=people)
    db = _make_db(fact=fact)

    await mark_resolved("user-1", "Mr. Iyer", db)

    resolved = {p["name"]: p["resolved"] for p in fact.significant_people}
    assert resolved["Mr. Iyer"] is True
    assert resolved["Kamala"] is False
