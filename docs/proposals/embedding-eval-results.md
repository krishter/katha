# Embedding model evaluation — results

**Status:** Done (offline, synthetic data)
**Date:** 2026-10-23
**Brief:** `docs/proposals/embedding-eval-brief.md` · **Runner and data:** `scripts/embedding_eval/`
**Raw output:** `scripts/embedding_eval/results/run-20261023T141832Z.json` (per-query ranks and top 5)

---

## Summary

- **Embeddings beat recency clearly.** Every model reached 88–100% Recall@5. Raising recency to 15 reached the target in 38% of queries, and in **0%** of queries whose target was an old atom (sessions 1–5). The brief's cheap fix does not solve the problem the brief describes.
- **`gemini-embedding-001` (Vertex, asia-south1) scored best:** 100% Recall@5 with MRR 0.95. Titan V2 scored 90% and BGE-M3 88%. That is a gap of four to five queries out of 42, all of them on romanised code-mixed look-alikes (Tanglish and Hinglish). The pattern is consistent but the sample is small, and Gemini is at the ceiling of this test set.
- **Residency is unconfirmed for Gemini.** It is confirmed for Titan V2, which runs on-demand in ap-south-1 according to AWS's own API.
- **Cohere Multilingual v3 did not complete.** Partway through the run, AWS Marketplace refused access with `INVALID_PAYMENT_INSTRUMENT`.

## Results

Recall@5 in %, with the query count in brackets. The language columns use the language of the correct atom. For the recency rows, the figure is **in-window**, meaning the share of queries whose target reaches Layer 3 at all, because recency ignores the query.

| Model | Overall | Hindi | Tamil | English | Code-mixed | MRR | Query latency p50 / p95 (ms) | USD per 1M tokens |
|---|---|---|---|---|---|---|---|---|
| `recency5` | 10 (42) | 9 | 12 | 7 | 11 | — | ~0 (SQL) | 0 |
| `recency15` | 38 (42) | 36 | 50 | 36 | 33 | — | ~0 | 0 |
| `recency25_prod` ¹ | 17 (42) | 9 | 12 | 7 | 44 | — | ~0 | 0 |
| `bge_m3` (local) | 88 (42) | 100 | 88 | 100 | 56 | 0.81 | 96 / 284 ² | 0 + hosting |
| `titan_v2` | 90 (42) | 100 | 88 | 100 | 67 | 0.81 | 948 / 1772 ³ | 0.024 |
| `cohere_ml_v3` ⁴ | 89 (27) | 86 | 86 | 100 | 83 | 0.81 | 179 / 2275 ³ | 0.10 |
| `gemini_001_1536` | **100 (42)** | 100 | 100 | 100 | 100 | **0.95** | 310 / 720 | 0.15 ⁵ |
| `gemini_001_3072` | 100 (42) | 100 | 100 | 100 | 100 | 0.95 | 511 / 2671 | 0.15 ⁵ |

Slice sizes: Hindi 11, Tamil 8, English 14, code-mixed 9.

By query type (Recall@5 %, in-window for the recency rows):

| Model | Paraphrase (9) | Cross-language (10) | Look-alike (9) | Old-but-relevant (8) | Recent control (6) | Target has STT noise (8) |
|---|---|---|---|---|---|---|
| `recency15` | 44 | 30 | 33 | **0** | 100 | 62 |
| `bge_m3` | 100 | 90 | 67 | 88 | 100 | 100 |
| `titan_v2` | 100 | 90 | 78 | 88 | 100 | 100 |
| `gemini_001_1536` | 100 | 100 | 100 | 100 | 100 | 100 |

**On the 27 queries Cohere completed:** Gemini 100%, Cohere 89%, BGE-M3 85%, Titan 85% (MRR 0.93 / 0.81 / 0.77 / 0.72).

The misses, with the rank at which the correct atom appeared:
- **BGE-M3:** Kamala's own wedding (9), Kamala's daughter's wedding (13), learning music as a girl (8), Shanti's daughter's wedding (32), learning Hindi from a track worker (11).
- **Titan:** Kamala's daughter's wedding (25), father's brass shop (8), learning music as a girl (8), Shanti's daughter's wedding (26).

Both "daughter's wedding" targets are romanised code-mixed text (`Lakshmi-oda kalyanam`, `Neha ki shaadi`). Both models ranked unrelated atoms above them, not just the elder's own wedding.

Notes:
1. `recency25_prod` copies the current production rule: the newest 25 atoms, open threads taken round-robin by domain, and the first 12 rendered. It scores below `recency15` because only threads reach the prompt, and only 52 of the 108 synthetic atoms carry open threads (fewer in later sessions). **That figure depends on the synthetic data and should not be quoted as production behaviour.**
2. Measured on a Mac CPU with the model already loaded. Loading it takes several seconds and about 2.2 GB of disk. Production server CPUs will differ.
3. Inflated by throttling. The run sent requests back to back and hit the default quotas: Titan 60 requests per minute, Cohere 20 requests per minute. These figures include backoff and do not measure the model's speed.
4. Incomplete run. See "Failure behaviour".
5. Vertex list price. A price specific to asia-south1 was not confirmed. The Bedrock prices come from the AWS Pricing API for ap-south-1, read on 2026-10-23.

**Actual spend for this evaluation:** Titan 11,302 tokens, Cohere 5,045, Gemini 6,224 × 2, which comes to about **$0.003**. The two vendors tokenise very differently: Titan counted 82% more tokens than Gemini for the same text, which narrows Titan's price advantage from about 6× to about 3.4×. At pilot scale, all of these amount to cents per month.

## Against the bar

The brief set two conditions: Recall@5 of at least 80%, and a clear margin over `recency15`.

| Model | ≥ 80% | Clear margin over `recency15` (38%) | Passes |
|---|---|---|---|
| `gemini_001_1536` | yes | +62 points | **yes** |
| `titan_v2` | yes | +52 | **yes** |
| `bge_m3` | yes | +50 | **yes** |
| `cohere_ml_v3` | yes, but on 27 of 42 queries | +51 on its subset | not assessable |

On noise: with 42 queries, one query is worth 2.4 points. The margins over recency are far outside noise. Between the models, Gemini's 10–12 point lead is four or five queries. That is more than the brief's 5-point noise line, but those queries all fall in one 9-query slice (code-mixed), so it is suggestive rather than decisive.

## Failure behaviour

| Contestant | What happened in this run | Implication |
|---|---|---|
| `titan_v2` | Default quota is 60 requests per minute (embeddings are limited by requests, not tokens). The run triggered `ThrottlingException` 90 times in 150 calls. botocore's standard retry mode (3 attempts) absorbed every one, so nothing failed. | Normal use (one query per session, one call per new atom) is far below the quota. Re-embedding existing atoms must be rate-limited. |
| `cohere_ml_v3` | All 108 documents and 27 queries succeeded. Then requests were throttled (20 requests per minute) and refused: `AccessDeniedException … INVALID_PAYMENT_INSTRUMENT … Your AWS Marketplace subscription for this model cannot be completed`. | It is sold through AWS Marketplace, which is separate billing with a third-party seller. A billing state outside Katha's control switched it off mid-run, which is the same failure that took down every turn with OpenAI in Sprint 1. |
| `gemini_001_*` | 300 sequential calls with no errors and no retries. The quota was not probed. | Unknown under burst. Check the project's Vertex quota before going live. |
| `bge_m3` | No network failure modes. | The risk becomes operational: model hosting, memory (about 2.2 GB), cold start. |

In production, retrieval is already wrapped (`orchestrator.build_prior_context`), so any of these failures would degrade to "facts and significant people only" rather than lose the reply.

## Recommendation

1. **Plan to replace recency with embeddings, and don't treat a larger `top_k` as the fix.** Recency fails structurally on old targets, which are exactly the case the brief exists for, and embeddings gave up nothing on recent ones (100% on the recent controls). This is a Phase 2 change. Nothing in production changes from this evaluation.
2. **Preferred: `gemini-embedding-001` on Vertex asia-south1 at 1536 dimensions, conditional on Google confirming in writing that processing stays in India.** It scored best on every slice, and at 1536 it fits the existing `vector(1536)` column with no migration. In this test, 1536 and 3072 dimensions gave identical rankings.
3. **Fallback, and the right choice if Google won't confirm: Titan V2 in ap-south-1.**
   - Residency is confirmed: it is on-demand in Mumbai according to AWS's API.
   - AWS already holds Katha's data in Mumbai, so it adds no new processor to the consent copy.
   - It is the cheapest option.
   - Its weak spot is romanised code-mixed text. Needs a `vector(1024)` migration (trivial, since the column is unwritten).
4. **Keep BGE-M3 as Route B.** It now has evidence behind it: within two points of Titan, no vendor, no per-call cost. Its cost is the operational work.
5. **Drop Cohere Multilingual v3.** It didn't complete, it's billed through Marketplace, its default quota is 20 requests per minute, and it showed no advantage on the queries it finished.
6. **Before committing, run one harder round.** Gemini is at the ceiling of this set, so the set can't separate Gemini from the field any further. The next round should have:
   - more romanised code-mixed atoms and look-alikes
   - a second author for queries, to reduce vocabulary leakage (one author wrote both the atoms and the queries here)
   - if possible, real Sarvam Saaras output from the owner's own consented recordings, in place of noise I invented

Route A (Azure OpenAI) was not tested here; the brief didn't list it. If it stays on the table, add it to `PROVIDERS` and rerun. The runner and cache make that cheap.

## Limits (read before quoting numbers)

- **Synthetic and clean.** One author wrote the atoms, queries and labels. Real extractions are messier, and the 14 STT-noise atoms use noise I invented, not real Saaras output.
- **42 queries, one run, no variance estimate.** Several slices have fewer than 10 queries.
- **The query set leans towards older targets** (it covers the brief's hard cases), which is why the recency figures look so poor. The 6 recent-target controls show what recency does well.
- **The labels are machine-written and not yet reviewed.** The Hindi and Tamil ones are flagged in `labels.json`.
- **Narrative language is assumed.** The atoms store narratives in the elder's own language. The extraction prompt doesn't specify a language for `narrative`. If Haiku in practice writes English summaries of Hindi and Tamil speech, the cross-language problem largely goes away and the gaps between models would probably narrow.
- **Latency was measured from a laptop, partly while throttled.** Treat it as order-of-magnitude only.

## Open items from the brief

1. **Does Titan V2 handle Hindi and Tamil?**
   - **Documented: yes.** AWS's Titan embeddings page lists both Hindi and Tamil under V2's multilingual support. It also describes the model as "optimized for English", lists "100+ languages in preview", and warns that "cross-language queries … will return sub-optimal results" ([AWS docs](https://docs.aws.amazon.com/bedrock/latest/userguide/titan-embedding-models.html)).
   - **Measured:** 100% on Hindi-target queries and 88% on Tamil-target queries. Its misses were on romanised code-mixed text (67%) and one Tamil cross-language query.
2. **Is Gemini's residency in asia-south1 confirmed? No, it is still unconfirmed.**
   - Google's data-residency page ([Vertex AI docs](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/learn/data-residency)) now has an "India (asia-south1)" column and a `gemini-embedding-001` row. The cell values still did not render when I fetched it today, so the page does not show a commitment.
   - What this run does show is that the asia-south1 *regional endpoint* serves the model; the runner refused any other host. An endpoint serving the model is not proof of where processing happens.
   - **Get written confirmation from Google**, covering both ML processing in India and abuse-monitoring logging, in the same way the strategy doc already requires of Microsoft.
3. **Dimensions.** The column is `vector(1536)`.

   | Model | Native dims | Options | Fits `vector(1536)` as is? |
   |---|---|---|---|
   | `gemini-embedding-001` | 3072 | `outputDimensionality` (run at 1536 here) | **yes, at 1536** |
   | Titan V2 | 1024 | 1024 / 512 / 256 | no, needs a `vector(1024)` migration |
   | Cohere ML v3 | 1024 | fixed | no |
   | BGE-M3 | 1024 | fixed | no |

   - Google advises normalising truncated Gemini vectors. The runner normalises every vector before taking the cosine, so these results already reflect that.
   - A migration is trivial while nothing is written. A model's vectors also can't be mixed with another model's in one column.
4. **Gemini TTS residency is flagged, and confirmed from the code.** `backend/adapters/gemini_tts.py:51` posts to `https://generativelanguage.googleapis.com/v1beta/interactions` with an `x-goog-api-key`. That is the Gemini Developer API, not regional Vertex.
   - Every reply's text, which is generated from the parent's life story, is therefore sent to an endpoint with no India residency commitment.
   - `store: false` controls retention. It does not control where processing happens.
   - This is out of scope here, but it is the same class of exposure that removed OpenAI in Sprint 1.
   - If Gemini TTS is available on Vertex asia-south1, moving it there would put both Google workloads on one regional, contractually confirmed path. That availability is not verified.

## Proposed changes to `embedding-strategy.md` (not made; for the owner)

- **Route A:** replace "Azure OpenAI, chosen" with "Vertex `gemini-embedding-001` at 1536 dimensions, asia-south1, conditional on written residency confirmation; otherwise Bedrock Titan V2, ap-south-1". Keep Azure only if someone evaluates it.
- **"What it does not do: reduce vendor count":** update it. Google is already a processor (Gemini TTS), and AWS already holds Katha's storage, so either preferred route adds no new vendor. Azure would add one.
- **Trigger section:** add that the old-target failure is now measured. `recency15` reached 0% of old-but-relevant targets on synthetic six-week histories, so the trigger is the domain spread, not the atom count. Also note that S2.5's top-25 and round-robin threads only surface atoms that carry open threads.
- **Steps:** add a step: a quota check plus a rate-limited re-embedding job. Bedrock embeddings are limited by requests per minute, and Titan's default is 60.
- **Open questions:** add the Gemini TTS endpoint question above.
