"""Offline embedding-retrieval eval for Layer 3. See README.md.

    python scripts/embedding_eval/run_eval.py                 # free contestants only
    python scripts/embedding_eval/run_eval.py --allow-paid    # also Bedrock / Vertex

Synthetic data only. Paid contestants call regional India endpoints only, and
the guards below refuse anything else.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import statistics
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DATA = HERE / "data"
CACHE = HERE / ".cache"
RESULTS = HERE / "results"

# --- Residency guards ------------------------------------------------------

BEDROCK_REGION = "ap-south-1"
# Plain on-demand model ids only. An inference-profile id ("apac.", "global.")
# can route outside the region, and Cohere Embed v4 is only reachable that way
# from ap-south-1, so it is not on this list.
BEDROCK_ALLOWED = {"amazon.titan-embed-text-v2:0", "cohere.embed-multilingual-v3"}
VERTEX_LOCATION = "asia-south1"
VERTEX_HOST = f"{VERTEX_LOCATION}-aiplatform.googleapis.com"

# --- Prices, USD per 1M input tokens (list prices; re-check before quoting) --

PRICE_PER_M = {
    "titan_v2": 0.024,  # AWS Pricing API, ap-south-1, 2026-10-23
    "cohere_ml_v3": 0.10,  # AWS Pricing API (Marketplace), ap-south-1, 2026-10-23
    "gemini_001_1536": 0.15,  # Vertex list price; asia-south1 not confirmed
    "gemini_001_3072": 0.15,
    "bge_m3": 0.0,
    "qwen3_06b": 0.0,
}

# Mirrors production: orchestrator._RETRIEVAL_TOP_K and
# system_prompt._MAX_RENDERED_THREADS.
PROD_TOP_K = 25
PROD_MAX_THREADS = 12

LANG_BUCKET = {"hi": "hindi", "ta": "tamil", "en": "english",
               "hinglish": "code_mixed", "tanglish": "code_mixed"}


def atom_text(a: dict) -> str:
    parts = [a.get("title") or "", a["narrative"]]
    if a.get("verbatim_quote"):
        parts.append(f"“{a['verbatim_quote']}”")
    return "\n".join(p for p in parts if p)


# --- Embedding providers ---------------------------------------------------


class Provider:
    """embed(texts, kind) -> (vectors, tokens, per-call latencies, errors)."""

    paid = False
    batch = 1

    def embed_one(self, text: str, kind: str) -> tuple[list[float], int]:
        raise NotImplementedError

    def embed_many(self, texts: list[str], kind: str) -> tuple[list[list[float]], int]:
        out, tok = [], 0
        for t in texts:
            v, n = self.embed_one(t, kind)
            out.append(v)
            tok += n
        return out, tok


class Bedrock(Provider):
    paid = True

    def __init__(self, model_id: str):
        import boto3
        from botocore.config import Config

        if model_id not in BEDROCK_ALLOWED:
            raise SystemExit(f"refusing Bedrock model {model_id!r}: not on the in-region allowlist")
        self.model_id = model_id
        self.client = boto3.client(
            "bedrock-runtime",
            region_name=BEDROCK_REGION,
            config=Config(retries={"mode": "standard", "max_attempts": 3}),
        )
        host = self.client.meta.endpoint_url
        if f".{BEDROCK_REGION}." not in host:
            raise SystemExit(f"refusing Bedrock endpoint {host}")
        self.retries = 0

    def _invoke(self, body: dict) -> tuple[dict, dict]:
        r = self.client.invoke_model(modelId=self.model_id, body=json.dumps(body),
                                     contentType="application/json", accept="application/json")
        self.retries += r["ResponseMetadata"].get("RetryAttempts", 0)
        return json.loads(r["body"].read()), r["ResponseMetadata"]["HTTPHeaders"]


class Titan(Bedrock):
    def __init__(self):
        super().__init__("amazon.titan-embed-text-v2:0")

    def embed_one(self, text, kind):
        body, _ = self._invoke({"inputText": text, "dimensions": 1024, "normalize": True})
        return body["embedding"], body["inputTextTokenCount"]


class CohereV3(Bedrock):
    batch = 96

    def __init__(self):
        super().__init__("cohere.embed-multilingual-v3")

    def embed_many(self, texts, kind):
        input_type = "search_document" if kind == "doc" else "search_query"
        body, headers = self._invoke({"texts": texts, "input_type": input_type, "truncate": "END"})
        return body["embeddings"], int(headers.get("x-amzn-bedrock-input-token-count", 0))


class GeminiVertex(Provider):
    paid = True

    def __init__(self, dims: int):
        import google.auth
        import google.auth.transport.requests
        import requests

        self.dims = dims
        project = os.environ.get("GOOGLE_CLOUD_PROJECT")
        if not project or not os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"):
            raise RuntimeError("GOOGLE_CLOUD_PROJECT / GOOGLE_APPLICATION_CREDENTIALS not set")
        creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
        self.creds = creds
        self.authreq = google.auth.transport.requests.Request()
        self.session = requests.Session()
        self.url = (f"https://{VERTEX_HOST}/v1/projects/{project}/locations/{VERTEX_LOCATION}"
                    f"/publishers/google/models/gemini-embedding-001:predict")
        # Belt and braces: the global host is aiplatform.googleapis.com.
        assert self.url.startswith(f"https://{VERTEX_HOST}/"), self.url
        self.retries = 0

    def embed_one(self, text, kind):
        task = "RETRIEVAL_DOCUMENT" if kind == "doc" else "RETRIEVAL_QUERY"
        payload = {"instances": [{"content": text, "task_type": task}],
                   "parameters": {"outputDimensionality": self.dims, "autoTruncate": True}}
        for attempt in range(3):
            if not self.creds.valid:
                self.creds.refresh(self.authreq)
            r = self.session.post(self.url, json=payload, timeout=30,
                                  headers={"Authorization": f"Bearer {self.creds.token}"})
            if r.status_code in (429, 500, 503) and attempt < 2:
                self.retries += 1
                time.sleep(2 ** attempt)
                continue
            if r.status_code != 200:
                raise RuntimeError(f"Vertex HTTP {r.status_code}: {r.text[:300]}")
            p = r.json()["predictions"][0]["embeddings"]
            return p["values"], int(p.get("statistics", {}).get("token_count", 0))
        raise RuntimeError("unreachable")


class Local(Provider):
    def __init__(self, name: str, query_prompt: str | None = None):
        from sentence_transformers import SentenceTransformer

        self.model = SentenceTransformer(name, device="cpu")
        self.query_prompt = query_prompt
        self.batch = 16

    def embed_many(self, texts, kind):
        kw = {"prompt_name": self.query_prompt} if (kind == "query" and self.query_prompt) else {}
        v = self.model.encode(texts, normalize_embeddings=True, **kw)
        return [list(map(float, x)) for x in v], 0


PROVIDERS = {
    "titan_v2": Titan,
    "cohere_ml_v3": CohereV3,
    "gemini_001_1536": lambda: GeminiVertex(1536),
    "gemini_001_3072": lambda: GeminiVertex(3072),
    "bge_m3": lambda: Local("BAAI/bge-m3"),
    "qwen3_06b": lambda: Local("Qwen/Qwen3-Embedding-0.6B", query_prompt="query"),
}
PAID = {"titan_v2", "cohere_ml_v3", "gemini_001_1536", "gemini_001_3072"}
RECENCY = ["recency5", "recency15", "recency25_prod"]


# --- Running ---------------------------------------------------------------


def _key(kind: str, text: str) -> str:
    return hashlib.sha256(f"{kind}\0{text}".encode()).hexdigest()[:24]


def embed_all(name: str, prov: Provider, items: list[tuple[str, str]], cache: dict) -> dict:
    """items: (kind, text). Fills cache; returns per-call stats for uncached calls."""
    stats = {"latencies_ms": {"doc": [], "query": []}, "tokens": 0, "errors": []}
    todo = [(k, t) for k, t in items if _key(k, t) not in cache]
    for kind in ("doc", "query"):
        texts = [t for k, t in todo if k == kind]
        # Queries are always sent one per call: that is the production shape.
        step = prov.batch if kind == "doc" else 1
        for i in range(0, len(texts), step):
            chunk = texts[i:i + step]
            t0 = time.perf_counter()
            try:
                vecs, tok = prov.embed_many(chunk, kind)
            except Exception as e:  # recorded, not hidden
                stats["errors"].append(f"{type(e).__name__}: {str(e)[:300]}")
                continue
            stats["latencies_ms"][kind].append((time.perf_counter() - t0) * 1000)
            stats["tokens"] += tok
            for t, v in zip(chunk, vecs):
                cache[_key(kind, t)] = v
    stats["retries"] = getattr(prov, "retries", 0)
    return stats


def rank_by_embedding(name, cache, atoms_by_user, q) -> list[str]:
    cands = atoms_by_user[q["user_id"]]
    qv = cache.get(_key("query", q["text"]))
    if qv is None or any(_key("doc", atom_text(a)) not in cache for a in cands):
        return []
    A = np.array([cache[_key("doc", atom_text(a))] for a in cands], dtype=float)
    A /= np.linalg.norm(A, axis=1, keepdims=True)
    v = np.array(qv, dtype=float)
    v /= np.linalg.norm(v)
    order = np.argsort(-(A @ v), kind="stable")
    return [cands[i]["id"] for i in order]


def prod_rendered_atoms(cands_newest_first: list[dict]) -> set[str]:
    """Atoms whose open thread reaches the prompt under today's production rule:
    newest 25, round-robin by domain, first 12 rendered."""
    by_domain: dict[str, list[tuple[str, str]]] = {}
    seen = set()
    for a in cands_newest_first[:PROD_TOP_K]:
        for t in a["open_threads"]:
            if t in seen:
                continue
            seen.add(t)
            by_domain.setdefault(a["domain"], []).append((t, a["id"]))
    flat = []
    for tier in range(max((len(v) for v in by_domain.values()), default=0)):
        for v in by_domain.values():
            if tier < len(v):
                flat.append(v[tier][1])
    return set(flat[:PROD_MAX_THREADS])


def score(ranking: list[str], relevant: list[str]) -> dict:
    if not ranking:
        return {"skipped": True}
    first = next((i + 1 for i, a in enumerate(ranking) if a in relevant), None)
    return {
        "first_rank": first,
        "rr": 1 / first if first else 0.0,
        "r5": bool(first and first <= 5),
        "r15": bool(first and first <= 15),
        "r25": bool(first and first <= 25),
    }


def aggregate(rows: list[dict]) -> dict:
    rows = [r for r in rows if not r.get("skipped")]
    if not rows:
        return {"n": 0}
    n = len(rows)
    return {
        "n": n,
        "recall@5": sum(r["r5"] for r in rows) / n,
        "recall@15": sum(r["r15"] for r in rows) / n,
        "recall@25": sum(r["r25"] for r in rows) / n,
        "mrr": sum(r["rr"] for r in rows) / n,
        "in_window": (sum(r["in_window"] for r in rows) / n) if "in_window" in rows[0] else None,
        "median_first_rank": statistics.median([r["first_rank"] or 99 for r in rows]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--models", default=",".join(RECENCY + ["bge_m3"]),
                    help="comma list from: " + ",".join(RECENCY + list(PROVIDERS)))
    ap.add_argument("--allow-paid", action="store_true",
                    help="required before any Bedrock / Vertex call is made")
    args = ap.parse_args()

    atoms = [json.loads(l) for l in open(DATA / "atoms.jsonl", encoding="utf-8")]
    queries = json.load(open(DATA / "queries.json", encoding="utf-8"))
    labels = json.load(open(DATA / "labels.json", encoding="utf-8"))["labels"]
    by_id = {a["id"]: a for a in atoms}
    atoms_by_user: dict[str, list[dict]] = {}
    for a in sorted(atoms, key=lambda a: a["created_at"], reverse=True):
        atoms_by_user.setdefault(a["user_id"], []).append(a)

    CACHE.mkdir(exist_ok=True)
    RESULTS.mkdir(exist_ok=True)
    report = {"run_at": datetime.now(timezone.utc).isoformat(), "models": {}}

    for name in [m.strip() for m in args.models.split(",") if m.strip()]:
        entry: dict = {}
        per_query: dict[str, dict] = {}
        if name in RECENCY:
            for q in queries:
                cands = atoms_by_user[q["user_id"]]
                ranking = [a["id"] for a in cands]
                rel = labels[q["id"]]["relevant"]
                s = score(ranking, rel)
                if name == "recency25_prod":
                    s["in_window"] = bool(prod_rendered_atoms(cands) & set(rel))
                else:
                    k = int(name.removeprefix("recency"))
                    s["in_window"] = bool(set(ranking[:k]) & set(rel))
                per_query[q["id"]] = s
            entry["note"] = "query-blind; in_window = target reaches Layer 3"
        else:
            if name not in PROVIDERS:
                raise SystemExit(f"unknown model {name}")
            if name in PAID and not args.allow_paid:
                print(f"[{name}] skipped: paid, run with --allow-paid")
                report["models"][name] = {"skipped": "paid; --allow-paid not given"}
                continue
            try:
                prov = PROVIDERS[name]()
            except Exception as e:
                print(f"[{name}] skipped: {type(e).__name__}: {e}")
                report["models"][name] = {"skipped": f"{type(e).__name__}: {e}"}
                continue
            cache_file = CACHE / f"{name}.json"
            cache = json.load(open(cache_file)) if cache_file.exists() else {}
            items = [("doc", atom_text(a)) for a in atoms] + [("query", q["text"]) for q in queries]
            stats = embed_all(name, prov, items, cache)
            json.dump(cache, open(cache_file, "w"))
            entry["calls"] = stats
            entry["dims"] = len(next(iter(cache.values()))) if cache else None
            for q in queries:
                per_query[q["id"]] = score(rank_by_embedding(name, cache, atoms_by_user, q),
                                           labels[q["id"]]["relevant"])
                per_query[q["id"]]["top5"] = rank_by_embedding(name, cache, atoms_by_user, q)[:5]

        # Slices
        def lang_of(q):
            return LANG_BUCKET[by_id[labels[q["id"]]["relevant"][0]]["_lang"]]

        slices = {"overall": aggregate(list(per_query.values()))}
        for key, fn in [
            ("lang", lang_of),
            ("type", lambda q: q["type"]),
            ("old_target", lambda q: "old_target" if min(by_id[r]["session_number"] for r in labels[q["id"]]["relevant"]) <= 5 else "recent_target"),
            ("noisy_target", lambda q: "noisy" if any(by_id[r]["_stt_noise"] for r in labels[q["id"]]["relevant"]) else "clean"),
        ]:
            groups: dict[str, list] = {}
            for q in queries:
                groups.setdefault(fn(q), []).append(per_query[q["id"]])
            for g, rows in groups.items():
                slices[f"{key}:{g}"] = aggregate(rows)
        entry["slices"] = slices
        entry["per_query"] = per_query
        report["models"][name] = entry
        o = slices["overall"]
        if o.get("n"):
            extra = f" in_window={o['in_window']:.0%}" if o.get("in_window") is not None else ""
            print(f"[{name}] R@5={o['recall@5']:.0%} R@15={o['recall@15']:.0%} MRR={o['mrr']:.2f}{extra}")

    out = RESULTS / f"run-{datetime.now(timezone.utc):%Y%m%dT%H%M%SZ}.json"
    json.dump(report, open(out, "w"), ensure_ascii=False, indent=1)
    print("wrote", out.relative_to(HERE.parent.parent))


if __name__ == "__main__":
    sys.exit(main())
