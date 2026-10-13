"""
The consolidation pass itself (§6.3), with the LLM injected.

Phase A drives this from a script over a prod restore; Phase B will drive
it from close_and_process_session. Neither the grouping nor the composing
knows which, and nothing here writes to the database.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Optional

from consolidation.arc import REQUIRED_FOR_STORY, Arc
from consolidation.prompts import (
    build_compose_prompt,
    build_fidelity_prompt,
    build_grouping_prompt,
)

logger = logging.getLogger(__name__)

GROUPING_MAX_TOKENS = 4000
COMPOSE_MAX_TOKENS = 2000
FIDELITY_MAX_TOKENS = 4000

STEP_GROUP = "group"
STEP_COMPOSE = "compose"
STEP_FIDELITY = "fidelity"

# A draft that still has unsupported sentences after one removal pass is
# not retried again. §5.3 allows one re-check; beyond that the model is
# arguing with itself and the honest outcome is to keep the incident as a
# thread.
_MAX_FIDELITY_PASSES = 2

# LLM call: (prompt, step, max_tokens) -> (text, input_tokens, output_tokens)
LLMCall = Callable[..., Awaitable[tuple[str, int, int]]]


@dataclass
class TokenUsage:
    """Per-step token accounting.

    §6.3 requires per-session cost to be measured in the self-trial and
    added to unit economics alongside the Sarvam costs, so this is a
    first-class output of the run, not a log line.
    """

    calls: int = 0
    input_tokens: int = 0
    output_tokens: int = 0

    def add(self, input_tokens: int, output_tokens: int) -> None:
        self.calls += 1
        self.input_tokens += input_tokens
        self.output_tokens += output_tokens

    def to_json(self) -> dict:
        return {
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


@dataclass
class Usage:
    by_step: dict[str, TokenUsage] = field(
        default_factory=lambda: {
            STEP_GROUP: TokenUsage(),
            STEP_COMPOSE: TokenUsage(),
            STEP_FIDELITY: TokenUsage(),
        }
    )

    def add(self, step: str, input_tokens: int, output_tokens: int) -> None:
        self.by_step.setdefault(step, TokenUsage()).add(input_tokens, output_tokens)

    def merge(self, other: "Usage") -> None:
        for step, usage in other.by_step.items():
            mine = self.by_step.setdefault(step, TokenUsage())
            mine.calls += usage.calls
            mine.input_tokens += usage.input_tokens
            mine.output_tokens += usage.output_tokens

    @property
    def totals(self) -> TokenUsage:
        total = TokenUsage()
        for usage in self.by_step.values():
            total.calls += usage.calls
            total.input_tokens += usage.input_tokens
            total.output_tokens += usage.output_tokens
        return total

    def to_json(self) -> dict:
        return {
            "by_step": {k: v.to_json() for k, v in self.by_step.items()},
            "total": self.totals.to_json(),
        }


@dataclass
class Sentence:
    text: str
    verdict: str
    turn_ids: list[str] = field(default_factory=list)
    reason: str = ""


@dataclass
class Thread:
    """One incident, part-told or complete. A Story is a Thread whose arc
    is complete and whose draft survived the fidelity check — the same
    shape, which is why §8 makes it one table with a status."""

    id: str
    title: str
    domain: str = ""
    when_approx: str = ""
    arc: Arc = field(default_factory=Arc)
    atom_ids: list[str] = field(default_factory=list)
    people: list[str] = field(default_factory=list)
    places: list[str] = field(default_factory=list)
    first_seen_session: Optional[int] = None
    last_seen_session: Optional[int] = None
    sessions_seen: list[int] = field(default_factory=list)

    status: str = "thread"
    body_first_person: Optional[str] = None
    quotes: list[dict] = field(default_factory=list)
    sentence_sources: list[dict] = field(default_factory=list)
    fidelity_status: Optional[str] = None
    removed_sentences: list[dict] = field(default_factory=list)
    source_language: str = ""
    notes: list[str] = field(default_factory=list)

    def to_json(self) -> dict:
        return {
            "id": self.id,
            "status": self.status,
            "title": self.title,
            "domain": self.domain,
            "when_approx": self.when_approx,
            "arc": self.arc.to_json(),
            "arc_present": sorted(self.arc.evidenced),
            "arc_missing": self.arc.missing,
            "atom_ids": self.atom_ids,
            "people": self.people,
            "places": self.places,
            "sessions_seen": self.sessions_seen,
            "source_turn_ids": self.arc.source_turn_ids,
            "body_first_person": self.body_first_person,
            "quotes": self.quotes,
            "sentence_sources": self.sentence_sources,
            "removed_sentences": self.removed_sentences,
            "fidelity_status": self.fidelity_status,
            "source_language": self.source_language,
            "notes": self.notes,
        }


@dataclass
class SessionResult:
    session_id: str
    session_number: int
    incidents_found: int = 0
    promoted_thread_ids: list[str] = field(default_factory=list)
    unassigned_atom_ids: list[str] = field(default_factory=list)
    usage: Usage = field(default_factory=Usage)
    errors: list[str] = field(default_factory=list)


def parse_json_object(raw: str) -> dict:
    """
    Pull the JSON object out of a model reply.

    Tolerates a ```json fence and surrounding prose, because the cost of
    being strict here is throwing away a good grouping over a code fence.
    Returns {} when there is nothing parseable — callers treat that as "no
    incidents", never as an exception, since this runs off the critical
    path in Phase B.
    """
    text = raw.strip()
    fenced = re.search(r"```(?:json)?\s*(.+?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        return {}
    try:
        parsed = json.loads(text[start : end + 1])
    except json.JSONDecodeError:
        logger.warning("Consolidation reply was not parseable JSON")
        return {}
    return parsed if isinstance(parsed, dict) else {}


def split_sentences(body: str) -> list[str]:
    """Rough sentence split, used only to sanity-check the fidelity reply
    against the draft. The authoritative split is the model's own."""
    return [s.strip() for s in re.split(r"(?<=[.!?])\s+", body.strip()) if s.strip()]


def _turns_by_id(turns: list[dict]) -> dict[str, dict]:
    return {str(t["id"]): t for t in turns}


def select_source_turns(arc: Arc, all_turns: list[dict]) -> list[dict]:
    """
    The transcripts the compose step reads: every turn evidencing any arc
    element, in the order they were spoken.

    §5.1 rule 1 — compose from transcripts, not atom summaries, because an
    atom is already a lossy third-person summary and writing first person
    from one means paraphrasing a paraphrase.
    """
    wanted = set(arc.source_turn_ids)
    return [t for t in all_turns if str(t["id"]) in wanted]


def arc_still_holds(arc: Arc, surviving_turn_ids: set[str]) -> bool:
    """
    After unsupported sentences are cut, does the body still tell the arc?

    §5.3 says a draft whose arc no longer holds goes back to Thread, but
    not how to decide that. The rule used here: each required element must
    still be carried by at least one surviving sentence, i.e. at least one
    of the element's turn ids is cited by a sentence that stayed. A body
    whose only "turn" sentence was invented has lost its peak, whatever
    the grouping call originally claimed.
    """
    for name in REQUIRED_FOR_STORY:
        element = arc.element(name)
        if not set(element.turn_ids) & surviving_turn_ids:
            return False
    return True


async def _call(
    call_llm: LLMCall, prompt: str, step: str, max_tokens: int, usage: Usage
) -> dict:
    text, input_tokens, output_tokens = await call_llm(
        prompt, step=step, max_tokens=max_tokens
    )
    usage.add(step, input_tokens, output_tokens)
    return parse_json_object(text)


async def compose_and_check(
    *,
    incident: dict,
    thread: Thread,
    all_turns: list[dict],
    call_llm: LLMCall,
    usage: Usage,
) -> Thread:
    """
    Steps 2 and 3 for one promoted incident: compose, then check sentence
    by sentence, cutting anything unsupported and re-checking once.
    """
    source_turns = select_source_turns(thread.arc, all_turns)
    if not source_turns:
        thread.notes.append("no source transcripts for the arc's turn ids")
        return thread

    language = source_turns[0].get("detected_language") or "unknown"
    thread.source_language = language

    composed = await _call(
        call_llm,
        build_compose_prompt(
            incident=incident, turns=source_turns, source_language=language
        ),
        STEP_COMPOSE,
        COMPOSE_MAX_TOKENS,
        usage,
    )
    body = (composed.get("body_first_person") or "").strip()
    if not body:
        thread.notes.append("compose returned no body")
        return thread

    thread.title = composed.get("title") or thread.title
    thread.quotes = composed.get("quotes") or []
    thread.when_approx = composed.get("when_approx") or thread.when_approx
    thread.people = composed.get("people") or thread.people
    thread.places = composed.get("places") or thread.places

    revised = False
    for _ in range(_MAX_FIDELITY_PASSES):
        checked = await _call(
            call_llm,
            build_fidelity_prompt(body=body, turns=source_turns),
            STEP_FIDELITY,
            FIDELITY_MAX_TOKENS,
            usage,
        )
        sentences = [
            Sentence(
                text=str(s.get("text", "")).strip(),
                verdict=str(s.get("verdict", "unsupported")),
                turn_ids=[str(t) for t in (s.get("turn_ids") or [])],
                reason=str(s.get("reason", "")),
            )
            for s in (checked.get("sentences") or [])
            if str(s.get("text", "")).strip()
        ]
        if not sentences:
            thread.notes.append("fidelity check returned no sentences")
            thread.fidelity_status = "failed"
            return thread

        unsupported = [s for s in sentences if s.verdict == "unsupported"]
        kept = [s for s in sentences if s.verdict != "unsupported"]

        if not unsupported:
            surviving = {t for s in kept for t in s.turn_ids}
            if not arc_still_holds(thread.arc, surviving):
                thread.fidelity_status = "failed"
                thread.notes.append(
                    "arc no longer evidenced by surviving sentences — kept as thread"
                )
                return thread
            thread.body_first_person = " ".join(s.text for s in kept)
            thread.sentence_sources = [
                {"text": s.text, "verdict": s.verdict, "turn_ids": s.turn_ids}
                for s in kept
            ]
            thread.fidelity_status = "revised" if revised else "passed"
            thread.status = "story"
            return thread

        # Cut what was not said and check the remainder once more.
        thread.removed_sentences.extend(
            {"text": s.text, "reason": s.reason} for s in unsupported
        )
        revised = True
        body = " ".join(s.text for s in kept).strip()
        if not body:
            thread.fidelity_status = "failed"
            thread.notes.append("every sentence was unsupported")
            return thread

    thread.fidelity_status = "failed"
    thread.notes.append("still unsupported after one revision — kept as thread")
    return thread


async def consolidate_session(
    *,
    session_id: str,
    session_number: int,
    turns: list[dict],
    atoms: list[dict],
    threads: dict[str, Thread],
    significant_people: list[dict],
    call_llm: LLMCall,
    history_turns: Optional[list[dict]] = None,
) -> SessionResult:
    """
    Run the three steps over one session, updating `threads` in place.

    `threads` is the user's running thread state, carried across sessions
    by the caller. That carry-forward is the whole point of §6.2: a
    session that finally supplies the ending completes a thread opened
    last week, and composing it needs transcripts from both — which is why
    `history_turns` holds every turn seen so far, not just this session's.
    """
    result = SessionResult(session_id=session_id, session_number=session_number)
    all_turns = list(history_turns or turns)

    open_threads = [
        {
            "id": t.id,
            "title": t.title,
            "present": sorted(t.arc.evidenced),
            "missing": t.arc.missing,
        }
        for t in threads.values()
        if t.status == "thread"
    ]
    # Most recently touched first, so the cap in the prompt drops the
    # coldest threads rather than an arbitrary set.
    open_threads.sort(
        key=lambda t: next(
            (x.last_seen_session or 0 for x in threads.values() if x.id == t["id"]), 0
        ),
        reverse=True,
    )

    grouped = await _call(
        call_llm,
        build_grouping_prompt(
            turns=turns,
            atoms=atoms,
            open_threads=open_threads,
            significant_people=significant_people,
        ),
        STEP_GROUP,
        GROUPING_MAX_TOKENS,
        result.usage,
    )

    incidents = grouped.get("incidents") or []
    if not isinstance(incidents, list):
        result.errors.append("grouping returned a non-list `incidents`")
        return result
    result.unassigned_atom_ids = [
        str(a) for a in (grouped.get("unassigned_atom_ids") or [])
    ]
    result.incidents_found = len(incidents)

    known_turn_ids = set(_turns_by_id(all_turns))

    for index, incident in enumerate(incidents):
        if not isinstance(incident, dict):
            continue
        arc = Arc.from_json(incident.get("arc"))

        # Drop citations to turns that do not exist. A hallucinated turn id
        # would otherwise prop up an arc element with no transcript behind
        # it, which is exactly what §4 forbids.
        for name in ("setting", "event", "turn", "resolution"):
            element = arc.element(name)
            element.turn_ids = [t for t in element.turn_ids if t in known_turn_ids]

        extends = incident.get("extends_thread_id")
        thread = threads.get(str(extends)) if extends else None

        if thread is None:
            thread = Thread(
                id=f"{session_id}:{index}",
                title=str(incident.get("working_title") or "untitled"),
                domain=str(incident.get("domain") or ""),
                when_approx=str(incident.get("when_approx") or ""),
                arc=arc,
                first_seen_session=session_number,
            )
            threads[thread.id] = thread
        else:
            thread.arc = thread.arc.merged_with(arc)

        thread.last_seen_session = session_number
        if session_number not in thread.sessions_seen:
            thread.sessions_seen.append(session_number)
        for atom_id in incident.get("atom_ids") or []:
            if str(atom_id) not in thread.atom_ids:
                thread.atom_ids.append(str(atom_id))
        for key in ("people", "places"):
            for value in incident.get(key) or []:
                existing = getattr(thread, key)
                if value not in existing:
                    existing.append(value)

        # Already a Story from an earlier session and nothing new to say.
        if thread.status == "story":
            continue

        if not thread.arc.is_complete:
            continue

        await compose_and_check(
            incident=incident,
            thread=thread,
            all_turns=all_turns,
            call_llm=call_llm,
            usage=result.usage,
        )
        if thread.status == "story":
            result.promoted_thread_ids.append(thread.id)

    return result
