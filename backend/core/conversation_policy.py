from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.session_manager import SessionState
from speech import catalogue

# The crisis reply and the "lost my train of thought" reply are fixed
# messages, in her language from speech/catalogue.py ("crisis",
# "malformed"). Every check below takes the parent's language and Katha's
# persona for that reason; the default is English, for callers with no
# parent (the eval harness, the listening kit).
SOURCE_LANGUAGE = catalogue.SOURCE_LANGUAGE


# Every language's crisis phrases (speech/messages/<lang>.json,
# "crisis_patterns"; P9, M-11). It was one hard-coded list covering English,
# romanised Hindi, Hindi and Marathi, Tamil, Telugu and Bengali, and nothing
# for Kannada, Malayalam, Gujarati, Punjabi, Odia, Urdu or Assamese. The
# lists are machine-drafted and reviewed with the rest of the catalogue
# (D1): not clinically validated, and drafted generously on purpose.
CRISIS_KEYWORDS: tuple[str, ...] = catalogue.all_crisis_patterns()


def find_crisis_keyword(text: str) -> str | None:
    """Return the first matching crisis keyword in text, or None."""
    lower = text.lower()
    for keyword in CRISIS_KEYWORDS:
        if keyword in lower:
            return keyword
    return None


@dataclass
class PolicyResult:
    allowed: bool
    override_response: str | None
    crisis_detected: bool
    matched_pattern: str | None = None
    # True when the dialogue reply was accepted despite arriving without its
    # <response> wrapper. Callers log this so the rate stays observable.
    salvaged_untagged: bool = False
    # True when the reply was cut off by max_tokens and we sent it trimmed
    # back to its last complete sentence. Also logged.
    salvaged_truncated: bool = False
    # The exact text to speak, when allowed. check_post_turn is the single
    # place that decides this: it strips reasoning blocks and trims
    # truncation, and a caller that re-parsed the raw output itself would
    # undo both. None only when allowed is False.
    response_text: str | None = None


def check_pre_turn(
    transcript: str,
    session_state: SessionState,
    *,
    language: str = SOURCE_LANGUAGE,
    gender: str | None = None,
) -> PolicyResult:
    """
    Checks before sending to LLM:
    1. Crisis detection — scan transcript for crisis keywords.
    2. Returns PolicyResult; if crisis detected, allowed=False with override_response.
    """
    matched = find_crisis_keyword(transcript)
    if matched is not None:
        return PolicyResult(
            allowed=False,
            override_response=catalogue.text("crisis", language, gender=gender),
            crisis_detected=True,
            matched_pattern=matched,
        )
    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)


def check_response_for_crisis(
    response_text: str,
    *,
    language: str = SOURCE_LANGUAGE,
    gender: str | None = None,
) -> PolicyResult:
    """
    Scan Katha's own generated reply for crisis-adjacent language. This is a
    safety net, not a duplicate of check_pre_turn: if the LLM's response
    ever resembles encouragement of self-harm, the crisis override must take
    precedence over whatever it generated, regardless of what the user said.
    """
    matched = find_crisis_keyword(response_text)
    if matched is not None:
        return PolicyResult(
            allowed=False,
            override_response=catalogue.text("crisis", language, gender=gender),
            crisis_detected=True,
            matched_pattern=matched,
        )
    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)


# The dialogue call is told to return <response> only, and mostly does.
# But on a long, detailed voice note the model routinely thinks out loud
# first — measured at roughly a third of calls on an 89-second Hindi note,
# at 400-600 tokens of English reasoning before the reply even starts.
# A complete block is simply dropped. An *unterminated* one means
# generation was cut off inside the reasoning, so there is no reply at all
# behind it — and that text must never be spoken: it is Katha narrating her
# own analysis of the user ("The user is sharing beautiful memories... Let
# me analyze what they've shared") in English, to an elderly person who
# just told her a story.
_THINKING_OPEN = "<thinking>"
_THINKING_RE = re.compile(r"<thinking>.*?</thinking>", re.DOTALL)

# Sentence terminators across the scripts Katha speaks: the Latin full stop
# (used by Tamil, Telugu, Kannada, Malayalam and English) plus the
# Devanagari danda and double danda (Hindi, Marathi) and the ellipsis.
_SENTENCE_END_RE = re.compile(r"[.!?\u0964\u0965\u2026]+(?=\s|$)")

# A trimmed reply shorter than this is not worth sending — the same floor
# the untagged-salvage path uses.
_MIN_USABLE_LENGTH = 15


def strip_reasoning(text: str) -> str:
    """Remove any complete <thinking>...</thinking> block."""
    return _THINKING_RE.sub("", text).strip()


def trim_to_last_sentence(text: str) -> str | None:
    """
    Cut a truncated reply back to its last complete sentence.

    A reply that lost its final clause to max_tokens is still a good reply
    — two warm Hindi sentences and a question are a complete conversational
    turn. Discarding them to say "could you tell me that again?" throws
    away work the user already did and asks an 80-year-old to repeat a
    five-minute story. Returns None when there is no sentence boundary to
    cut at, i.e. the reply was cut off before finishing its first sentence.
    """
    matches = list(_SENTENCE_END_RE.finditer(text))
    if not matches:
        return None
    trimmed = text[: matches[-1].end()].strip()
    return trimmed if len(trimmed) >= _MIN_USABLE_LENGTH else None


def salvage_untagged_response(
    llm_response: str, *, truncated: bool = False
) -> str | None:
    """
    Decide whether an untagged dialogue output is usable as-is.

    The dialogue call is asked to wrap its reply in <response>, but on short,
    low-engagement user turns the model routinely answers in bare prose and
    drops the wrapper. Measured on a two-word "not much to say" turn, that
    happens on roughly half of calls — and the replies themselves are fine.
    Discarding them costs a good answer and hands the user
    the "malformed" message ("could you tell me that again?"), which is the worst
    possible reply to someone who is already disengaging.

    `truncated` says max_tokens cut generation off — the caller gets that
    from the API's stop_reason rather than guessing it from the text.

    Returns the salvaged text, or None if the output is genuinely unusable.
    """
    text = strip_reasoning(llm_response)

    if not text:
        return None

    # An unterminated reasoning block: generation stopped inside Katha's
    # own analysis, so there is no reply behind it to recover.
    if _THINKING_OPEN in text:
        return None

    # Machinery leaking into the dialogue channel: the extraction block
    # belongs to a separate call, and a bare JSON object is not a reply.
    if "<extraction>" in text:
        return None
    if text.startswith("{") or text.startswith("["):
        return None

    # An opening <response> with no closing tag means the reply was cut off
    # inside the block. Recover what completed; the tag itself must not be
    # spoken aloud.
    if "<response>" in text and "</response>" not in text:
        if not truncated:
            return None
        return trim_to_last_sentence(text.split("<response>", 1)[1].strip())

    if truncated:
        return trim_to_last_sentence(text)

    # Too short to be a real conversational turn.
    if len(text) < _MIN_USABLE_LENGTH:
        return None

    return text


def check_post_turn(
    llm_response: str,
    session_state: SessionState,
    *,
    truncated: bool = False,
    language: str = SOURCE_LANGUAGE,
    gender: str | None = None,
) -> PolicyResult:
    """
    Checks after the dialogue LLM call responds:
    1. Accept a well-formed <response> block.
    2. Otherwise try to salvage — a bare untagged reply, or a truncated one
       trimmed back to its last complete sentence.
    3. Only fall back to the "malformed" message when nothing usable came
       back.

    The dialogue call returns <response> only — extraction is a separate
    call (see check_extraction_response) with its own guard.

    `truncated` is the API's stop_reason == "max_tokens", not a guess from
    the text. It changes the answer: an unclosed <response> on a complete
    generation is a malformed reply, while the same text on a truncated one
    is a good reply missing its tail, and trimming it beats apologising.

    Returns the text to speak in `response_text`. Callers must use that
    rather than re-parsing the raw output, which would reintroduce both the
    reasoning block and the truncation this strips.

    Note the salvage paths are not silent passes: callers log them, and the
    crisis safety net in check_response_for_crisis still runs over whatever
    text is ultimately sent, salvaged or not.
    """
    cleaned = strip_reasoning(llm_response)
    match = re.search(r"<response>\s*(.+?)\s*</response>", cleaned, re.DOTALL)

    if match:
        return PolicyResult(
            allowed=True,
            override_response=None,
            crisis_detected=False,
            response_text=match.group(1).strip(),
        )

    salvaged = salvage_untagged_response(llm_response, truncated=truncated)
    if salvaged is not None:
        return PolicyResult(
            allowed=True,
            override_response=None,
            crisis_detected=False,
            salvaged_untagged=not truncated,
            salvaged_truncated=truncated,
            response_text=salvaged,
        )

    return PolicyResult(
        allowed=False,
        override_response=catalogue.text("malformed", language, gender=gender),
        crisis_detected=False,
    )


def check_extraction_response(llm_response: str) -> bool:
    """
    Validate the extraction call's output: True if a well-formed
    <extraction>{...}</extraction> block with parseable JSON is present.
    Unlike check_post_turn, this has no user-facing override — callers are
    expected to retry once with a stricter instruction before giving up
    (see orchestrator.run_extraction_for_turn).
    """
    match = re.search(r"<extraction>\s*(.+?)\s*</extraction>", llm_response, re.DOTALL)
    if not match:
        return False
    try:
        json.loads(match.group(1))
    except json.JSONDecodeError:
        return False
    return True
