# Embedding retrieval eval (offline)

Answers the question in `docs/proposals/embedding-eval-brief.md`: does any
in-India embedding model retrieve the right story atom for Layer 3 better
than recency? Results and recommendation: `docs/proposals/embedding-eval-results.md`.

Nothing here touches production code, the database, or real parent data.

## Data (`data/`)

- `atoms.jsonl`: 108 synthetic story atoms for three invented elders
  (Kamala, Tamil, Madurai/Chennai; Shanti, Hindi, Allahabad/Lucknow;
  Prakash, English with Hinglish/Tanglish, Mysore/railways). The fields mirror
  `backend/models/story_atom.py`. The created_at values run across 30 daily
  sessions. Fields starting with `_` are eval metadata (`_lang`, `_stt_noise`).
  14 atoms carry STT-style noise.
- `queries.json`: 42 topic strings of the sort Katha would look up, each typed
  `paraphrase`, `cross_language`, `look_alike`, `old_relevant`, or `recent_control` (6 queries whose target is from sessions 26–30, so recency gets a fair test).
- `labels.json`: 1–3 correct atoms per query. **All labels are machine-written**;
  those with `needs_owner_review: true` involve Hindi, Tamil or code-mixed text.

The text embedded per atom is `title + narrative + verbatim_quote`.

## Contestants

| id | what |
|---|---|
| `recency5`, `recency15` | newest N atoms, query-blind |
| `recency25_prod` | today's production rule: newest 25, open threads round-robin by domain, first 12 rendered |
| `titan_v2` | Bedrock `amazon.titan-embed-text-v2:0`, ap-south-1, 1024 dims |
| `cohere_ml_v3` | Bedrock `cohere.embed-multilingual-v3`, ap-south-1, 1024 dims |
| `gemini_001_1536` / `_3072` | Vertex `gemini-embedding-001`, `asia-south1-aiplatform.googleapis.com` |
| `bge_m3` | `BAAI/bge-m3`, local CPU |
| `qwen3_06b` | `Qwen/Qwen3-Embedding-0.6B`, local CPU (optional) |

The runner refuses Bedrock model ids that aren't on an in-region allowlist (no
inference profiles, no Cohere Embed v4), and it refuses any Vertex host other
than the asia-south1 one.

## Running

```
python3 -m venv /tmp/embed-eval && /tmp/embed-eval/bin/pip install -r scripts/embedding_eval/requirements.txt

# free: recency baselines + local BGE-M3
/tmp/embed-eval/bin/python scripts/embedding_eval/run_eval.py

# paid contestants need an explicit flag
GOOGLE_APPLICATION_CREDENTIALS=... GOOGLE_CLOUD_PROJECT=... \
/tmp/embed-eval/bin/python scripts/embedding_eval/run_eval.py --allow-paid \
  --models recency5,recency15,recency25_prod,bge_m3,titan_v2,cohere_ml_v3,gemini_001_1536,gemini_001_3072
```

- AWS credentials come from the standard boto3 chain (environment variables, then `~/.aws`).
- Google credentials come from `GOOGLE_APPLICATION_CREDENTIALS` and `GOOGLE_CLOUD_PROJECT`.
- If a contestant's credentials are missing, it is marked skipped and the run continues.
- Vectors are cached in `.cache/` (gitignored), so a rerun costs nothing.
- Each run writes `results/run-<timestamp>.json`, which holds per-query ranks and top 5.

A full paid run sends about 30K tokens per model, which costs under $0.02 in total.

## Metrics

- **Recall@k:** at least one correct atom appears in the top k. Each query ranks only that elder's own atoms.
- **MRR:** mean reciprocal rank of the first correct atom.
- **in_window** (recency rows only): the target reaches Layer 3 at all. For
  `recency25_prod`, that means the target's open thread survives the 12-thread cut.
- **Slices:** the language of the first correct atom, the query type, whether
  the target is from sessions 1–5, and whether the target has STT noise.
