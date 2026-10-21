"""
What the child tells Katha so cards can be written and timed
(docs/specs/CONVERSATION_CARDS.md §8.2, §15): what they call her, their
timezone, and when they usually call her. Validated the same way whether it
comes from the onboarding form or from scripts/cards.py on a setup call.
"""

from __future__ import annotations

from zoneinfo import ZoneInfo

DAYS = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}
MAX_SLOTS = 7


def parse_slot(text: str) -> dict:
    """ "sun 11:00" -> {"dow": 6, "time": "11:00"}. Raises ValueError."""
    try:
        day, at = text.strip().lower().split()
        hour, minute = (int(x) for x in at.split(":"))
    except ValueError as exc:
        raise ValueError(
            f'{text!r}: give a time as "<day> HH:MM", e.g. "sun 11:00"'
        ) from exc
    if day[:3] not in DAYS or not (0 <= hour < 24 and 0 <= minute < 60):
        raise ValueError(f'{text!r}: give a time as "<day> HH:MM", e.g. "sun 11:00"')
    return {"dow": DAYS[day[:3]], "time": f"{hour:02d}:{minute:02d}"}


def parse_timezone(name: str) -> str:
    """An IANA timezone name, checked. Raises ValueError."""
    try:
        ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"{name!r} is not an IANA timezone") from exc
    return name
