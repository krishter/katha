"""
Screening what a child sends back about a card (docs/specs/CONVERSATION_CARDS.md
§10.2), before anything of it reaches her.

    ok          a question she can enjoy answering        -> queued
    sensitive   property, money, inheritance, health,     -> held for the
                old family conflicts                         operator
    correction  "it was Chennai, not Kumbakonam"          -> not relayed
    reaction    "I never knew this!", a heart             -> relayed as a
                                                             reaction
    other       support questions, "stop", anything else  -> not relayed

When the call fails or the answer cannot be read, the question is held
for the operator: a person looks at it before she ever hears it. Holding
an ordinary question costs a day; relaying a painful one cannot be undone.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from adapters import llm
from adapters.llm import Message
from config import settings
from consolidation.pipeline import parse_json_object

logger = logging.getLogger(__name__)

LABELS = ("ok", "sensitive", "correction", "reaction", "other")
FALLBACK = "sensitive"

_PURPOSE = "question_screen"
# Headroom over the short JSON answer (see LLM_CLASSIFIER_EFFORT).
_MAX_TOKENS = 1024

# A bare opt-out, in the words people actually send. Checked before any
# model call so "stop" always works, even when the model is down.
_STOP_RE = re.compile(
    r"^\s*(stop|unsubscribe|band karo|बंद करो|nirutthu|நிறுத்து)\s*[.!]*\s*$",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Screening:
    label: str
    stop: bool = False


def is_stop(text: str) -> bool:
    return bool(_STOP_RE.match(text or ""))


def build_prompt(text: str, parent_title: str) -> str:
    return f"""An adult child received a short card about a memory their \
elderly parent ({parent_title}) shared, and sent this back. It may be in any \
language. Label it.

- "ok": a question for {parent_title} about her life or the memory that she \
could enjoy answering ("Which film was it?", "What was Ammachi like?").
- "sensitive": a question touching property, money, inheritance, wills, \
health or illness, death, or old family conflicts or quarrels — even if \
kindly meant.
- "correction": the child says something in the card is wrong, or gives \
their own version of a fact ("It was Chennai, not Kumbakonam").
- "reaction": a feeling about the card, with no question ("I never knew \
this!", "Love this", a heart).
- "other": anything else — a question for Katha, a support request, \
"stop", or unclear.

If a message is both a question and sensitive, it is "sensitive". If it \
both corrects and asks, it is "correction".

MESSAGE
{text}

Respond with ONLY this JSON object: {{"label": one of {", ".join(LABELS)}}}"""


async def screen(text: str, parent_title: str = "your parent") -> Screening:
    """Label a child's message. Never raises."""
    if is_stop(text):
        return Screening("other", stop=True)
    if not text or not text.strip():
        return Screening("other")
    try:
        response = await llm.chat(
            [Message(role="user", content=build_prompt(text, parent_title))],
            max_tokens=_MAX_TOKENS,
            model=settings.LLM_CLASSIFIER_MODEL,
            effort=settings.LLM_CLASSIFIER_EFFORT,
            purpose=_PURPOSE,
        )
    except Exception:
        logger.exception("question_screen: call failed — holding for the operator")
        return Screening(FALLBACK)
    label = str(parse_json_object(response.content).get("label", "")).strip()
    if label not in LABELS:
        logger.warning("question_screen: unreadable label %r — holding", label)
        return Screening(FALLBACK)
    return Screening(label)
