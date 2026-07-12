import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from extraction.story_extractor import (
    ExtractionResult,
    compute_completeness,
    process_extraction,
)


def _no_op_create_task(coro, **kwargs):
    """Close the coroutine so it doesn't warn about never being awaited."""
    coro.close()
    return MagicMock()


def _make_db():
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db.execute = AsyncMock(return_value=result)
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

    with patch(
        "extraction.story_extractor.asyncio.create_task",
        side_effect=_no_op_create_task,
    ):
        result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    assert isinstance(result, ExtractionResult)
    assert len(result.story_atoms) == 1
    db.add.assert_called_once()
    db.commit.assert_called()


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

    mock_upsert.assert_called_once_with(_USER_ID, people[0], db)


async def test_process_extraction_marks_resolved_when_atom_scores_3():
    db = _make_db()
    # Atom narrative contains person name; score = 5
    atom_about_iyer = {
        "domain": "education",
        "narrative": "Mr. Iyer was the teacher who changed my life.",
        "who": ["Mr. Iyer"],
        "what": "Inspired teaching career",
        "when_approx": "1965",
        "where_approx": "Madurai school",
        "why": "Encouragement despite family pressure",
    }
    people = [
        {"name": "Mr. Iyer", "relationship": "teacher", "why_significant": "Inspiring"}
    ]
    extraction = {"story_atoms": [atom_about_iyer], "significant_people": people}

    with (
        patch(
            "extraction.story_extractor.fact_store.upsert_significant_person",
            new=AsyncMock(),
        ),
        patch(
            "extraction.story_extractor.fact_store.mark_resolved",
            new=AsyncMock(),
        ) as mock_resolve,
        patch(
            "extraction.story_extractor.asyncio.create_task",
            side_effect=_no_op_create_task,
        ),
    ):
        result = await process_extraction(extraction, _SESSION_ID, _USER_ID, db)

    mock_resolve.assert_called_once_with(_USER_ID, "Mr. Iyer", db)
    assert "Mr. Iyer" in result.resolved_people
