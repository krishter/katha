from __future__ import annotations

import json
import re
from dataclasses import dataclass

from core.session_manager import SessionState

_CRISIS_RESPONSE = (
    "I can hear that things feel very difficult right now, and I'm glad you felt "
    "safe enough to share that with me. Please reach out to iCall India: 9152987821 "
    "— they are available to talk and to help. Your wellbeing matters more than "
    "any story we could share today. Please take care of yourself."
)

_MALFORMED_RESPONSE = (
    "I'm so sorry, I lost my train of thought just now! Could you tell me that "
    "again? I want to make sure I give you my full attention."
)

CRISIS_KEYWORDS: list[str] = [
    "end my life",
    "don't want to live",
    "do not want to live",
    "suicide",
    "kill myself",
    "no reason to live",
    "want to die",
    "better off dead",
    "wish i was dead",
    "can't go on",
    "cannot go on",
    # Hindi crisis phrases (romanized)
    "jeena nahi chahta",
    "jeena nahi chahti",
    "marna chahta hoon",
    "marna chahti hoon",
    "jaan dena chahta",
    "khud ko khatam",
    # Hindi / Marathi crisis phrases (Devanagari — shared script).
    # NOTE: reviewed for correctness of common suicide-risk phrasing, but
    # per the architecture review this list still needs native-speaker
    # sign-off before pilot launch — do not treat as clinically validated.
    "जीना नहीं चाहता",
    "जीना नहीं चाहती",
    "मरना चाहता हूँ",
    "मरना चाहती हूँ",
    "जान देना चाहता",
    "खुद को खत्म",
    "आत्महत्या",
    "जगायचं नाही",
    "मरायचं आहे",
    # Tamil crisis phrases
    "தற்கொலை",
    "சாக விரும்புகிறேன்",
    "வாழ விரும்பவில்லை",
    # Telugu crisis phrases
    "ఆత్మహత్య",
    "చనిపోవాలని అనిపిస్తుంది",
    "జీవించాలని అనుకోవడం లేదు",
    # Bengali crisis phrases
    "আত্মহত্যা",
    "বাঁচতে চাই না",
    "মরে যেতে ইচ্ছা করছে",
]


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


def check_pre_turn(transcript: str, session_state: SessionState) -> PolicyResult:
    """
    Checks before sending to LLM:
    1. Crisis detection — scan transcript for crisis keywords.
    2. Returns PolicyResult; if crisis detected, allowed=False with override_response.
    """
    matched = find_crisis_keyword(transcript)
    if matched is not None:
        return PolicyResult(
            allowed=False,
            override_response=_CRISIS_RESPONSE,
            crisis_detected=True,
            matched_pattern=matched,
        )
    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)


def check_response_for_crisis(response_text: str) -> PolicyResult:
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
            override_response=_CRISIS_RESPONSE,
            crisis_detected=True,
            matched_pattern=matched,
        )
    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)


def check_post_turn(llm_response: str, session_state: SessionState) -> PolicyResult:
    """
    Checks after the dialogue LLM call responds:
    1. Validate the <response> tag is present.
    2. If malformed, return override_response with safe fallback.

    The dialogue call returns <response> only — extraction is a separate
    call (see check_extraction_response) with its own guard.
    """
    has_response = bool(
        re.search(r"<response>\s*.+?\s*</response>", llm_response, re.DOTALL)
    )

    if not has_response:
        return PolicyResult(
            allowed=False,
            override_response=_MALFORMED_RESPONSE,
            crisis_detected=False,
        )

    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)


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
