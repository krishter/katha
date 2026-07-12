from __future__ import annotations

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
    # Hindi crisis phrases
    "jeena nahi chahta",
    "jeena nahi chahti",
    "marna chahta hoon",
    "marna chahti hoon",
    "jaan dena chahta",
    "khud ko khatam",
]


@dataclass
class PolicyResult:
    allowed: bool
    override_response: str | None
    crisis_detected: bool


def check_pre_turn(transcript: str, session_state: SessionState) -> PolicyResult:
    """
    Checks before sending to LLM:
    1. Crisis detection — scan transcript for crisis keywords.
    2. Returns PolicyResult; if crisis detected, allowed=False with override_response.
    """
    lower = transcript.lower()
    for keyword in CRISIS_KEYWORDS:
        if keyword in lower:
            return PolicyResult(
                allowed=False,
                override_response=_CRISIS_RESPONSE,
                crisis_detected=True,
            )
    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)


def check_post_turn(llm_response: str, session_state: SessionState) -> PolicyResult:
    """
    Checks after LLM responds:
    1. Validate both <response> and <extraction> tags are present.
    2. If malformed, return override_response with safe fallback.
    """
    has_response = bool(
        re.search(r"<response>\s*.+?\s*</response>", llm_response, re.DOTALL)
    )
    has_extraction = bool(
        re.search(r"<extraction>\s*.+?\s*</extraction>", llm_response, re.DOTALL)
    )

    if not has_response or not has_extraction:
        return PolicyResult(
            allowed=False,
            override_response=_MALFORMED_RESPONSE,
            crisis_detected=False,
        )

    return PolicyResult(allowed=True, override_response=None, crisis_detected=False)
