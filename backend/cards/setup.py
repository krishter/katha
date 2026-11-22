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


# Old names some browsers still report (Intl, from CLDR), mapped to the
# current IANA name. Chrome and Safari in India give "Asia/Calcutta", and
# the production image's zone files (Debian) leave the old names out, so
# without this the onboarding form failed for most Indian families. The
# tzdata package (requirements.in) resolves every other old name; this map
# only makes sure the stored name is the current one.
_LEGACY_NAMES = {
    "Asia/Calcutta": "Asia/Kolkata",
    "Asia/Katmandu": "Asia/Kathmandu",
    "Asia/Rangoon": "Asia/Yangon",
    "Asia/Saigon": "Asia/Ho_Chi_Minh",
    "Asia/Dacca": "Asia/Dhaka",
    "Asia/Thimbu": "Asia/Thimphu",
    "Atlantic/Faeroe": "Atlantic/Faroe",
    "America/Buenos_Aires": "America/Argentina/Buenos_Aires",
    "America/Indianapolis": "America/Indiana/Indianapolis",
    "America/Louisville": "America/Kentucky/Louisville",
    "Pacific/Truk": "Pacific/Chuuk",
    "Pacific/Ponape": "Pacific/Pohnpei",
    "Europe/Kiev": "Europe/Kyiv",
}


def parse_timezone(name: str) -> str:
    """An IANA timezone name, checked, under its current name. Raises
    ValueError."""
    name = _LEGACY_NAMES.get(name, name)
    try:
        ZoneInfo(name)
    except Exception as exc:
        raise ValueError(f"{name!r} is not an IANA timezone") from exc
    return name
