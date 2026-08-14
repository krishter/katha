import json
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from extraction.entity_extractor import NamedEntities, extract_entities

_USER_ID = "user-1"
_TRANSCRIPT = (
    "My father Raman worked at Indian Bank in Madurai in the 1960s. "
    "My sister Kamala went to Meenakshi College."
)

_LLM_RESPONSE_JSON = {
    "people": [
        {"name": "Raman", "relationship": "father"},
        {"name": "Kamala", "relationship": "sister"},
    ],
    "places": ["Madurai"],
    "dates": ["1960s"],
    "institutions": ["Indian Bank", "Meenakshi College"],
}


def _make_db():
    db = AsyncMock()
    return db


async def test_extract_entities_parses_llm_response():
    db = _make_db()
    llm_content = json.dumps(_LLM_RESPONSE_JSON)

    with (
        patch(
            "extraction.entity_extractor.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=100, output_tokens=50
                )
            ),
        ),
        patch(
            "extraction.entity_extractor.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "extraction.entity_extractor.fact_store.update_facts",
            new=AsyncMock(),
        ),
    ):
        result = await extract_entities(_TRANSCRIPT, _USER_ID, db)

    assert isinstance(result, NamedEntities)
    names = [p["name"] for p in result.people]
    assert "Raman" in names
    assert "Kamala" in names
    assert "Madurai" in result.places
    assert "1960s" in result.dates
    assert "Indian Bank" in result.institutions


async def test_extract_entities_calls_update_facts():
    db = _make_db()
    llm_content = json.dumps(_LLM_RESPONSE_JSON)

    with (
        patch(
            "extraction.entity_extractor.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=50, output_tokens=30
                )
            ),
        ),
        patch(
            "extraction.entity_extractor.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "extraction.entity_extractor.fact_store.update_facts",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await extract_entities(_TRANSCRIPT, _USER_ID, db)

    mock_update.assert_called_once()


async def test_extract_entities_no_duplicate_people():
    db = _make_db()
    # Existing facts already has Raman
    existing_facts = {"people": [{"name": "Raman", "relationship": "father"}]}
    llm_content = json.dumps(_LLM_RESPONSE_JSON)

    with (
        patch(
            "extraction.entity_extractor.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=llm_content, input_tokens=50, output_tokens=30
                )
            ),
        ),
        patch(
            "extraction.entity_extractor.fact_store.get_facts",
            new=AsyncMock(return_value=existing_facts),
        ),
        patch(
            "extraction.entity_extractor.fact_store.update_facts",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await extract_entities(_TRANSCRIPT, _USER_ID, db)

    # Verify the merged dict passed to update_facts has no duplicate Raman
    call_args = mock_update.call_args
    merged = call_args.args[1]
    raman_entries = [p for p in merged["people"] if p["name"] == "Raman"]
    assert len(raman_entries) == 1


async def test_extract_entities_handles_bad_llm_json():
    db = _make_db()

    with (
        patch(
            "extraction.entity_extractor.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content="not valid json", input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch(
            "extraction.entity_extractor.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "extraction.entity_extractor.fact_store.update_facts",
            new=AsyncMock(),
        ) as mock_update,
    ):
        result = await extract_entities(_TRANSCRIPT, _USER_ID, db)

    assert isinstance(result, NamedEntities)
    assert result.people == []
    mock_update.assert_not_called()


# ── fenced output ─────────────────────────────────────────────────────────────
#
# The tests above all feed bare json.dumps output. The live model wraps this
# particular response in a ```json fence essentially every time, which used to
# fail the parse and return before the fact store was written — leaving
# structured_facts empty for every user. These cover the shape that actually
# arrives.


_FENCED_LLM_RESPONSE = (
    "```json\n"
    '{"people": [{"name": "Kamala", "relationship": "sister"}],\n'
    ' "places": ["Madurai"],\n'
    ' "dates": ["1948"],\n'
    ' "institutions": ["provisions shop"]}\n'
    "```"
)


async def test_extract_entities_parses_fenced_json():
    db = _make_db()

    with (
        patch(
            "extraction.entity_extractor.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=_FENCED_LLM_RESPONSE, input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch(
            "extraction.entity_extractor.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "extraction.entity_extractor.fact_store.update_facts",
            new=AsyncMock(),
        ),
    ):
        result = await extract_entities(_TRANSCRIPT, _USER_ID, db)

    assert [p["name"] for p in result.people] == ["Kamala"]
    assert result.places == ["Madurai"]
    assert result.dates == ["1948"]


async def test_fenced_json_reaches_the_fact_store():
    """The regression that mattered: a fence made extract_entities return
    early, so update_facts was never called and the fact store stayed
    empty no matter how many sessions ran."""
    db = _make_db()

    with (
        patch(
            "extraction.entity_extractor.llm.chat",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    content=_FENCED_LLM_RESPONSE, input_tokens=10, output_tokens=5
                )
            ),
        ),
        patch(
            "extraction.entity_extractor.fact_store.get_facts",
            new=AsyncMock(return_value={}),
        ),
        patch(
            "extraction.entity_extractor.fact_store.update_facts",
            new=AsyncMock(),
        ) as mock_update,
    ):
        await extract_entities(_TRANSCRIPT, _USER_ID, db)

    mock_update.assert_called_once()
    merged = mock_update.call_args[0][1]
    assert [p["name"] for p in merged["people"]] == ["Kamala"]


def test_strip_code_fence_variants():
    from extraction.entity_extractor import _strip_code_fence

    payload = '{"people": []}'
    assert _strip_code_fence(f"```json\n{payload}\n```") == payload
    assert _strip_code_fence(f"```\n{payload}\n```") == payload
    assert _strip_code_fence(f"```JSON\n{payload}\n```") == payload
    assert _strip_code_fence(f"  ```json\n{payload}\n```  ") == payload
    # Unfenced input is returned unchanged (stripped).
    assert _strip_code_fence(payload) == payload
    assert _strip_code_fence(f"\n{payload}\n") == payload
    # A stray fence marker inside the JSON must not truncate it.
    tricky = '{"places": ["```"]}'
    assert _strip_code_fence(f"```json\n{tricky}\n```") == tricky
