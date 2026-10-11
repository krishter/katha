"""
The three consolidation prompts (§6.3).

Each one is deliberately narrow. The grouping call decides *what belongs
together*, the compose call decides *how it reads*, and the fidelity call
decides *whether it is true to what was said*. Keeping them apart is what
makes the third able to catch the second: a single call asked to compose
and self-check grades its own work.
"""

from __future__ import annotations

import json

from consolidation.arc import REQUIRED_FOR_STORY

# §6.2: bounded so the prompt stays a fixed size as history grows. Same
# starvation trade-off as Layer 3 retrieval (embedding-strategy.md) —
# older threads fall out of view, and raising this is the cheap stopgap
# until semantic retrieval lands.
MAX_OPEN_THREADS = 15


def _render_turns(turns: list[dict]) -> str:
    """
    Transcripts with their ids, which the model must cite back.

    Katha's own replies are included and explicitly labelled. §5.1 rule 5
    keeps her out of the story, but her question is often the only thing
    that makes an answer intelligible ("Yes, about ten" means nothing
    without "How old were you?"). Labelling lets the model read them as
    context while knowing they are not source material.
    """
    blocks = []
    for turn in turns:
        blocks.append(
            f"[turn_id: {turn['id']}] (session {turn.get('session_number', '?')}, "
            f"{turn.get('detected_language') or 'unknown language'})\n"
            f"  THEY SAID: {turn['transcript']}\n"
            f"  (Katha asked: {turn.get('response_text', '')})"
        )
    return "\n\n".join(blocks)


def _render_atoms(atoms: list[dict]) -> str:
    lines = []
    for atom in atoms:
        lines.append(
            f"- [atom_id: {atom['id']}] (from turn {atom.get('turn_id')}, "
            f"domain {atom.get('domain')}) {atom.get('narrative', '')}"
            f" | who={atom.get('who') or []}"
            f" when={atom.get('when_approx')} where={atom.get('where_approx')}"
        )
    return "\n".join(lines) if lines else "(none)"


def _render_open_threads(threads: list[dict]) -> str:
    if not threads:
        return "(none — this user has no incomplete incidents on record)"
    lines = []
    for thread in threads[:MAX_OPEN_THREADS]:
        missing = ", ".join(thread.get("missing", [])) or "nothing"
        lines.append(
            f'- [thread_id: {thread["id"]}] "{thread.get("title", "")}" '
            f"— has: {', '.join(thread.get('present', [])) or 'nothing'}; "
            f"still missing: {missing}"
        )
    return "\n".join(lines)


def _render_people(people: list[dict]) -> str:
    if not people:
        return "(none on record)"
    return "\n".join(
        f"- {p.get('name', '')} ({p.get('relationship', '')})" for p in people
    )


def build_grouping_prompt(
    *,
    turns: list[dict],
    atoms: list[dict],
    open_threads: list[dict],
    significant_people: list[dict],
) -> str:
    """Step 1 (§6.3): group this session's material by incident and assess
    each incident's arc."""
    required = " + ".join(REQUIRED_FOR_STORY)
    return f"""You are Katha's consolidation pass. You are given one \
conversation session with an elderly storyteller: the transcripts of what \
they said, and the per-turn fragments ("atoms") already extracted from them.

Your job is to work out WHICH INCIDENTS they were telling, and for each one, \
how much of a complete account you actually have.

An INCIDENT is one thing that happened: a flood, a first day at work, the \
day they met their spouse. It is not a topic and not a description. Some \
things people say are vivid but are not incidents — a description of a \
kitchen, a portrait of a grandmother. Those are NOT incidents. Leave them \
out rather than forcing them into a story shape.

An incident's account has up to four arc elements:
- setting: where and when, and what life was like then
- event: what happened
- turn: the peak — the decision, surprise, or moment that mattered
- resolution: how it ended, or what it meant to them

Rules you must follow:
1. An element counts as present ONLY if the person actually said it. If you \
inferred it, or it is implied but unsaid, it is NOT present. This is the \
most important rule here.
2. Every element you mark present must list the turn_id(s) it came from.
3. Each atom belongs to AT MOST ONE incident. Atoms that belong to no \
incident (detours, descriptions, chit-chat) go in `unassigned_atom_ids`.
4. If an incident continues one of the OPEN THREADS below, set \
`extends_thread_id` to that thread's id. Otherwise set it to null.
5. The same person may be referred to differently across turns ("Appa", \
"my father", by name). Treat those as one person.

CONVERSATION TRANSCRIPTS
{_render_turns(turns)}

ATOMS ALREADY EXTRACTED FROM THIS SESSION
{_render_atoms(atoms)}

OPEN THREADS FROM EARLIER SESSIONS (incidents already part-told)
{_render_open_threads(open_threads)}

PEOPLE KNOWN TO MATTER TO THIS STORYTELLER
{_render_people(significant_people)}

Respond with ONLY this JSON object and nothing else:

{{
  "incidents": [
    {{
      "working_title": "short descriptive label",
      "atom_ids": ["..."],
      "extends_thread_id": null,
      "domain": "the life domain this belongs to",
      "when_approx": "as they said it, e.g. 'when I was ten'",
      "people": ["..."],
      "places": ["..."],
      "arc": {{
        "setting":    {{"present": true, "summary": "...", "turn_ids": ["..."]}},
        "event":      {{"present": true, "summary": "...", "turn_ids": ["..."]}},
        "turn":       {{"present": false, "summary": "", "turn_ids": []}},
        "resolution": {{"present": false, "summary": "", "turn_ids": []}}
      }}
    }}
  ],
  "unassigned_atom_ids": ["..."]
}}

A Story needs at least {required}. Do not inflate an element to reach that \
bar — an honest incomplete incident is useful, and a fabricated one is not."""


def build_compose_prompt(
    *,
    incident: dict,
    turns: list[dict],
    source_language: str,
) -> str:
    """Step 2 (§6.3): write the first-person story, per §5.1 and §5.2."""
    return f"""You are writing one story the way the storyteller told it, \
for their family to keep.

You are given the RAW TRANSCRIPTS of what they said. Write the story in \
FIRST PERSON, as them: "I was maybe ten when the river came into our \
house...", never "He recalls that...".

These rules are not style preferences. Their children will read these \
sentences as their parent's words, so:

1. SOURCE IS THE TRANSCRIPTS BELOW. Nothing else.
2. THEIR WORDS FIRST. Keep their phrasing, their order of telling where it \
works, their idioms and their code-mixed words (ammachi, thinnai, "that \
fellow"). Edit for flow by CUTTING AND JOINING, not by rewriting.
3. ADD NOTHING THEY DID NOT SAY. No invented dialogue, sensory detail, \
emotion, motive or date. If they did not say they were frightened, the \
story does not say it. Connecting words ("Later that night", "So") are \
allowed. New facts are not.
4. UNCERTAINTY STAYS UNCERTAIN. "I think it was 1962, or maybe '63" is not \
tidied into "In 1962".
5. KATHA IS NOT IN THE STORY. Her questions are context for understanding \
the answers. Never quote or paraphrase her.
6. LENGTH FOLLOWS WHAT WAS SAID. Four short voice notes become a short \
story. Do not pad.
7. TITLE IN THEIR WORDS where a phrase of theirs works ("The night the \
river came in"). Otherwise a plain descriptive title.

LANGUAGE. They were speaking {source_language}. Write the story BODY in \
English — it will be labelled to the family as a translation, not as a \
transcript. Separately, pick 1-3 QUOTES that are worth keeping in their own \
words: give each one exactly as they said it, in the original language and \
script, with a plain English gloss. Do not translate a quote into the body \
and also list it as a quote; the body is English prose, the quotes are \
theirs.

WHAT THIS INCIDENT IS
{
        json.dumps(
            {
                k: incident.get(k)
                for k in ("working_title", "when_approx", "people", "places")
            },
            ensure_ascii=False,
            indent=2,
        )
    }

THE TRANSCRIPTS (the only source)
{_render_turns(turns)}

Respond with ONLY this JSON object and nothing else:

{{
  "title": "...",
  "body_first_person": "the story, in their voice, in English",
  "quotes": [
    {{"original": "exactly as said, original script", "gloss": "English", \
"turn_id": "..."}}
  ],
  "when_approx": "as they said it",
  "sort_year": null,
  "people": ["..."],
  "places": ["..."]
}}

`sort_year` is your best numeric estimate of when this happened, for \
ordering a timeline, or null if you cannot tell from what they said. It is \
never shown to anyone as a fact."""


def build_fidelity_prompt(*, body: str, turns: list[dict]) -> str:
    """Step 3 (§5.3): sentence-by-sentence check of the draft against source."""
    return f"""You are checking a draft story against the transcripts it was \
written from. This is the check that stops us putting words in a real \
person's mouth, so be strict and be literal.

Split the DRAFT into sentences. Classify EVERY sentence as exactly one of:

- "supported": everything it asserts was actually said in the transcripts. \
Rewording is fine. Translation from the original language is fine.
- "connective": it asserts no new fact. Pure joining or sequencing \
("Later that night.", "So we waited."). If it carries any fact of its own, \
it is not connective.
- "unsupported": it asserts something the transcripts do not contain — an \
invented detail, emotion, motive, date, name, or an inference presented as \
something they said.

When you are unsure between supported and unsupported, choose unsupported. \
A story that loses a good sentence is a small cost; a story containing a \
sentence their parent never said is the thing we must never ship.

DRAFT
{body}

TRANSCRIPTS (the only source of truth)
{_render_turns(turns)}

Respond with ONLY this JSON object and nothing else:

{{
  "sentences": [
    {{"text": "the sentence verbatim from the draft",
      "verdict": "supported",
      "turn_ids": ["turns that support it; empty for connective"],
      "reason": "one short clause, only if unsupported"}}
  ]
}}"""
