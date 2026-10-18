"""
The three card prompts (docs/specs/CONVERSATION_CARDS.md §7.2–§7.4).

Kept apart for the same reason as consolidation/prompts.py: the selection
call decides *what* is worth a card, the writing call *how it reads*, and
the fidelity call *whether it is true to what was said*. A call asked to
write and check its own work grades itself.

Every prompt reads the parent's raw transcripts, never the atom summaries
(Stories §5.1 rule 1). Katha's question before each answer is shown,
labelled, as context only: "Yes, about ten" means nothing without "How old
were you?", but Katha's words are never source material.

The prompts speak of "the parent" and use the pronouns the family gave,
or none: a father can be on Katha too, and guessing from a name is the
mistake Layer 1 is written to avoid.
"""

from __future__ import annotations

import json

# Writing limits (§7.3). Checked in code as well as asked for.
HOOK_MAX_WORDS = 30
EXCERPT_MAX_WORDS = 60
CALL_PROMPT_MAX_WORDS = 15


def render_turns(turns: list[dict]) -> str:
    blocks = []
    for turn in turns:
        asked = turn.get("katha_asked") or ""
        context = f"  (Katha had asked: {asked})\n" if asked else ""
        blocks.append(
            f"[turn_id: {turn['id']}] (conversation {turn.get('session_number', '?')}"
            f", {turn.get('date', '')}, "
            f"{turn.get('detected_language') or 'unknown language'})\n"
            f"{context}"
            f"  PARENT SAID: {turn['transcript']}"
        )
    return "\n\n".join(blocks)


def pronoun_rule(pronouns: str, parent_title: str) -> str:
    """How to refer to the parent: as the family wrote it, or, when they
    did not, without gendered pronouns at all (as Layer 1 does)."""
    if pronouns.strip():
        return f'Refer to {parent_title} with the pronouns "{pronouns.strip()}".'
    return (
        f'The parent\'s pronouns are not known: say "{parent_title}" and use no '
        "gendered pronouns for them."
    )


def _render_list(items: list[str], empty: str) -> str:
    return "\n".join(f"- {item}" for item in items) if items else empty


def build_selection_prompt(
    *,
    candidates: list[dict],
    turns: list[dict],
    private_topics: list[str],
    recent_titles: list[str],
    answered_questions: list[dict],
    parent_title: str,
    child_name: str,
    pronouns: str = "",
) -> str:
    rendered_candidates = "\n".join(
        f"- [atom_id: {c['id']}] (from turn {c['turn_id']}) {c.get('title') or ''}: "
        f"{c.get('narrative', '')}"
        for c in candidates
    )
    rendered_questions = _render_list(
        [
            f'[question_id: {q["id"]}] {child_name} asked: "{q["text"]}" '
            f"(raised with the parent in conversation {q.get('session_number', '?')})"
            for q in answered_questions
        ],
        "(none)",
    )
    return f"""You choose material for a "Did you know?" card: one surprising, \
true detail from an elderly parent's life that their adult child \
({child_name}, who calls them {parent_title}) will read before their next \
phone call, so that the child brings it up and the parent knows they were \
heard. {pronoun_rule(pronouns, parent_title)}

Pick ONE story moment from the candidates below, or none.

Prefer:
- What the child probably does not know: life before the child was born; \
{parent_title} as a young or mischievous person; firsts; risky decisions; \
people the child never met.
- An answer to a question the child asked (listed below). If a candidate \
answers one, prefer it and give the question's id.
- A concrete, vivid detail told in the parent's own words.

Never pick:
- Health, illness, money, property, inheritance, family conflict or \
quarrels, or grief told in pain. These can be beautiful stories, but they \
are the parent's to raise with their children, not a teaser's.
- Anything the parent framed as hard to talk about, or anything on their \
private topics below.
- The same subject as a recent card.

"none" is a good answer. No card is better than a weak card.

You may group atoms only if they are the same moment told across turns.

CANDIDATES
{rendered_candidates}

WHAT THE PARENT SAID (the source of every candidate)
{render_turns(turns)}

PRIVATE TOPICS (never pick anything touching these)
{_render_list(private_topics, "(none)")}

RECENT CARDS (do not repeat their subject)
{_render_list(recent_titles, "(none)")}

QUESTIONS THE CHILD ASKED THAT THE PARENT HAS SINCE BEEN ASKED
{rendered_questions}

Respond with ONLY this JSON object:
{{"atom_ids": ["chosen atom ids; empty for none"],
  "answers_question_id": "question id or null",
  "why": "one short sentence"}}"""


def build_writing_prompt(
    *,
    turns: list[dict],
    parent_title: str,
    child_name: str,
    language_name: str,
    pronouns: str = "",
    question: str = "",
    fixes: list[str] | None = None,
    previous: dict | None = None,
) -> str:
    question_block = ""
    if question:
        question_block = f"""
{child_name} had asked: "{question}". This card carries the parent's \
answer. The hook may open "You asked {parent_title} …" instead of "Did you \
know…".
"""
    fix_block = ""
    if fixes:
        fix_block = f"""
A CHECK OF YOUR PREVIOUS DRAFT FOUND PROBLEMS. Rewrite the card so that \
every one is fixed, changing nothing else that was fine.

PREVIOUS DRAFT
{json.dumps(previous or {}, ensure_ascii=False, indent=2)}

PROBLEMS
{_render_list(fixes, "(none)")}
"""
    return f"""You write a "Did you know?" card from an elderly parent's own \
words, for their adult child {child_name}, who calls them {parent_title}. \
The child reads it before their next call; it should make them want to ask \
about it. {parent_title} will hear it read back, in {language_name}, and \
must agree before it is sent. {pronoun_rule(pronouns, parent_title)}
{question_block}
"PARENT SAID" in the transcripts marks {parent_title}'s words. These are \
real words of a real person, and the child will read them as their \
parent's. So:
1. Use ONLY what the parent said in the transcripts. Add nothing: no \
invented detail, feeling, motive, date, name or dialogue. If they did not \
say they were scared, the card does not say it. Uncertainty stays \
uncertain ("I think it was 1962, or maybe '63").
2. The parent's words first. Keep their phrasing and idioms; edit by \
cutting and joining, not rewriting.
3. Katha is not in the card. Katha's questions are context; never quote or \
paraphrase them as the parent's.

FIELDS
- title: a few words, in the parent's words where possible ("The walk to \
the talkies").
- hook: ONE sentence, at most {HOOK_MAX_WORDS} words, starting "Did you \
know". Third person, calling the parent "{parent_title}". One concrete \
detail. Hold back the ending or the why, so the child has something to ask.
- excerpt: 1-3 sentences, at most {EXCERPT_MAX_WORDS} words, first person, \
cut and joined from what the parent said, translated into English where \
they spoke another language.
- original_quote: at most one short phrase exactly as the parent said it, \
in their own language and script, or null. Only if they spoke a language \
other than English.
- original_quote_gloss: its English meaning, or null.
- call_prompt: ONE open question the child can ask on the call, at most \
{CALL_PROMPT_MAX_WORDS} words, that the parent will enjoy answering at \
length. Never a quiz, never yes/no. It must not assume any fact the parent \
did not say.
- held_back_note: for the reviewer only: what the card leaves out and why.
- readback_text: the hook, the excerpt and the call prompt, in \
{language_name}, to be read aloud to the parent, keeping original_quote \
exactly as said. Plain spoken sentences, no labels.
{fix_block}
TRANSCRIPTS (the only source of truth)
{render_turns(turns)}

Respond with ONLY this JSON object:
{{"title": "", "hook": "", "excerpt": "", "original_quote": null,
  "original_quote_gloss": null, "call_prompt": "", "held_back_note": "",
  "readback_text": ""}}"""


def build_fidelity_prompt(*, card: dict, turns: list[dict], parent_title: str) -> str:
    """
    §7.4. The consolidation check (consolidation/prompts.build_fidelity_prompt)
    adapted to a card: the hook is third person and addressed to the child,
    so its framing is not a claim; the call prompt can presuppose a fact
    ("which film was it?" presupposes a film), so it is checked too; and
    the original-language quote must be the parent's own words.
    """
    parts = {
        "hook": card.get("hook", ""),
        "excerpt": card.get("excerpt", ""),
        "call_prompt": card.get("call_prompt", ""),
        "original_quote": card.get("original_quote") or "",
    }
    rendered = "\n".join(f"{name.upper()}: {text}" for name, text in parts.items())
    return f"""You are checking a short card against the transcripts it was \
written from. The child will read these as their parent's words, so be \
strict and be literal. "PARENT SAID" marks the parent's words.

Split HOOK, EXCERPT and CALL_PROMPT into sentences, and take ORIGINAL_QUOTE \
whole if present. Classify EVERY one:

- "supported": everything it asserts was actually said in the transcripts \
by the parent. Rewording and translation are fine. "Did you know" and \
calling the parent "{parent_title}" are framing, not claims. For \
CALL_PROMPT, a question is supported when everything it takes for granted \
was said.
- "connective": it asserts no fact of its own ("So we went.").
- "unsupported": it asserts or assumes something the transcripts do not \
contain — an invented detail, emotion, motive, date or name, something only \
Katha said, or an inference presented as the parent's. For ORIGINAL_QUOTE: \
unsupported unless the parent said those words.

When unsure between supported and unsupported, choose unsupported.

CARD
{rendered}

TRANSCRIPTS (the only source of truth)
{render_turns(turns)}

Respond with ONLY this JSON object:
{{"sentences": [
  {{"field": "hook|excerpt|call_prompt|original_quote",
    "text": "the sentence verbatim from the card",
    "verdict": "supported|connective|unsupported",
    "turn_ids": ["turns that support it"],
    "reason": "one short clause, only if unsupported"}}
]}}"""
