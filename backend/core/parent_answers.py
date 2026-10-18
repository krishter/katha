"""
What she meant by her answer to an ask (docs/specs/CONVERSATION_CARDS.md §9.3).

Three questions get a labelled answer: the offer to read a card ("shall I
read it to you?"), the read-back itself ("shall I send this to Priya?"),
and the sharing question ("would you like me to share some of these with
Priya?"). Her reply can be in any language, code-mixed, and is often not
an answer at all.

The rule is core/parent_consent.py's: when in doubt, it is not a yes.
Getting this wrong in the permissive direction sends her words to her
child without her agreement, which is the one thing cards must never do.
So every failure, from a timeout to a label the model invents, lands on
the label that sends nothing, and a positive label stands only if the
words it relied on are really in what she said.
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

OFFER = "offer"
READBACK = "readback"
SHARE = "share"

LABELS: dict[str, tuple[str, ...]] = {
    OFFER: ("yes", "not_now", "other"),
    READBACK: ("approve", "correction", "keep_private", "not_now", "unclear"),
    SHARE: ("yes", "no", "unclear"),
}

# What each kind falls back to: the label that sends and changes nothing.
SAFE: dict[str, str] = {OFFER: "other", READBACK: "unclear", SHARE: "unclear"}

# Labels that move something forward. Each must quote her.
POSITIVE = frozenset({"yes", "approve"})

_MAX_TOKENS = 300
_PURPOSE = "parent_answer_classify"

_GUIDE: dict[str, str] = {
    OFFER: """Katha offered to read her something it wrote from her stories.
- "yes": she clearly wants to hear it now ("yes, read it", "haan, sunao",
  "சரி, படிங்க").
- "not_now": she declines or defers ("later", "not today", "abhi nahi").
- "other": anything else — she carries on with a story, changes the
  subject, answers something different, or it is unclear.""",
    READBACK: """Katha read her a short card it wrote from her stories and asked
whether to send it to her child.
- "approve": she clearly agrees to sending THIS card ("yes, send it",
  "that's right, send", "haan bhej do", "அனுப்புங்க").
- "correction": she says something in it is wrong or changes a fact ("it
  was my uncle, not my father", "it was 1962"), even if she also says yes.
- "keep_private": she does not want it sent ("don't send", "keep it",
  "this is just for me", "mat bhejo").
- "not_now": she wants to decide later.
- "unclear": anything else, including "hmm", "okay okay" while carrying on
  with something else, a laugh, or praise without agreeing to send.""",
    SHARE: """Katha asked whether she would like some of her stories shared with
her child, always reading them to her first.
- "yes": she clearly agrees to sharing ("yes, share", "haan, zaroor").
- "no": she does not want her stories shared.
- "unclear": anything else, including changing the subject.""",
}


@dataclass(frozen=True)
class Classification:
    kind: str
    label: str
    # The words of hers the label rests on; empty when it rests on nothing.
    span: str


def _normalise(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().casefold()


def build_prompt(kind: str, question: str, transcript: str) -> str:
    labels = ", ".join(f'"{label}"' for label in LABELS[kind])
    return f"""You label an elderly person's spoken reply to one question. The \
reply was transcribed from a voice note and may be in any Indian language, in \
English, or a mix.

{_GUIDE[kind]}

Be conservative. Only an explicit answer to THIS question counts. If she \
says both yes and no, or you are not sure, do not choose "yes" or \
"approve". Choosing a cautious label costs one more question; wrongly \
choosing "yes" or "approve" sends her words to her family without her \
agreement.

QUESTION KATHA ASKED
{question}

HER REPLY
{transcript}

Respond with ONLY this JSON object:
{{"label": one of {labels},
  "span": "the exact words from her reply your label rests on, copied \
character for character in the original script; empty if none"}}"""


async def classify(kind: str, question: str, transcript: str) -> Classification:
    """Label her reply. Never raises; any failure is the safe label."""
    if kind not in LABELS:
        raise ValueError(f"unknown ask kind {kind!r}")
    safe = Classification(kind, SAFE[kind], "")
    if not transcript or not transcript.strip():
        return safe

    try:
        response = await llm.chat(
            [Message(role="user", content=build_prompt(kind, question, transcript))],
            max_tokens=_MAX_TOKENS,
            model=settings.LLM_CLASSIFIER_MODEL,
            purpose=_PURPOSE,
        )
    except Exception:
        logger.exception("parent_answers: classifier call failed (%s)", kind)
        return safe

    parsed = parse_json_object(response.content)
    label = str(parsed.get("label", "")).strip()
    span = str(parsed.get("span", "") or "").strip()

    if label not in LABELS[kind]:
        logger.warning("parent_answers: unknown label %r for %s", label, kind)
        return safe
    quoted = bool(span) and _normalise(span) in _normalise(transcript)
    if label in POSITIVE and not quoted:
        # A yes that cannot point at her words is the model's yes, not hers.
        logger.info(
            "parent_answers: %s %r without a quotable span — treated as %s",
            kind,
            label,
            SAFE[kind],
        )
        return safe
    return Classification(kind, label, span)
