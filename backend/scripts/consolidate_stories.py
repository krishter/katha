#!/usr/bin/env python
"""
Phase A: run the consolidation pass offline and write a report for review.

    python scripts/consolidate_stories.py --user-id user_7f3a2b

Reads one user's sessions, turns and story atoms, runs the three steps of
docs/proposals/first-person-stories.md §6.3 over each session in order, and
writes a JSON record plus a Markdown report to --out-dir.

NOTHING IS WRITTEN TO THE DATABASE. The connection is put into read-only
mode before any query, so an accidental write fails loudly rather than
quietly changing pilot data. This is Phase A of §11: no schema change, no
production behaviour change, output reviewed by hand.

Run it against a restore of production, not against production itself.
The LLM calls are real and billed; that is the point, since the per-session
token cost is one of the things Phase A exists to measure.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from adapters import llm  # noqa: E402
from adapters.llm import Message  # noqa: E402
from consolidation.pipeline import (  # noqa: E402
    STEP_COMPOSE,
    STEP_FIDELITY,
    STEP_GROUP,
    SessionResult,
    Thread,
    Usage,
    consolidate_session,
)
from memory import fact_store  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.session import Session  # noqa: E402
from models.story_atom import StoryAtom  # noqa: E402
from models.turn import Turn  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("consolidate")

# The consent handshake is not a conversation and has nothing to consolidate.
_SKIP_STATUSES = {"consent"}


async def make_llm_call():
    """The real Sonnet call, shaped for the pipeline's injection point."""

    async def call(prompt: str, *, step: str, max_tokens: int):
        response = await llm.chat(
            [Message(role="user", content=prompt)],
            system=None,
            max_tokens=max_tokens,
        )
        if response.stop_reason == "max_tokens":
            # Worth seeing in Phase A: a truncated grouping silently loses
            # incidents, which would read as "consolidation found little"
            # when the real answer is "the budget was too small".
            logger.warning(
                "%s call hit max_tokens=%d — output is cut off", step, max_tokens
            )
        return response.content, response.input_tokens, response.output_tokens

    return call


async def load_user(db, user_id: str) -> list[dict]:
    """Every session for this user, oldest first, with turns and atoms."""
    session_rows = (
        (
            await db.execute(
                select(Session)
                .where(Session.user_id == user_id)
                .order_by(Session.started_at)
            )
        )
        .scalars()
        .all()
    )

    sessions = []
    for row in session_rows:
        if row.status in _SKIP_STATUSES:
            continue
        turns = (
            (
                await db.execute(
                    select(Turn)
                    .where(Turn.session_id == row.id)
                    .order_by(Turn.turn_number)
                )
            )
            .scalars()
            .all()
        )
        if not turns:
            # A session where every turn failed to process leaves no turn
            # rows at all. There is nothing to consolidate, and counting it
            # as a session that produced no Story would understate the
            # pass. Reported separately instead.
            sessions.append(
                {
                    "id": str(row.id),
                    "session_number": row.session_number,
                    "status": row.status,
                    "started_at": row.started_at.isoformat()
                    if row.started_at
                    else None,
                    "turns": [],
                    "atoms": [],
                    "empty": True,
                }
            )
            continue
        atoms = (
            (
                await db.execute(
                    select(StoryAtom)
                    .where(StoryAtom.session_id == row.id)
                    .order_by(StoryAtom.created_at)
                )
            )
            .scalars()
            .all()
        )
        sessions.append(
            {
                "id": str(row.id),
                "session_number": row.session_number,
                "status": row.status,
                "started_at": row.started_at.isoformat() if row.started_at else None,
                "empty": False,
                "turns": [
                    {
                        "id": str(t.id),
                        "session_number": row.session_number,
                        "turn_number": t.turn_number,
                        "transcript": t.transcript,
                        "response_text": t.response_text,
                        "detected_language": t.detected_language,
                    }
                    for t in turns
                ],
                "atoms": [
                    {
                        "id": str(a.id),
                        "turn_id": str(a.turn_id) if a.turn_id else None,
                        "domain": a.domain,
                        "narrative": a.narrative,
                        "who": list(a.who or []),
                        "when_approx": a.when_approx,
                        "where_approx": a.where_approx,
                        "completeness_score": a.completeness_score,
                    }
                    for a in atoms
                ],
            }
        )
    return sessions


def _render_markdown(
    user_id: str,
    sessions: list[dict],
    threads: dict[str, Thread],
    results: list[SessionResult],
    usage: Usage,
    rates: tuple[float, float] | None,
) -> str:
    stories = [t for t in threads.values() if t.status == "story"]
    open_threads = [t for t in threads.values() if t.status != "story"]
    spoken = [s for s in sessions if not s["empty"]]

    out = [
        f"# Consolidation review — `{user_id}`",
        "",
        f"Generated {datetime.now(timezone.utc).isoformat(timespec='seconds')}. "
        "Nothing was written to the database.",
        "",
        "## Summary",
        "",
        f"- Sessions with transcripts: **{len(spoken)}** "
        f"(+{len(sessions) - len(spoken)} with no turns recorded)",
        f"- Incidents found: **{sum(r.incidents_found for r in results)}**",
        f"- Stories (arc complete, fidelity passed): **{len(stories)}**",
        f"- Threads still open: **{len(open_threads)}**",
        "",
        "## Token cost",
        "",
        "| Step | Calls | Input | Output |",
        "|---|---:|---:|---:|",
    ]
    for step in (STEP_GROUP, STEP_COMPOSE, STEP_FIDELITY):
        u = usage.by_step.get(step)
        if u:
            out.append(
                f"| {step} | {u.calls} | {u.input_tokens:,} | {u.output_tokens:,} |"
            )
    total = usage.totals
    out.append(
        f"| **total** | **{total.calls}** | **{total.input_tokens:,}** "
        f"| **{total.output_tokens:,}** |"
    )
    if spoken:
        out += [
            "",
            f"Per session with transcripts: {total.input_tokens / len(spoken):,.0f} "
            f"in / {total.output_tokens / len(spoken):,.0f} out "
            f"over {total.calls / len(spoken):.1f} calls.",
        ]
    if rates:
        cost = (total.input_tokens / 1e6) * rates[0] + (
            total.output_tokens / 1e6
        ) * rates[1]
        out += [
            "",
            f"At the rates supplied: **{cost:.4f}** total"
            + (f", {cost / len(spoken):.4f} per session." if spoken else "."),
        ]

    out += ["", "## Stories", ""]
    if not stories:
        out.append("_None. Every incident is still missing a required arc element._")
    for story in sorted(stories, key=lambda t: t.sessions_seen):
        out += [
            f"### {story.title}",
            "",
            f"*{story.domain} · {story.when_approx or 'no date given'} · "
            f"told across session(s) "
            f"{', '.join(str(s) for s in story.sessions_seen)} · "
            f"fidelity: {story.fidelity_status}*",
            "",
            f"> In their words, translated from {story.source_language}.",
            "",
            story.body_first_person or "",
            "",
        ]
        if story.quotes:
            out.append("**In their own words**")
            out.append("")
            for q in story.quotes:
                out.append(f"- {q.get('original', '')}")
                out.append(f"  - _{q.get('gloss', '')}_")
            out.append("")
        if story.removed_sentences:
            out.append("**Cut by the fidelity check** (not said in any transcript)")
            out.append("")
            for cut in story.removed_sentences:
                out.append(f"- ~~{cut['text']}~~ — {cut.get('reason', '')}")
            out.append("")
        out += [
            "**Arc**",
            "",
            "| Element | Present | From turns |",
            "|---|---|---|",
        ]
        for name in ("setting", "event", "turn", "resolution"):
            el = story.arc.element(name)
            out.append(
                f"| {name} | {'yes' if el.is_evidenced else 'no'} | "
                f"{', '.join(el.turn_ids) or '—'} |"
            )
        out.append("")

    out += ["## Open threads", ""]
    if not open_threads:
        out.append("_None._")
    for thread in open_threads:
        present = ", ".join(sorted(thread.arc.evidenced)) or "nothing"
        out.append(
            f"- **{thread.title}** — has {present}; "
            f"missing **{', '.join(thread.arc.missing)}**"
            + (f" ({'; '.join(thread.notes)})" if thread.notes else "")
        )
    return "\n".join(out) + "\n"


async def run(args) -> int:
    rates = None
    if args.input_price is not None and args.output_price is not None:
        rates = (args.input_price, args.output_price)

    threads: dict[str, Thread] = {}
    results: list[SessionResult] = []
    usage = Usage()
    call_llm = await make_llm_call()

    async with AsyncSessionLocal() as db:
        # Belt and braces for "nothing is written": any INSERT/UPDATE from
        # here on raises instead of landing in pilot data.
        try:
            await db.execute(
                text("SET SESSION CHARACTERISTICS AS TRANSACTION READ ONLY")
            )
        except Exception:
            logger.warning("Could not set the session read-only; not writing anyway")

        sessions = await load_user(db, args.user_id)
        if not sessions:
            logger.error("No sessions for %s", args.user_id)
            return 1
        significant_people = await fact_store.get_significant_people(args.user_id, db)

    spoken = [s for s in sessions if not s["empty"]]
    logger.info(
        "%s: %d session(s), %d with transcripts",
        args.user_id,
        len(sessions),
        len(spoken),
    )

    history: list[dict] = []
    for session in spoken:
        if args.max_sessions and len(results) >= args.max_sessions:
            break
        history.extend(session["turns"])
        logger.info(
            "session %s (#%s): %d turns, %d atoms",
            session["id"][:8],
            session["session_number"],
            len(session["turns"]),
            len(session["atoms"]),
        )
        result = await consolidate_session(
            session_id=session["id"],
            session_number=session["session_number"],
            turns=session["turns"],
            atoms=session["atoms"],
            threads=threads,
            significant_people=significant_people,
            call_llm=call_llm,
            history_turns=history,
        )
        usage.merge(result.usage)
        results.append(result)
        for note in result.errors:
            logger.warning("session %s: %s", session["id"][:8], note)

    out_dir = pathlib.Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = out_dir / f"{args.user_id}-{stamp}"

    payload = {
        "user_id": args.user_id,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "sessions_total": len(sessions),
        "sessions_with_transcripts": len(spoken),
        "threads": [t.to_json() for t in threads.values()],
        "per_session": [
            {
                "session_id": r.session_id,
                "session_number": r.session_number,
                "incidents_found": r.incidents_found,
                "promoted": r.promoted_thread_ids,
                "unassigned_atom_ids": r.unassigned_atom_ids,
                "usage": r.usage.to_json(),
                "errors": r.errors,
            }
            for r in results
        ],
        "usage": usage.to_json(),
    }
    base.with_suffix(".json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False)
    )
    base.with_suffix(".md").write_text(
        _render_markdown(args.user_id, sessions, threads, results, usage, rates)
    )

    stories = sum(1 for t in threads.values() if t.status == "story")
    logger.info(
        "%d incident(s) -> %d story/ies, %d open thread(s). Report: %s",
        sum(r.incidents_found for r in results),
        stories,
        len(threads) - stories,
        base.with_suffix(".md"),
    )
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--out-dir", default="consolidation_reports")
    parser.add_argument("--max-sessions", type=int, default=0, help="0 = all sessions")
    parser.add_argument(
        "--input-price",
        type=float,
        default=None,
        help="USD per million input tokens; cost is only reported when both "
        "prices are given, so no rate is ever assumed",
    )
    parser.add_argument("--output-price", type=float, default=None)
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
