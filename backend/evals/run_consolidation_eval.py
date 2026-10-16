#!/usr/bin/env python
"""
TC-12 to TC-18: the consolidation cases from first-person-stories.md §10.

    DATABASE_URL=... ANTHROPIC_API_KEY=... \
      ./.venv/bin/python evals/run_consolidation_eval.py          # all
      ./.venv/bin/python evals/run_consolidation_eval.py tc14 tc17

These make real, billed Sonnet calls, like run_eval.py — that is the point,
since what is under test is the model's judgement about grouping, voice and
fidelity. They need no database and no schema change: the input is the
fixture transcripts in tests/fixtures/consolidation (§11, Phase A).

Every criterion below traces to a Pass column in §10. Criteria are never
loosened to make a run pass; a case that fails on a criterion the spec does
not state is a harness bug and the harness is what gets fixed.
"""

from __future__ import annotations

import asyncio
import json
import pathlib
import sys
from datetime import datetime, timezone

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adapters import llm  # noqa: E402
from adapters.llm import Message  # noqa: E402
from consolidation.pipeline import Thread, Usage, consolidate_session  # noqa: E402
from scripts.consolidate_stories import CONSOLIDATION_MODEL  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent.parent / (
    "tests/fixtures/consolidation"
)
RESULTS = pathlib.Path(__file__).resolve().parent / "results"

# Devanagari through Malayalam, i.e. "is there Indic script in here".
_INDIC = range(0x0900, 0x0D80)


def _has_indic(text: str) -> bool:
    return any(ord(c) in _INDIC for c in text)


async def _call_llm(prompt: str, *, step: str, max_tokens: int):
    response = await llm.chat(
        [Message(role="user", content=prompt)],
        system=None,
        max_tokens=max_tokens,
        model=CONSOLIDATION_MODEL,
        purpose="eval",
    )
    return response.content, response.input_tokens, response.output_tokens


async def run_fixture(fixture: dict) -> tuple[dict[str, Thread], list, Usage]:
    threads: dict[str, Thread] = {}
    results = []
    usage = Usage()
    history: list[dict] = []
    for session in fixture["sessions"]:
        history.extend(session["turns"])
        result = await consolidate_session(
            session_id=f"{fixture['id']}-s{session['session_number']}",
            session_number=session["session_number"],
            turns=session["turns"],
            atoms=session["atoms"],
            threads=threads,
            significant_people=fixture.get("significant_people", []),
            call_llm=_call_llm,
            history_turns=history,
        )
        usage.merge(result.usage)
        results.append(result)
    return threads, results, usage


def _stories(threads: dict[str, Thread]) -> list[Thread]:
    return [t for t in threads.values() if t.status == "story"]


def _all_transcripts(fixture: dict) -> str:
    return "\n".join(t["transcript"] for s in fixture["sessions"] for t in s["turns"])


def check_tc12(fixture, threads, results):
    """Exactly one Thread/Story; the detour atom is not included."""
    detour = fixture["expect"]["detour_atom_excluded"]
    incidents = sum(r.incidents_found for r in results)
    carrying = [t.title for t in threads.values() if detour in t.atom_ids]
    ok = incidents == 1 and not carrying
    return ok, (
        f"incidents={incidents} (want 1); "
        f"detour atom {detour} attached to: {carrying or 'nothing'}"
    )


def check_tc13(fixture, threads, results):
    """Two separate Threads, no cross-contamination of source turns."""
    incidents = sum(r.incidents_found for r in results)
    turn_sets = [set(t.arc.source_turn_ids) for t in threads.values()]
    overlap = set()
    for i, a in enumerate(turn_sets):
        for b in turn_sets[i + 1 :]:
            overlap |= a & b
    ok = incidents == 2 and len(threads) == 2 and not overlap
    return ok, (
        f"incidents={incidents} threads={len(threads)} (want 2 and 2); "
        f"shared turn ids: {sorted(overlap) or 'none'}"
    )


def check_tc14(fixture, threads, results):
    """Zero unsupported sentences in the final body."""
    stories = _stories(threads)
    if not stories:
        return False, "no story promoted, so there is no final body to check"
    story = stories[0]
    verdicts = [s["verdict"] for s in story.sentence_sources]
    bad = [v for v in verdicts if v == "unsupported"]
    body = (story.body_first_person or "").lower()
    invented = [w for w in fixture["expect"]["must_not_contain"] if w in body]
    ok = not bad and not invented
    return ok, (
        f"final verdicts={verdicts}; invented words present: {invented or 'none'}; "
        f"cut during revision: {[c['text'] for c in story.removed_sentences]}"
    )


def check_tc15(fixture, threads, results):
    """Stays a thread; the missing element is `turn`."""
    stories = _stories(threads)
    thread = next(iter(threads.values())) if threads else None
    missing = thread.arc.missing if thread else []
    ok = not stories and thread is not None and "turn" in missing
    return ok, f"stories={len(stories)} (want 0); missing={missing} (want 'turn' in it)"


def check_tc16(fixture, threads, results):
    """Promoted in session 3; the body draws on turns from sessions 1 and 3."""
    promoted_in = [r.session_number for r in results if r.promoted_thread_ids]
    stories = _stories(threads)
    if not stories:
        return False, f"nothing promoted (per-session promotions: {promoted_in})"
    sources = set(stories[0].arc.source_turn_ids)
    from_s1 = {t for t in sources if t.startswith("t1-")}
    from_s3 = {t for t in sources if t.startswith("t3-")}
    ok = promoted_in == [3] and bool(from_s1) and bool(from_s3)
    return ok, (
        f"promoted in session(s) {promoted_in} (want [3]); "
        f"source turns from s1={sorted(from_s1)}, s3={sorted(from_s3)}"
    )


def check_tc17(fixture, threads, results):
    """Original-language quote preserved verbatim; native terms kept."""
    stories = _stories(threads)
    if not stories:
        return False, "no story promoted"
    story = stories[0]
    transcripts = _all_transcripts(fixture)
    indic_quotes = [q for q in story.quotes if _has_indic(str(q.get("original", "")))]
    verbatim = [q for q in indic_quotes if str(q["original"]).strip() in transcripts]
    body = story.body_first_person or ""
    native = [w for w in fixture["expect"]["body_should_contain_any"] if w in body]
    ok = bool(verbatim) and bool(native)
    return ok, (
        f"quotes in Indic script: {len(indic_quotes)}, of which verbatim in a "
        f"transcript: {len(verbatim)}; native terms kept in body: {native or 'none'}"
    )


def check_tc18(fixture, threads, results):
    """The hedge survives into the body."""
    stories = _stories(threads)
    if not stories:
        return False, "no story promoted"
    body = stories[0].body_first_person or ""
    found = [m for m in fixture["expect"]["hedge_markers"] if m.lower() in body.lower()]
    tidied = [
        p
        for p in fixture["expect"]["must_not_contain_exact"]
        if p.lower() in body.lower()
    ]
    ok = bool(found) and not tidied
    return ok, (
        f"hedge markers present: {found or 'NONE'}; "
        f"tidied-away phrasing present: {tidied or 'none'}"
    )


CHECKS = {
    "tc12": check_tc12,
    "tc13": check_tc13,
    "tc14": check_tc14,
    "tc15": check_tc15,
    "tc16": check_tc16,
    "tc17": check_tc17,
    "tc18": check_tc18,
}


async def main(selected: list[str]) -> int:
    names = selected or sorted(CHECKS)
    records = []
    total = Usage()

    for name in names:
        fixture = json.loads((FIXTURES / f"{name}.json").read_text())
        print("=" * 90)
        print(f"{fixture['id']} — {fixture['description']}")
        print("-" * 90)
        try:
            threads, results, usage = await run_fixture(fixture)
        except Exception as exc:  # noqa: BLE001 — a crashed case is a result
            print(f"ERROR: {type(exc).__name__}: {exc}")
            records.append({"tc": fixture["id"], "passed": False, "error": str(exc)})
            continue
        total.merge(usage)
        passed, evidence = CHECKS[name](fixture, threads, results)
        print(f"{'PASS' if passed else 'FAIL'}  {evidence}")
        for story in _stories(threads):
            print(f"\n  “{story.title}”  [{story.fidelity_status}]")
            print(f"  {story.body_first_person}")
            for quote in story.quotes:
                print(f"    — {quote.get('original', '')}  ({quote.get('gloss', '')})")
        print(
            f"\n  tokens: {usage.totals.input_tokens:,} in / "
            f"{usage.totals.output_tokens:,} out over {usage.totals.calls} call(s)"
        )
        records.append(
            {
                "tc": fixture["id"],
                "passed": passed,
                "evidence": evidence,
                "usage": usage.to_json(),
                "threads": [t.to_json() for t in threads.values()],
            }
        )

    passed = sum(1 for r in records if r.get("passed"))
    print("\n" + "#" * 90)
    print("SUMMARY")
    print("#" * 90)
    for record in records:
        print(f"  {record['tc']:<8} {'PASS' if record.get('passed') else 'FAIL'}")
    print(f"\nOverall: {passed}/{len(records)}")
    print(
        f"Tokens:  {total.totals.input_tokens:,} in / "
        f"{total.totals.output_tokens:,} out over {total.totals.calls} call(s)"
    )

    RESULTS.mkdir(exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RESULTS / f"consolidation-{stamp}.json"
    out.write_text(
        json.dumps(
            {
                "ran_at": stamp,
                "cases": names,
                "passed": passed,
                "total": len(records),
                "usage": total.to_json(),
                "results": records,
            },
            indent=2,
            ensure_ascii=False,
        )
    )
    print(f"\nResults written to {out}")
    return 0 if passed == len(records) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main([a.lower() for a in sys.argv[1:]])))
