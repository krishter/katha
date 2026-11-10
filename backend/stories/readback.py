"""
The read-back of a Story (docs/specs/STORIES.md §2.1): what Katha will say
aloud to her, in her language, before anything reaches the family.

The family reads the English body; she approves the read-back. So the two
are checked against each other, sentence by sentence, both ways: nothing in
the read-back that the body does not say, and nothing in the body left out
of the read-back (TC-43). One rewrite with the problems named, as for cards;
still failing, the Story stays with the operator.
"""

from __future__ import annotations

import logging
from typing import Awaitable, Callable, Optional

from prompts.system_prompt import _language_name

logger = logging.getLogger(__name__)

WRITE_MAX_TOKENS = 6000
CHECK_MAX_TOKENS = 6000
MAX_PASSES = 2

STEP_WRITE = "readback"
STEP_CHECK = "readback_check"

# (prompt, step, max_tokens) -> parsed JSON
Ask = Callable[[str, str, int], Awaitable[dict]]


def build_write_prompt(
    *,
    title: str,
    body: str,
    quotes: list[dict],
    language_name: str,
    parent_title: str,
    child_name: str,
    previous: Optional[str] = None,
    problems: Optional[list[str]] = None,
) -> str:
    fix_block = ""
    if problems:
        listed = "\n".join(f"- {p}" for p in problems)
        fix_block = f"""
A CHECK OF YOUR PREVIOUS READ-BACK FOUND PROBLEMS. Rewrite it so that every \
one is fixed, changing nothing else that was fine.

PREVIOUS READ-BACK
{previous or ""}

PROBLEMS
{listed}
"""
    rendered_quotes = (
        "\n".join(f"- {q.get('original', '')}" for q in quotes if q.get("original"))
        or "(none)"
    )
    return f"""Katha is about to read a story back to {parent_title}, an \
elderly storyteller, as one voice note in {language_name}. The story was \
written in English from what {parent_title} told Katha. {parent_title} must \
hear exactly what their family will read, and agree, before it is shared \
with {child_name}.

Write what Katha will say:
1. One short line: this is the story written down from what they told you, \
in their own words.
2. The STORY, all of it, in {language_name}, still in the first person, as \
{parent_title} telling it. Translate it faithfully: every sentence, in the \
same order. Add nothing, leave nothing out, soften nothing. Where it uses \
one of the ORIGINAL WORDS below, use those words exactly as written.
3. The question: is this right, and may Katha share it with {child_name}?

All of it in {language_name} (unless {language_name} is English). Plain \
spoken sentences, no title, no labels, no emoji.
{fix_block}
TITLE: {title}

STORY (English)
{body}

ORIGINAL WORDS (keep exactly)
{rendered_quotes}

Respond with ONLY this JSON object:
{{"readback_text": ""}}"""


def build_check_prompt(*, body: str, readback: str, language_name: str) -> str:
    return f"""You are checking a read-back against the story it was \
translated from. The storyteller will approve the read-back in \
{language_name}; their family will read the English story. They must say \
the same thing. Be strict and literal.

Split the READ-BACK into sentences and classify EVERY one:
- "framing": the opening line saying this is their story, or the closing \
question asking whether it is right and may be shared.
- "supported": it says what the STORY says, translated. Rewording is fine; \
a changed fact, person, place, number, feeling or certainty is not.
- "unsupported": it says something the STORY does not, or says it \
differently enough to change its meaning.

Then list every sentence of the STORY whose content is missing from the \
read-back, or changed in it.

STORY (English)
{body}

READ-BACK ({language_name})
{readback}

Respond with ONLY this JSON object:
{{"sentences": [{{"text": "", "verdict": "framing|supported|unsupported", \
"reason": ""}}],
  "missing_from_readback": [""]}}"""


def problems_in(checked: dict) -> list[str]:
    if not checked.get("sentences"):
        return ["the check returned no sentences"]
    problems = [
        f'adds or changes: "{s.get("text", "")}" ({s.get("reason", "")})'
        for s in checked["sentences"]
        if s.get("verdict") not in ("framing", "supported")
    ]
    problems += [
        f'leaves out: "{m}"' for m in checked.get("missing_from_readback") or [] if m
    ]
    return problems


async def write_readback(
    *,
    title: str,
    body: str,
    quotes: list[dict],
    language: str,
    parent_title: str,
    child_name: str,
    ask: Ask,
) -> tuple[str, dict]:
    """Write and check the read-back. Returns (text, check record); the
    record's `result` is "passed" or "failed"."""
    language_name = _language_name(language or "en-IN")
    text = ""
    passes: list[dict] = []
    problems: list[str] = []
    for _ in range(MAX_PASSES):
        written = await ask(
            build_write_prompt(
                title=title,
                body=body,
                quotes=quotes,
                language_name=language_name,
                parent_title=parent_title,
                child_name=child_name,
                previous=text or None,
                problems=problems or None,
            ),
            STEP_WRITE,
            WRITE_MAX_TOKENS,
        )
        text = str(written.get("readback_text") or "").strip()
        if not text:
            problems = ["the read-back came back empty"]
            passes.append({"problems": problems})
            continue
        checked = await ask(
            build_check_prompt(body=body, readback=text, language_name=language_name),
            STEP_CHECK,
            CHECK_MAX_TOKENS,
        )
        problems = problems_in(checked)
        passes.append(
            {
                "problems": problems,
                "sentences": checked.get("sentences") or [],
                "missing_from_readback": checked.get("missing_from_readback") or [],
            }
        )
        if not problems:
            return text, {"result": "passed", "language": language, "passes": passes}
    logger.info("Read-back still failing after %d passes", MAX_PASSES)
    return text, {"result": "failed", "language": language, "passes": passes}
