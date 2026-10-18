import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from extraction.story_extractor import (
    ExtractionResult,
    coerce_domain,
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


async def test_resolves_a_known_person_even_when_the_extractor_does_not_relist_them():
    """
    The extraction prompt says not to re-flag a known significant person
    without new emotional signal. A model that follows that literally
    (Haiku 5.5 does) returns significant_people: [] for the very turn in
    which the user finally tells the full story — and resolution, which
    used to look only at re-listed people, never happened. TC-11 failed
    its resolution gate on three eval runs in a row for exactly this.
    """
    db = _make_db()
    extraction = {"story_atoms": [_ATOM_ABOUT_IYER], "significant_people": []}
    get_people, upsert, resolve = _resolution_patches(
        already_on_file=[{"name": "Mr. Iyer (school teacher)"}]
    )

    with get_people, upsert, resolve as mock_resolve:
        result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    # Resolved under the name it is stored by, so mark_resolved finds it.
    mock_resolve.assert_called_once_with(_USER_ID, "Mr. Iyer (school teacher)", db)
    assert result.resolved_people == ["Mr. Iyer (school teacher)"]


async def test_a_known_person_only_mentioned_in_passing_stays_unresolved():
    db = _make_db()
    passing = {
        **_ATOM_ABOUT_IYER,
        "when_approx": None,
        "where_approx": None,
        "why": None,
        "what": None,
    }
    extraction = {"story_atoms": [passing], "significant_people": []}
    get_people, upsert, resolve = _resolution_patches(
        already_on_file=[{"name": "Mr. Iyer"}]
    )

    with get_people, upsert, resolve as mock_resolve:
        await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_resolve.assert_not_called()


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


# ── domain coercion ──────────────────────────────────────────────────────────


def test_a_known_domain_is_kept():
    assert coerce_domain("childhood", "career") == "childhood"


def test_a_display_name_is_matched_back_to_its_id():
    assert coerce_domain("Childhood & Home", "career") == "childhood"


def test_casing_and_spacing_are_tolerated():
    assert coerce_domain("Family Ancestors", "career") == "family_ancestors"


def test_an_unknown_domain_falls_back_to_the_session_domain():
    """Not to a placeholder. An atom extracted during a childhood session
    is far more likely to be about childhood than about nothing, and
    "unknown" is an id nothing in the system can look up."""
    assert coerce_domain("education_coming_of_age", "childhood") == "childhood"


def test_a_missing_domain_falls_back_to_the_session_domain():
    assert coerce_domain(None, "childhood") == "childhood"
    assert coerce_domain("", "childhood") == "childhood"


def test_a_non_string_domain_does_not_raise():
    assert coerce_domain({"nested": "object"}, "career") == "career"


def test_everything_unusable_still_returns_a_string():
    """Never raises: this runs inside background extraction, where an
    exception costs the whole turn's atoms."""
    assert coerce_domain("nonsense", None) == "nonsense"
    assert coerce_domain(None, None) == "unknown"
