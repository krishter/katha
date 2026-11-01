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


# ── Rejection: "I never said Divya" ───────────────────────────────────────────

from memory.fact_store import (  # noqa: E402
    get_rejected_names,
    mark_rejected,
    name_keys,
    same_person,
    threads_without_rejected,
    without_rejected,
)


def test_name_keys_covers_both_scripts():
    assert name_keys("ദിവ്യ (Divya)") == {"ദിവ്യ", "divya"}
    assert name_keys("Divya") == {"divya"}
    assert name_keys("") == set()


def test_same_person_matches_across_spellings():
    assert same_person("Divya", "ദിവ്യ (Divya)")
    assert same_person("ദിവ്യ", "ദിവ്യ (Dhivya)")
    assert not same_person("Divya", "Neha")


async def test_mark_rejected_retires_every_spelling_on_file():
    fact = _make_fact(
        significant_people=[
            {"name": "Divya", "relationship": "Tuition student"},
            {"name": "Neha", "relationship": "Former student"},
        ]
    )
    db = _make_db(fact=fact)

    await mark_rejected("user-1", "ദിവ്യ (Divya)", db)

    divya, neha = fact.significant_people
    assert divya["rejected"] and divya["resolved"]
    assert divya["relationship"] == "Tuition student"
    assert "rejected" not in neha
    db.commit.assert_called_once()


async def test_mark_rejected_records_someone_only_in_the_people_list():
    """She can reject a name that was never a significant person, only a
    fact-store person the Layer 4 anchor picked. It still has to stick."""
    fact = _make_fact(significant_people=[{"name": "Neha"}])
    db = _make_db(fact=fact)

    await mark_rejected("user-1", "Usha", db)

    assert fact.significant_people[-1] == {
        "name": "Usha",
        "resolved": True,
        "rejected": True,
    }


async def test_rejected_people_are_not_returned_as_significant():
    fact = _make_fact(significant_people=[{"name": "Divya"}])
    db = _make_db(fact=fact)
    await mark_rejected("user-1", "Divya", db)

    assert await get_significant_people("user-1", db) == []
    assert await get_rejected_names("user-1", db) == ["Divya"]


def test_without_rejected_drops_every_spelling():
    people = [
        {"name": "ദിവ്യ (Divya)", "relationship": "former student"},
        {"name": "ദിവ്യ (Dhivya)", "relationship": "unclear"},
        {"name": "നേഹ (Neha)", "relationship": "former student"},
    ]
    kept = without_rejected(people, ["Divya", "ദിവ്യ (Dhivya)"])
    assert [p["name"] for p in kept] == ["നേഹ (Neha)"]
    assert without_rejected(people, []) is people


def test_threads_naming_a_rejected_person_are_dropped():
    threads = [
        "What happened to Divya and Neha after the classes ended?",
        "How long did Saroja teach these two students?",
        "ദിവ്യയെ പിന്നീട് കണ്ടോ?",
    ]
    assert threads_without_rejected(threads, ["ദിവ്യ (Divya)"]) == [
        "How long did Saroja teach these two students?"
    ]


# ── Set aside: "I barely remember Rani", "I never worked" ─────────────────────

from memory.fact_store import (  # noqa: E402
    SET_ASIDE_KEY,
    add_set_aside,
    get_dropped_names,
    get_set_aside,
)


async def test_set_aside_stores_people_and_topics_once():
    fact = _make_fact(structured_facts={"places": ["Nagpur"]})
    db = _make_db(fact=fact)

    await add_set_aside(
        "user-1",
        [
            {"kind": "person", "name": "റാണി (Rani)"},
            {"kind": "topic", "name": "childhood years in Nagpur"},
            {"kind": "person", "name": "Rani"},  # same person, other script
            {"kind": "nonsense", "name": "a job outside the home"},
            {"kind": "topic", "name": "  "},
        ],
        db,
    )

    assert fact.structured_facts["places"] == ["Nagpur"]
    assert fact.structured_facts[SET_ASIDE_KEY] == [
        {"kind": "person", "name": "റാണി (Rani)"},
        {"kind": "topic", "name": "childhood years in Nagpur"},
        {"kind": "topic", "name": "a job outside the home"},
    ]


async def test_a_set_aside_person_is_retired_from_significant_people():
    fact = _make_fact(
        significant_people=[{"name": "Rani"}, {"name": "Uma"}],
    )
    db = _make_db(fact=fact)

    await add_set_aside("user-1", [{"kind": "person", "name": "റാണി (Rani)"}], db)

    rani, uma = fact.significant_people
    assert rani["set_aside"] and rani["resolved"]
    assert "set_aside" not in uma
    assert await get_significant_people("user-1", db) == [{"name": "Uma"}]


async def test_a_topic_never_retires_a_person():
    """'work' must not reach the name filters: it would match 'homework'."""
    fact = _make_fact(significant_people=[{"name": "Work"}])
    db = _make_db(fact=fact)

    await add_set_aside("user-1", [{"kind": "topic", "name": "work"}], db)

    assert "set_aside" not in fact.significant_people[0]
    assert await get_dropped_names("user-1", db) == []


async def test_dropped_names_cover_rejected_and_set_aside_people():
    fact = _make_fact(
        structured_facts={
            SET_ASIDE_KEY: [
                {"kind": "person", "name": "Rani"},
                {"kind": "person", "name": "Divya"},
                {"kind": "topic", "name": "Nagpur"},
            ]
        },
        significant_people=[{"name": "Divya", "rejected": True, "resolved": True}],
    )
    db = _make_db(fact=fact)

    assert await get_dropped_names("user-1", db) == ["Divya", "Rani"]
    assert [s["name"] for s in await get_set_aside("user-1", db)] == [
        "Rani",
        "Divya",
        "Nagpur",
    ]


async def test_nothing_to_set_aside_writes_nothing():
    db = _make_db(fact=None)
    await add_set_aside("user-1", [], db)
    db.commit.assert_not_called()
