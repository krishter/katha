import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from extraction.story_extractor import (
    ExtractionResult,
    compute_completeness,
    process_extraction,
)


def _make_db(existing_atom_for_turn=None):
    """
    existing_atom_for_turn controls the idempotency-check query's result:
    None means no prior atoms exist for the given turn_id.
    """
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = existing_atom_for_turn
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


_SESSION_ID = str(uuid.uuid4())
_USER_ID = "user-1"

_FULL_ATOM = {
    "domain": "childhood",
    "title": "Father's shop",
    "narrative": "My father had a shop in Madurai selling brass vessels.",
    "who": ["father"],
    "what": "Brass vessel shop",
    "when_approx": "circa 1955",
    "where_approx": "Madurai",
    "why": "Family livelihood",
    "verbatim_quote": "The shop smelled of oil and metal",
    "open_threads": ["name of the street"],
}

_PARTIAL_ATOM = {
    "domain": "childhood",
    "narrative": "There was a neighbour who made sweets.",
    "who": ["neighbour"],
    "what": "Made sweets",
}

_EMPTY_ATOM = {
    "domain": "childhood",
    "narrative": "Something happened.",
}


def test_compute_completeness_empty():
    assert compute_completeness(_EMPTY_ATOM) == 0


def test_compute_completeness_full():
    assert compute_completeness(_FULL_ATOM) == 5


def test_compute_completeness_partial():
    # who + what = 2
    assert compute_completeness(_PARTIAL_ATOM) == 2


async def test_process_extraction_inserts_story_atoms():
    db = _make_db()
    extraction = {"story_atoms": [_FULL_ATOM], "significant_people": []}

    result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    assert isinstance(result, ExtractionResult)
    assert len(result.story_atoms) == 1
    db.add.assert_called_once()
    db.commit.assert_called()


async def test_process_extraction_skips_malformed_non_dict_atom():
    """
    Regression guard (WS5.4 eval finding): build_extraction_prompt's
    story_atoms schema had no per-item example, and the LLM was observed
    returning plain strings instead of objects. One bad atom must not
    abort the whole turn's extraction (people/state updates shouldn't be
    held hostage by it) — the well-formed atom in the same list should
    still be inserted.
    """
    db = _make_db()
    extraction = {
        "story_atoms": ["Born in 1948 in a village near Mysore.", _FULL_ATOM],
        "significant_people": [],
    }

    result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    assert len(result.story_atoms) == 1
    assert result.story_atoms[0].title == "Father's shop"


async def test_process_extraction_sets_turn_id_on_atoms():
    db = _make_db()
    turn_id = uuid.uuid4()
    extraction = {"story_atoms": [_FULL_ATOM], "significant_people": []}

    result = await process_extraction(
        extraction, _SESSION_ID, _USER_ID, db, turn_id=turn_id
    )

    assert result.story_atoms[0].turn_id == turn_id


async def test_process_extraction_skips_if_atoms_already_exist_for_turn():
    """Idempotency guard: re-processing the same turn must not double-insert."""
    existing_atom_id = uuid.uuid4()
    db = _make_db(existing_atom_for_turn=existing_atom_id)
    turn_id = uuid.uuid4()
    extraction = {"story_atoms": [_FULL_ATOM], "significant_people": []}

    result = await process_extraction(
        extraction, _SESSION_ID, _USER_ID, db, turn_id=turn_id
    )

    assert result.story_atoms == []
    db.add.assert_not_called()


async def test_process_extraction_calls_upsert_significant_person():
    db = _make_db()
    people = [
        {"name": "Mr. Iyer", "relationship": "teacher", "why_significant": "Inspiring"}
    ]
    extraction = {"story_atoms": [], "significant_people": people}

    with (
        patch(
            "extraction.story_extractor.fact_store.upsert_significant_person",
            new=AsyncMock(),
        ) as mock_upsert,
        patch(
            "extraction.story_extractor.fact_store.mark_resolved",
            new=AsyncMock(),
        ),
    ):
        await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_upsert.assert_called_once_with(_USER_ID, people[0], db, session_number=None)


_IYER = {"name": "Mr. Iyer", "relationship": "teacher", "why_significant": "Inspiring"}
_ATOM_ABOUT_IYER = {
    "domain": "education",
    "narrative": "Mr. Iyer was the teacher who changed my life.",
    "who": ["Mr. Iyer"],
    "what": "Inspired teaching career",
    "when_approx": "1965",
    "where_approx": "Madurai school",
    "why": "Encouragement despite family pressure",
}


def _resolution_patches(already_on_file):
    """Patch the fact-store calls process_extraction makes, varying only
    who was already known before this pass."""
    return (
        patch(
            "extraction.story_extractor.fact_store.get_significant_people",
            new=AsyncMock(return_value=already_on_file),
        ),
        patch(
            "extraction.story_extractor.fact_store.upsert_significant_person",
            new=AsyncMock(),
        ),
        patch(
            "extraction.story_extractor.fact_store.mark_resolved",
            new=AsyncMock(),
        ),
    )


async def test_does_not_resolve_someone_flagged_in_the_same_pass():
    """Person introduced AND fully written about in one extraction. They
    must not be resolved.

    This test asserted the opposite until now. Resolution retires someone
    from future Layer 3 injection, so doing it in the pass that first
    flagged them retires them before Katha has ever raised them.
    TECH_DESIGN 3.3 TC-11 has session 2 flag Mr. Iyer and session 6
    resurface him; a live run found him gone from significant_people before
    session 6 ran, so nobody was ever resurfaced.
    """
    db = _make_db()
    extraction = {"story_atoms": [_ATOM_ABOUT_IYER], "significant_people": [_IYER]}
    get_people, upsert, resolve = _resolution_patches(already_on_file=[])

    with get_people, upsert, resolve as mock_resolve:
        result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_resolve.assert_not_called()
    assert result.resolved_people == []


async def test_resolves_someone_already_on_file_when_an_atom_elaborates():
    """The other half: flagged in an earlier session, and now an atom
    scoring >= 3 explores them. That is the moment they have actually been
    resurfaced, so they retire from Layer 3."""
    db = _make_db()
    extraction = {"story_atoms": [_ATOM_ABOUT_IYER], "significant_people": [_IYER]}
    get_people, upsert, resolve = _resolution_patches(
        already_on_file=[{"name": "Mr. Iyer"}]
    )

    with get_people, upsert, resolve as mock_resolve:
        result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_resolve.assert_called_once_with(_USER_ID, "Mr. Iyer", db)
    assert "Mr. Iyer" in result.resolved_people


async def test_resolution_matches_names_carrying_a_parenthetical():
    """Extracted names routinely look like "Grandfather (unnamed)". Matching
    that literally against narrative text never succeeded, which used to be
    the only reason anyone survived long enough to be resurfaced — survival
    by failed string match rather than by design."""
    db = _make_db()
    atom = {
        "domain": "family_ancestors",
        "narrative": "My grandfather ran the farm until he was eighty.",
        "who": ["grandfather"],
        "what": "Ran the family farm",
        "when_approx": "1950s",
        "where_approx": "near Mysore",
        "why": "Anchor of the family",
    }
    people = [{"name": "Grandfather (unnamed)", "relationship": "grandfather"}]
    extraction = {"story_atoms": [atom], "significant_people": people}
    get_people, upsert, resolve = _resolution_patches(
        already_on_file=[{"name": "Grandfather (unnamed)"}]
    )

    with get_people, upsert, resolve as mock_resolve:
        await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_resolve.assert_called_once_with(_USER_ID, "Grandfather (unnamed)", db)


async def test_does_not_resolve_when_the_atom_is_incomplete():
    """A known person merely mentioned again, without a substantially
    complete story, stays open."""
    db = _make_db()
    thin_atom = {
        "domain": "education",
        "narrative": "Mr. Iyer taught me.",
        "who": ["Mr. Iyer"],
    }
    extraction = {"story_atoms": [thin_atom], "significant_people": [_IYER]}
    get_people, upsert, resolve = _resolution_patches(
        already_on_file=[{"name": "Mr. Iyer"}]
    )

    with get_people, upsert, resolve as mock_resolve:
        await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_resolve.assert_not_called()
