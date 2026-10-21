"""
The fixed-message catalogue (speech/catalogue.py, PRE_PILOT_FIX_PLAN.md P2).

Every fixed thing Katha says to a parent used to be an English literal
read aloud by an Indic voice (M-14). These tests hold the catalogue to
what makes that impossible to regress: every supported language has every
message, translations keep their placeholders and the iCall number, and
no module outside speech/ carries the English text any more.
"""

from __future__ import annotations

import re
import string
from pathlib import Path

import pytest

from core.conversation_policy import find_crisis_keyword
from prompts.system_prompt import SUPPORTED_LANGUAGES
from speech import catalogue

_BACKEND = Path(__file__).resolve().parent.parent
_ICALL = "9152987821"


def _variants(value) -> list[str]:
    return list(value.values()) if isinstance(value, dict) else [value]


def _fields(text: str) -> set[str]:
    return {f for _, f, _, _ in string.Formatter().parse(text) if f}


def test_every_supported_language_has_a_message_file():
    assert set(catalogue.languages()) == set(SUPPORTED_LANGUAGES)


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_language_has_exactly_the_known_messages(language):
    entries = catalogue._load(language)
    ids = {k for k in entries if not k.startswith("_") and k not in catalogue.DATA_KEYS}
    assert ids == set(catalogue.MESSAGE_IDS), (
        f"{language}: missing {set(catalogue.MESSAGE_IDS) - ids}, "
        f"unknown {ids - set(catalogue.MESSAGE_IDS)}"
    )


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_translations_keep_the_english_placeholders(language):
    source = catalogue._load(catalogue.SOURCE_LANGUAGE)
    entries = catalogue._load(language)
    for message_id in catalogue.MESSAGE_IDS:
        expected = _fields(source[message_id])
        for text in _variants(entries[message_id]):
            assert _fields(text) == expected, (
                f"{language}/{message_id}: placeholders {_fields(text)}, "
                f"English has {expected}"
            )


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_gendered_messages_have_both_personas(language):
    for message_id, value in catalogue._load(language).items():
        if isinstance(value, dict) and not message_id.startswith("_"):
            assert set(value) == {"female", "male"}, f"{language}/{message_id}"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
@pytest.mark.parametrize("message_id", ["crisis", "crisis_text"])
def test_crisis_messages_carry_the_icall_number_as_digits(language, message_id):
    for text in _variants(catalogue._load(language)[message_id]):
        assert _ICALL in text, f"{language}/{message_id} lost the iCall number"


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_no_fixed_message_trips_the_crisis_keywords(language):
    """Katha's own replies are scanned for crisis language; her fixed
    messages must never match it themselves."""
    for message_id in catalogue.MESSAGE_IDS:
        for text in _variants(catalogue._load(language)[message_id]):
            assert find_crisis_keyword(text) is None, f"{language}/{message_id}"


@pytest.mark.parametrize(
    "language", [lang for lang in SUPPORTED_LANGUAGES if lang != "en-IN"]
)
def test_translations_are_not_english(language):
    """A copied-in English file would pass every structural test."""
    source = catalogue._load(catalogue.SOURCE_LANGUAGE)
    entries = catalogue._load(language)
    for message_id in catalogue.MESSAGE_IDS:
        for text in _variants(entries[message_id]):
            assert text != source[message_id], f"{language}/{message_id}"
            # Latin letters beyond the iCall name, "AI" and the placeholders
            # would mean English left in the text.
            latin = re.sub(r"\{\w+\}|iCall India|AI", "", text)
            assert not re.search(r"[A-Za-z]{4,}", latin), (
                f"{language}/{message_id} has English in it: {text[:80]!r}"
            )


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_review_block_says_where_the_words_came_from(language):
    review = catalogue.review(language)
    assert review.get("source") in catalogue.REVIEW_SOURCES
    assert review.get("date")


def test_english_is_the_original_and_the_rest_are_machine_until_read():
    assert catalogue.reviewed("en-IN")
    for language in SUPPORTED_LANGUAGES:
        if catalogue.review(language)["source"] == "machine":
            assert not catalogue.reviewed(language)


def test_text_fills_placeholders_and_picks_the_persona():
    female = catalogue.text("consent_reask", "hi-IN", gender="female", name="Kamala")
    male = catalogue.text("consent_reask", "hi-IN", gender="male", name="Kamala")
    assert "Kamala" in female and "Kamala" in male
    assert female != male
    assert "पाई" in female and "पाया" in male


def test_text_defaults_to_the_female_persona():
    assert catalogue.text("fallback_stt", "hi-IN") == catalogue.text(
        "fallback_stt", "hi-IN", gender="female"
    )


def test_an_unknown_language_falls_back_to_english():
    assert catalogue.text("fallback_llm", "xx-XX") == catalogue.text(
        "fallback_llm", "en-IN"
    )


def test_an_unknown_message_raises():
    with pytest.raises(KeyError):
        catalogue.text("no_such_message", "en-IN")


def test_a_missing_field_raises():
    with pytest.raises(KeyError):
        catalogue.text("consent_welcome", "ta-IN", name="Lakshmi")


# Sentences that used to be English literals in the code. If one appears in
# a module again, a parent who speaks no English is hearing it.
_FORMER_LITERALS = (
    "I am a computer program",
    "I did not quite follow",
    "That means a great deal",
    "I will not record anything",
    "I couldn't quite hear that",
    "I'm having a little trouble",
    "Something went wrong on my side",
    "I lost my train of thought",
    "We already had our conversation today",
    "Please send me a voice message",
    "Please reach out to iCall India",
)


def test_no_module_outside_speech_carries_a_fixed_message():
    offenders = []
    for path in _BACKEND.rglob("*.py"):
        rel = path.relative_to(_BACKEND).as_posix()
        if rel.startswith((".venv/", "tests/", "speech/", "evals/")):
            continue
        source = path.read_text(encoding="utf-8")
        offenders += [f"{rel}: {s!r}" for s in _FORMER_LITERALS if s in source]
    assert not offenders, "\n".join(offenders)


def test_metrics_report_each_languages_review_status():
    """The operator sees which families hear machine-translated text (D1)."""
    from types import SimpleNamespace

    from core import metrics, metrics_page

    world = SimpleNamespace(
        storytellers={
            "a": SimpleNamespace(preferred_language="ta-IN"),
            "b": SimpleNamespace(preferred_language="ta-IN"),
            "c": SimpleNamespace(preferred_language="en-IN"),
        }
    )
    rows = metrics._languages(world)
    assert rows == [
        {
            "language": "en-IN",
            "families": 1,
            "source": "original",
            "reviewer": "Katha team",
            "date": "2026-10-20",
        },
        {
            "language": "ta-IN",
            "families": 2,
            "source": "machine",
            "reviewer": None,
            "date": "2026-10-20",
        },
    ]
    html = metrics_page._languages(rows)
    assert "<b>machine</b>" in html and "ta-IN" in html


# ── crisis coverage (P9, M-11) ───────────────────────────────────────────────


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_language_can_hear_and_answer_a_crisis(language):
    """It covered six languages; a Malayalam speaker in distress was not
    detected at all. Every language now has a crisis reply in it, and at
    least five crisis phrases, and one of them trips the pre-turn check."""
    from core.conversation_policy import check_pre_turn
    from core.session_manager import SessionState

    patterns = catalogue.crisis_patterns(language)
    assert len(patterns) >= 5, f"{language}: only {len(patterns)} crisis patterns"

    state = SessionState(
        session_id="s",
        user_id="u",
        session_number=1,
        domain="childhood",
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    said = f"Some days are hard. {patterns[0]}. That is how I feel."
    result = check_pre_turn(said, state, language=language)
    assert result.crisis_detected and not result.allowed, language
    assert result.override_response == catalogue.text("crisis", language)
    assert _ICALL in result.override_response


# ── the 1.1 notice (P10, M-06) ───────────────────────────────────────────────


@pytest.mark.parametrize("language", SUPPORTED_LANGUAGES)
def test_every_welcome_says_who_processes_her_words(language):
    """The spoken notice is all she gets; the web page she never sees. It
    must say that AI companies, some outside India, process her words, that
    a person may read them, and that her family sees summaries."""
    from core.parent_consent import build_welcome_text, update_text

    for gender in ("female", "male"):
        processing = catalogue.text("consent_processing", language, gender=gender)
        for child in ("Priya", None):
            welcome = build_welcome_text(
                "Lakshmi", child, language=language, gender=gender
            )
            assert processing in welcome, (language, gender, child)
        assert processing in update_text("Lakshmi", language=language, gender=gender)
    assert "AI" in catalogue.text("consent_processing", language)
