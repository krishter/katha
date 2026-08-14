from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field

from adapters import llm
from adapters.llm import Message
from memory import fact_store

logger = logging.getLogger(__name__)

_EXTRACTION_PROMPT = (
    "You are a precise entity extractor. Given a conversation transcript,\n"
    "extract the following entities and return ONLY valid JSON:\n\n"
    '{"people": [{"name": "string", "relationship": "string"}],\n'
    ' "places": ["string"], "dates": ["string"], "institutions": ["string"]}\n\n'
    "Rules:\n"
    "- people: named individuals; include relationship to speaker if stated\n"
    "- places: towns, cities, countries, neighbourhoods, landmarks\n"
    '- dates: approximate years or decades ("circa 1960", "1970s")\n'
    "- institutions: schools, employers, temples, hospitals, organisations\n"
    "- If a category has no entries, return an empty list []\n"
    "- Do NOT include the speaker themselves in the people list\n\n"
    "Transcript:\n"
)


_FENCE_RE = re.compile(
    r"^\s*```(?:json)?\s*\n(?P<body>.*?)\n?\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)


def _strip_code_fence(content: str) -> str:
    """
    Unwrap a ```json ... ``` fence if the model added one.

    This call asks for bare JSON and gets fenced JSON almost every time —
    which used to fail json.loads, log a warning, and return before the
    fact store was ever written, so structured_facts stayed empty for
    every user forever. Unlike the dialogue and story-extraction calls,
    this one has no delimiting tags to parse against, so the fence is the
    only thing standing between a correct extraction and a silent no-op.
    """
    if not isinstance(content, str):
        return content
    match = _FENCE_RE.match(content)
    return match.group("body") if match else content.strip()


@dataclass
class NamedEntities:
    people: list[dict] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    dates: list[str] = field(default_factory=list)
    institutions: list[str] = field(default_factory=list)


async def extract_entities(
    transcript: str,
    user_id: str,
    db,
) -> NamedEntities:
    """
    Use a lightweight LLM call to extract named entities from the transcript.
    Merge extracted entities into structured_facts via fact_store.update_facts.
    """
    response = await llm.chat(
        messages=[Message(role="user", content=_EXTRACTION_PROMPT + transcript)],
        system=None,
    )

    entities = NamedEntities()
    try:
        raw = json.loads(_strip_code_fence(response.content))
        entities.people = raw.get("people", [])
        entities.places = raw.get("places", [])
        entities.dates = raw.get("dates", [])
        entities.institutions = raw.get("institutions", [])
    except (json.JSONDecodeError, AttributeError, TypeError):
        logger.warning(
            "entity_extractor: failed to parse LLM JSON response: %.200r",
            response.content,
        )
        return entities

    # Load existing facts and merge
    existing = await fact_store.get_facts(user_id, db)
    merged = _merge_entities(existing, entities)
    await fact_store.update_facts(user_id, merged, db)

    return entities


def _merge_entities(existing: dict, new: NamedEntities) -> dict:
    """Merge new entities into the existing fact store dict without duplicates."""
    result = dict(existing)

    # Merge people (case-insensitive name dedup)
    existing_people: list[dict] = result.get("people", [])
    existing_names = {p.get("name", "").lower() for p in existing_people}
    for person in new.people:
        if person.get("name", "").lower() not in existing_names:
            existing_people.append(person)
            existing_names.add(person.get("name", "").lower())
    result["people"] = existing_people

    # Merge scalar lists (case-insensitive dedup)
    for key, values in (
        ("places", new.places),
        ("dates", new.dates),
        ("institutions", new.institutions),
    ):
        existing_list: list[str] = result.get(key, [])
        existing_lower = {v.lower() for v in existing_list}
        for v in values:
            if v.lower() not in existing_lower:
                existing_list.append(v)
                existing_lower.add(v.lower())
        result[key] = existing_list

    return result
