"""
Every fixed thing Katha says to a parent, in her language
(docs/PRE_PILOT_FIX_PLAN.md, P2; finding M-14).

Most of what Katha says is written fresh by the dialogue model, in the
parent's language. A handful of messages are not: the consent welcome and
its outcomes, the failure apologies, the crisis reply, "we already talked
today". Until this module they were English literals handed straight to a
Hindi or Tamil voice. TTS reads text; it does not translate it. So a
parent who speaks no English heard her consent notice, her consent
question and the crisis message in English, while every check that looked
at the language code passed.

Each language is a JSON file in messages/, named by its BCP-47 code and
holding {message_id: text}. A text is either a string or, where the
language marks the speaker's gender in the verb (Hindi, Urdu, Punjabi),
{"female": ..., "male": ...}, chosen by Katha's persona. Placeholders are
{name} (the parent) and {child}.

Every file carries a "_review" block saying where its words came from:
"original" (the English, written by the team), "machine" (translated by
Claude, not yet read by a native speaker) or "human" (read and corrected
by one). That is reported on the operator metrics page and gates nothing
(decision D1, 2026-10-20): a machine translation she can follow is better
than English she cannot.
"""

from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

MESSAGES_DIR = Path(__file__).parent / "messages"
SOURCE_LANGUAGE = "en-IN"

# Every fixed message a parent can receive. A language file must hold all
# of them; tests/test_speech_catalogue.py fails CI otherwise.
MESSAGE_IDS: tuple[str, ...] = (
    "consent_welcome",
    "consent_welcome_no_child",
    "consent_reask",
    "consent_granted",
    "consent_confirm",
    "consent_declined",
    "withdraw_confirm",
    "fallback_stt",
    "fallback_llm",
    "fallback_other",
    "malformed",
    "already_talked",
    "already_talked_addendum",
    "trial_paused",
    "text_only",
    "crisis",
    "crisis_text",
)

# Keys in a language file that are data, not something Katha says.
# crisis_patterns: phrases that mean she may be in danger, in that language
# (P9, M-11). Detection checks every language's list together, so a parent
# who switches language, or whose speech is transcribed in another
# script, is still heard.
DATA_KEYS: tuple[str, ...] = ("crisis_patterns",)

REVIEW_SOURCES = ("original", "machine", "human")
# Sources whose words a person has stood behind.
_REVIEWED = frozenset({"original", "human"})
_GENDERS = ("female", "male")
_DEFAULT_GENDER = "female"


@lru_cache(maxsize=None)
def _load(language: str) -> dict:
    path = MESSAGES_DIR / f"{language}.json"
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def languages() -> list[str]:
    """Every language with a message file."""
    return sorted(p.stem for p in MESSAGES_DIR.glob("*.json"))


def _entries(language: str) -> dict:
    try:
        return _load(language)
    except FileNotFoundError:
        # Onboarding only accepts supported languages, so this means a
        # profile predating that check. English is the only safe guess.
        logger.warning(
            "No message file for language %r — using %s", language, SOURCE_LANGUAGE
        )
        return _load(SOURCE_LANGUAGE)


def text(
    message_id: str, language: str, *, gender: str | None = None, **fields: str
) -> str:
    """
    The words for `message_id` in `language`, as Katha's persona says them,
    with `fields` filled in. A message missing from a language falls back
    to English with a warning (the completeness test should make that
    impossible); an unknown id, or a missing field, raises.
    """
    value = _entries(language).get(message_id)
    if value is None:
        if message_id not in MESSAGE_IDS:
            raise KeyError(f"unknown fixed message: {message_id}")
        logger.warning(
            "Message %r missing for %r — using %s",
            message_id,
            language,
            SOURCE_LANGUAGE,
        )
        value = _load(SOURCE_LANGUAGE)[message_id]
    if isinstance(value, dict):
        value = value[gender if gender in _GENDERS else _DEFAULT_GENDER]
    return value.format(**fields)


def crisis_patterns(language: str) -> list[str]:
    """The crisis phrases written for this language (P9)."""
    return list(_entries(language).get("crisis_patterns") or [])


@lru_cache(maxsize=None)
def all_crisis_patterns() -> tuple[str, ...]:
    """Every language's crisis phrases, lowercased and de-duplicated, in a
    stable order. What detection checks, whatever her language."""
    seen: dict[str, None] = {}
    for language in languages():
        for pattern in crisis_patterns(language):
            seen.setdefault(pattern.lower(), None)
    return tuple(seen)


def review(language: str) -> dict:
    """The language's review block: source, reviewer, date."""
    return dict(_entries(language).get("_review") or {"source": "machine"})


def reviewed(language: str) -> bool:
    """Whether a person has stood behind this language's words. Reporting
    only, never a gate (D1)."""
    return review(language).get("source") in _REVIEWED
