"""Round 2 (docs/specs/SEMANTIC_RETRIEVAL.md §3.1): first-reply queries,
four query shapes, native and English-summary atoms. Gemini on Vertex
asia-south1 only.

    python scripts/embedding_eval/run_round2.py --allow-paid

Vectors are cached in .cache/, so a rerun is free.
"""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

from run_eval import CACHE, RESULTS, GeminiVertex, _key, embed_all

HERE = Path(__file__).resolve().parent
DATA = HERE / "data" / "round2"

# prompts/domains.py: display name and the canned opener (Domain.entry_prompt).
DOMAINS = {
    "childhood": ("Childhood & Home", "Tell me about the house you grew up in — what did it look like?"),
    "family_ancestors": ("Family & Ancestors", "Tell me about your grandparents. What do you remember of them?"),
    "education": ("Education & Coming of Age", "What was your school like? Who was a teacher you remember?"),
    "career": ("Career & Work Life", "How did you end up in your career? What was your first job?"),
    "love_marriage": ("Love, Marriage & Family Building", "Tell me about when you and your spouse first met."),
    "historical_events": ("Historical Events Witnessed", "You have lived through remarkable times in India's history. What is one event that you remember vividly — where were you when it happened?"),
    "hobbies": ("Hobbies, Passions & Talents", "Was there something you were known for? A skill or hobby that people associated with you?"),
    "wisdom": ("Wisdom & Life Lessons", "What is something you know now that you wish you had known at 25?"),
}

# §5.2's four shapes, simplest first (ties go to the simpler one).
SHAPES = {
    "1_domain": lambda d, reply: DOMAINS[d][0],
    "2_reply": lambda d, reply: reply,
    "3_domain_reply": lambda d, reply: f"{DOMAINS[d][0]}\n{reply}",
    "4_domain_opener_reply": lambda d, reply: f"{DOMAINS[d][0]}\n{DOMAINS[d][1]}\n{reply}",
}


def atom_text(a: dict, corpus: str) -> str:
    """As the eval's atom_text: title, narrative, quote in curly quotes."""
    title, narrative = (
        (a["en_title"], a["en_narrative"]) if corpus == "english" else (a["title"], a["narrative"])
    )
    parts = [title or "", narrative]
    if a.get("verbatim_quote"):
        parts.append(f"“{a['verbatim_quote']}”")
    return "\n".join(p for p in parts if p)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-paid", action="store_true")
    ap.add_argument("--dims", type=int, default=1536)
    args = ap.parse_args()

    atoms = [json.loads(line) for line in open(DATA / "atoms.jsonl", encoding="utf-8")]
    queries = json.load(open(DATA / "queries.json", encoding="utf-8"))
    by_user: dict[str, list[dict]] = {}
    for a in atoms:
        by_user.setdefault(a["user_id"], []).append(a)

    items = [("doc", atom_text(a, c)) for a in atoms for c in ("native", "english")]
    items += [
        ("query", fn(q["session_domain"], q["reply"]))
        for q in queries
        for fn in SHAPES.values()
    ]
    items = list(dict.fromkeys(items))
    name = f"gemini_001_{args.dims}"
    cache_file = CACHE / f"{name}.json"
    CACHE.mkdir(exist_ok=True)
    cache = json.load(open(cache_file)) if cache_file.exists() else {}
    todo = [i for i in items if _key(*i) not in cache]
    print(f"{len(items)} texts, {len(todo)} not cached")
    stats = {}
    if todo:
        if not args.allow_paid:
            raise SystemExit("uncached texts need --allow-paid (Vertex asia-south1)")
        stats = embed_all(name, GeminiVertex(args.dims), todo, cache)
        json.dump(cache, open(cache_file, "w"))
        if stats["errors"]:
            print("errors:", stats["errors"][:3])

    def rank(q, shape, corpus):
        cands = by_user[q["user_id"]]
        qv = np.array(cache[_key("query", SHAPES[shape](q["session_domain"], q["reply"]))])
        A = np.array([cache[_key("doc", atom_text(a, corpus))] for a in cands])
        A /= np.linalg.norm(A, axis=1, keepdims=True)
        qv /= np.linalg.norm(qv)
        return [cands[i]["id"] for i in np.argsort(-(A @ qv), kind="stable")]

    report = {"run_at": datetime.now(timezone.utc).isoformat(), "calls": stats, "results": {}}
    print(f"\n{'shape':24} {'corpus':8} R@5    MRR   misses@5")
    for corpus in ("english", "native"):
        for shape in SHAPES:
            rows = {}
            for q in queries:
                r = rank(q, shape, corpus)
                first = next((i + 1 for i, a in enumerate(r) if a in q["relevant"]), None)
                rows[q["id"]] = {"first_rank": first, "top5": r[:5]}
            n = len(rows)
            r5 = sum(1 for v in rows.values() if v["first_rank"] and v["first_rank"] <= 5) / n
            mrr = sum(1 / v["first_rank"] for v in rows.values() if v["first_rank"]) / n
            misses = [k for k, v in rows.items() if not (v["first_rank"] and v["first_rank"] <= 5)]
            report["results"][f"{shape}|{corpus}"] = {"recall@5": r5, "mrr": mrr, "per_query": rows}
            print(f"{shape:24} {corpus:8} {r5:5.0%}  {mrr:.2f}  {misses}")

    RESULTS.mkdir(exist_ok=True)
    out = RESULTS / f"round2-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    json.dump(report, open(out, "w"), ensure_ascii=False, indent=1)
    print("wrote", out.name)


if __name__ == "__main__":
    main()
