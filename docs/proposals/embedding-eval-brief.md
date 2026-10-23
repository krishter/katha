# Embedding model evaluation — brief for Claude Code

**Status:** Not started
**Date:** 2026-10-23
**Related:** `docs/proposals/embedding-strategy.md` (read first)

---

## Why this exists

Layer 3 retrieval is `ORDER BY created_at DESC LIMIT 5` (`retrieve_relevant`, `top_k=5`). Once a user has more than 5 atoms spread across domains, the oldest domains drop out of Layer 3 entirely. A probe against seeded six-domain history showed `childhood` disappearing completely. At daily cadence this can happen around session 5-6, inside the pilot's first two weeks.

`embedding-strategy.md` (2026-08-14) chose Azure OpenAI in an India region for Phase 2 (Route A) mainly because provisioning could start immediately, not because it won a comparison. We now want evidence before committing, and AWS and Google Cloud are both candidates.

## The question to answer

Which embedding model retrieves the right story atom best for Hindi, Tamil, English and code-mixed text, with processing fully inside India, and does it beat the cheap alternative of simply raising `top_k` on recency retrieval?

## Hard constraints

1. **Synthetic data only.** Never use real parent data and never send it to any vendor for this test (DPDP).
2. **Regional endpoints only.** Processing must stay in India.
   - Bedrock: `ap-south-1`. Titan Text Embeddings V2 and Cohere Embed Multilingual v3 run in-region there.
   - **Do NOT use Cohere Embed v4.** In ap-south-1 it is reachable only via *global* cross-region inference, which can process outside India.
   - Vertex AI: use the regional `asia-south1` endpoint for `gemini-embedding-001`. **Never the global endpoint** (`aiplatform.googleapis.com`), which gives no residency guarantee. Do not use the Gemini Developer API / AI Studio key for this.
3. **Do not touch production code, migrations, or the `story_atoms` table.** This is an offline experiment.
4. Follow `CLAUDE.md` and `.claude/rules/`: work on branch `chore/embedding-eval`, never commit to `main`, commit messages imperative and 72 characters or fewer.

## Contestants

| Id | Model | Where it runs |
|---|---|---|
| `recency5` | Current behaviour: newest 5 atoms | no model, baseline |
| `recency15` | Newest 15 atoms | no model, the cheap fix |
| `cohere_ml_v3` | `cohere.embed-multilingual-v3` | Bedrock ap-south-1 |
| `titan_v2` | `amazon.titan-embed-text-v2:0` (1024 dims) | Bedrock ap-south-1 |
| `gemini_001` | `gemini-embedding-001` | Vertex AI asia-south1 |
| `bge_m3` | BGE-M3 | local, CPU is fine for ~50 items |

Optional if time allows: a Qwen3-Embedding variant run locally. It tops the 2026 MTEB multilingual ranking but needs more compute.

## Test data

Build under `scripts/embedding_eval/data/`.

**Atoms (~50):** three invented elders, about 15 atoms each, spread across the 8 life domains in `docs/TECH_DESIGN.md` Section 2.2.
- Inspect the real `story_atoms` schema first and match its fields (narrative, domain, created_at, etc.) so the atoms are realistic.
- Languages: Hindi, Tamil, English, plus code-mixed (Hinglish, Tanglish). Roughly balanced.
- About 10 atoms should carry speech-to-text style noise (misspelt names, odd word breaks), since that is what Sarvam Saaras will really produce.
- Give atoms created_at values spread over a simulated 30 daily sessions so recency baselines behave realistically.

**Queries (~30):** the kind of topic string Katha would use to look up context, for example "today's topic is her wedding". Include these hard cases on purpose:
- Paraphrase with no shared keywords with the target atom
- Cross-language (English query, Tamil or Hindi atom)
- Look-alike distractors (e.g. one elder's own wedding versus her daughter's)
- Old-but-relevant targets, where the right atom is from session 1-5 and recency would have dropped it

**Labels:** for each query, list the 1-3 atoms that are correct answers, in a JSON file. Mark clearly which labels were machine-written so the owner can review the Hindi and Tamil ones.

## Scoring

Per model, overall and sliced by language (Hindi, Tamil, English, code-mixed) and by query type (paraphrase, cross-language, look-alike, old-but-relevant):
- **Recall@5:** is at least one correct atom in the top 5?
- **Rank of first correct hit** (and MRR)
- **Latency per call** and **cost per 1M tokens**
- Failure behaviour: what happens on error or throttling

Bar: Recall@5 of 80% or more overall (matches the project's objective-case target), AND a clear margin over `recency15`. If nothing beats `recency15` clearly, say so plainly. That is a valid result and means the cheap fix wins for now.

Report: with ~30 queries, differences under about 5 points are noise. Say so in the write-up rather than declaring a winner on a small gap.

## Deliverables

1. `scripts/embedding_eval/` with a runner (one command, credentials read from environment variables, no secrets committed), the data files, and a README.
2. Results table as `docs/proposals/embedding-eval-results.md`: models down the side, languages across the top, plus latency and cost.
3. A short recommendation, honest about limits: synthetic data is cleaner than real speech, and 30 queries is small.
4. Do not edit `embedding-strategy.md`. Propose any changes to it in the results doc.

## Open items to verify and report back, not assume

- Does Titan V2 handle Hindi and Tamil well? AWS's page does not say which languages it supports.
- Google's own data-residency table for `gemini-embedding-001` in asia-south1 did not render when checked, and a third-party tracker was the only source listing Mumbai. Treat residency as unconfirmed and recommend getting it in writing.
- Dimension handling: `story_atoms.embedding` is `vector(1536)`. Titan and Cohere v3 are 1024; Gemini is 3072 by default and reportedly shortenable. Nothing is written to the column, so a size change is a trivial migration, but note what each model needs.
- Separately: `CLAUDE.md` says Gemini TTS uses an API key with `store: false`. If that is the Gemini Developer API, TTS may be processed outside India. Flag it; it is out of scope here.

## Credentials the owner must provide

- AWS credentials with Bedrock access in ap-south-1 and model access enabled for Cohere Embed Multilingual v3 and Titan Text Embeddings V2
- A Google Cloud project with Vertex AI enabled and a service account with access to asia-south1

If credentials for a contestant are missing, run the others and mark that one as skipped.
