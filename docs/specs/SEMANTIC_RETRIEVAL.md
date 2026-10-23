# Semantic retrieval for Layer 3: plan

**Status:** Ready to build. Decisions settled 2026-10-23 (§4). Nothing built yet.
**Owner:** Krish · **Written by:** Claude, from `docs/proposals/embedding-eval-results.md`
**Branch:** `feature/semantic-retrieval`, branched from `chore/embedding-eval` (or from `main` once that is merged). One PR. Never commit to `main`; see `.claude/rules/git.md`.
**Background:** `docs/proposals/embedding-strategy.md` (why embeddings were removed), `docs/proposals/embedding-eval-results.md` (the evidence), `docs/specs/ADMIN_METRICS.md` and `docs/specs/COST_TO_SERVE.md` (the ledger and metrics this extends).

---

## 1. Why

Layer 3 picks the 25 newest story atoms and takes their open threads, breadth-first across domains, up to 12 (`orchestrator._RETRIEVAL_TOP_K`, `system_prompt._MAX_RENDERED_THREADS`). That is recency, and recency is blind to what the parent is talking about.

In the offline evaluation (synthetic data, 42 queries, one run):

- `recency15` put the right atom in the window for **0%** of old-but-relevant targets (sessions 1-5). Recency cannot fix this by getting bigger.
- `gemini-embedding-001` on Vertex `asia-south1`, at 1536 dimensions, scored **100% Recall@5, MRR 0.95**. The runner-up, Titan V2, scored 90%. Rankings at 1536 and 3072 dimensions were identical.
- 1536 dimensions fits the existing `vector(1536)` column. No dimension migration.

**Limits of that evidence** (from the results doc): one author wrote atoms, queries and labels; 42 queries, one run; Gemini is at the ceiling of the test set; the queries were short topic lines, not what a parent says on her first turn; and the atoms were written in the parent's own language, which real extraction may not do (§5.3). Prerequisite P2 turns each of these into a test.

## 2. What this changes, and what it does not

**Changes:**
1. Each new story atom gets an embedding after extraction.
2. When Layer 3 is built (first turn of a session), a query embedding finds the atoms closest to what the parent is talking about. Their open threads go first, then today's breadth-first fill.
3. Existing atoms are backfilled.
4. Embedding health appears on the admin metrics page (§5.8).

**Does not change:** the fact store, significant people, the extraction prompt (see §5.3 on narrative language), which domain a session opens on, the Layer 3 freeze per session (COST_TO_SERVE C2), `_MAX_RENDERED_THREADS`, the parent consent wording, or the rule that retrieval can never take down a turn.

**Non-goals:** an ANN index (exact scan is right at pilot scale), re-ranking, embedding facts or people, bringing back `recent_stories` quotes (the strategy doc's deferred question), and moving Gemini TTS onto Vertex.

## 3. Before `EMBEDDINGS_ENABLED=true` in production

| # | Item | Blocks enabling? |
|---|---|---|
| P1 | **Written confirmation from Google** that `gemini-embedding-001` requests to the `asia-south1` regional endpoint are processed in India, with no use of content for training, and what abuse-monitoring logging or caching of request content applies. File the reply with the DPDP records. | **No.** Send the request now; nothing waits on it. If the answer is bad (processing outside India, or content used for training), stop and revisit: the fallback is Titan V2 in `ap-south-1`. |
| P2 | **A second eval round** in `scripts/embedding_eval/` (§3.1) | **Yes** |
| P3 | **Vertex quota check** for the project, and query latency measured from the Lightsail box. Round one measured p50 310 ms, p95 720 ms from a laptop, no errors in 300 calls, quota never probed. | **Yes** |
| P4 | **Privacy page updated and deployed** (§5.7), after confirming Google's no-training terms for Vertex | **Yes** |

**Why P1 does not block** (Krish's decision, 2026-10-23). DPDP's substantive duties, including the cross-border section 16, start in mid-May 2027, and section 16 is a negative list: transfers are allowed unless a country is restricted, and none is. Keeping data in India is Katha's own commitment (`CLAUDE.md`, the privacy page), not a legal requirement of section 16. What does apply today is the IT Act s.43A and the SPDI Rules 2011: health information is sensitive, and sending it abroad needs the recipient to give the same protection plus consent or contract necessity (Rule 7). The existing parent consent already says services are used "from AI companies, some of them outside India". This is a risk call, not a legal clearance, and not legal advice. The regional Mumbai endpoint stays regardless, since it costs nothing extra.

### 3.1 The second eval round (P2)

Extend `scripts/embedding_eval/`. Ask before any paid call, and give the approximate cost first.

0. **Look first at what real atoms contain.** The extraction prompt asks for "a 1-3 sentence summary of what was said" with no language rule, and `CLAUDE.md` says "capture in the user's language; English summary only". Read the atoms from Krish's self-trial (read-only; report counts and languages only, never paste atom text into docs or logs) and report whether `title` and `narrative` come out English, in the parent's language, or mixed.
1. **Queries written as a parent's first reply**, not topic lines.
2. **English-summary variants** of the same atoms, alongside the native-language ones, so both stored-language cases are tested. If step 0 shows atoms are consistently one language, weight the variants accordingly.
3. **More romanised code-mixed look-alikes**, and a second author for the queries, to reduce vocabulary leakage.
4. **Four query shapes** (§5.2), each scored.

**Decision rule** (settled): the shape with the highest Recall@5 on the first-reply queries wins; ties go to the simpler shape. It must reach **80% or more**, the project's objective-case target. If none does, stop, report, and leave the flag off. Do not move the bar.

## 4. Settled decisions

| # | Decision | Settled |
|---|---|---|
| D1 | Gate | **Ship dark.** `EMBEDDINGS_ENABLED`, default `false`. While false, no atom text and no query leaves for Google. Enabled after P2, P3, P4. |
| D2 | Pure semantic or hybrid | **Hybrid.** Semantic hits first, then the existing newest-25 breadth-first fill. Recent controls scored 100% under embeddings, so this is insurance: it keeps S2.5's breadth and covers atoms with no vector yet. Untested as a combination; the end-to-end test in §6 checks it. |
| D3 | Query text | Chosen by P2 under the rule in §3.1. Default until then: domain name + first reply. |
| D4 | Mixing embedding spaces | **Add `story_atoms.embedding_model`** and filter on it at query time. |
| D5 | Failed embeddings | **Hourly sweep** in the existing scheduler, capped at 50 atoms a run. |
| D6 | Production credentials | Krish creates a dedicated service account key (`roles/aiplatform.user` only) at deploy time. The code reads it from `GOOGLE_APPLICATION_CREDENTIALS`. |
| D7 | Timing | **Enable before family #1**, after the self-trial (`docs/SELF_TRIAL.md` Stage B) has shown it working. |
| D8 | Narrative language | **Embed whatever is stored.** No translation step and no change to the extraction prompt. P2 tests both languages. If Krish later pins the language, that is a separate change. |
| D9 | Admin metrics | **In this PR** (§5.8). |

## 5. Build

### 5.1 The adapter (`adapters/gemini_embeddings.py`, new)

Reuse the call shape the eval validated (`GeminiVertex` in `scripts/embedding_eval/run_eval.py`):

- `POST https://asia-south1-aiplatform.googleapis.com/v1/projects/{project}/locations/asia-south1/publishers/google/models/gemini-embedding-001:predict`
- Body: one instance per request, `{"content": text, "task_type": "RETRIEVAL_DOCUMENT" | "RETRIEVAL_QUERY"}`, parameters `{"outputDimensionality": 1536, "autoTruncate": true}`.
- **The location is a constant, not a setting.** There is deliberately no `VERTEX_LOCATION` to misconfigure. The adapter asserts the host is `asia-south1-aiplatform.googleapis.com` and refuses to run otherwise. Never use the global host (`aiplatform.googleapis.com`), and never the Gemini Developer API key from `adapters/gemini_tts.py`.
- **Normalise every vector** (L2) before storing or querying. Google advises it for truncated Gemini vectors, and the eval's scores assume it.
- **Auth:** a service account key, via `google-auth` (new dependency) and `GOOGLE_APPLICATION_CREDENTIALS`. Token refresh is blocking, so run it in a thread (`asyncio.to_thread`); make the call with `httpx` and `adapters.retry.post_with_backoff`, as `gemini_tts.py` does. The Lightsail box has no IAM role to federate from, so a key file is the practical route. Mount it read-only, mode 600, outside git and outside the image.
- **Interface:** `async def embed(text: str, *, kind: Literal["document", "query"]) -> list[float]`, plus `MODEL_TAG = "gemini-embedding-001@1536"`. Nothing outside this module may know it is Gemini. That keeps the Titan fallback cheap: one file plus a `vector(1024)` migration.
- **Timeouts and retries:** a query gets one attempt plus one retry and a hard 2.5 s ceiling, because it sits on the turn path. A document gets three attempts; it runs in the background.
- **Cap the input** (about 2,000 characters for a query) so a runaway transcript cannot become a long call.
- **Ledger and pricing.** Write one `api_usage` row per call with **`vendor="google_embeddings"`**, not `"google"`. `core/metrics._units` treats every `vendor == "google"` row as a Gemini TTS clip and `core/pricing.cost_breakdown` prices it as TTS, so embedding rows under that name would corrupt the TTS counts, audio minutes and fallback rate. Operations: `embed_document`, `embed_query`, and `embed_query_fallback` (written when a query embedding fails and retrieval falls back to recency; `units={"reason": "timeout" | "error" | "empty"}`). Tokens come from the response's `statistics.token_count`.
  - In `core/pricing.py` add a `vendor == "google_embeddings"` branch: $0.15 per 1M tokens for the two priced operations (Vertex list price; the asia-south1 price is unconfirmed, so say so in the comment), component `google_embeddings`, added to `COMPONENTS`. A `*_fallback` row has no tokens and must return a zero breakdown (`{}` or zero cost), not `None`, so it is not counted as unpriced.

### 5.2 The query: the part the eval did not test

Today `build_prior_context` receives only `state.domain`, a bare id like `childhood`. A domain name alone is a weak query: the eval's queries were specific ("her father's brass shop in Madurai"), and a session opens on one of only eight broad domains with a canned opener (`Domain.entry_prompt`). The specific signal is what the parent says first.

- Layer 3 is built on the session's first turn (`prior_context_for_session`) and then frozen, so the query is embedded **once per session**, from the first reply.
- Add `query_text: str | None = None` to `build_prior_context` and `prior_context_for_session`, and pass `stt_result.transcript` from both call sites (`orchestrator.py` ~1060 and ~1220).
- If the transcript is empty or very short (STT failure, "haan"), fall back to the domain name alone.
- **Four shapes for P2:** (1) domain name only (today's input; the control); (2) first reply only; (3) domain name + first reply; (4) domain name + opener + first reply. Default to (3) until P2 decides.

### 5.3 Write path (`extraction/story_extractor.py`, `memory/vector_store.py`)

- After the atoms are committed and refreshed, embed each one: the text built as in the eval's `atom_text`, which is title, narrative, and verbatim quote in curly quotes. **Embed exactly what is stored (D8).** Do not translate, and do not change the extraction prompt. One request per atom (the model takes one input per request); small bounded concurrency (about 4).
- **Failure never touches extraction.** Wrap it; on error set `embedding_failed = True`, log, and carry on. The atoms stay committed and significant-people handling still runs. Embedding runs in the background task, so the parent's reply never waits on it.
- On success set `embedding` and `embedding_model = MODEL_TAG`, and clear `embedding_failed`.
- `embedding_failed` exists on the model but nothing writes it. `docs/PRE_PILOT_REVIEW.md` CO-02 recommends dropping it. **Do not apply that recommendation**; this feature uses it.
- The hourly sweep (D5) retries `embedding IS NULL` or `embedding_failed` atoms, oldest first, up to 50 a run.
- When `EMBEDDINGS_ENABLED` is false, none of this runs.

### 5.4 Read path (`memory/vector_store.py`, `core/orchestrator.py`)

- Keep `retrieve_relevant` as it is (the recency path, and the fallback). Add `retrieve_semantic(user_id, query_vec, k, db, current_session_id=None)`:

  ```
  WHERE user_id = :u AND embedding IS NOT NULL AND embedding_model = :tag
        [AND session_id != :current]
  ORDER BY embedding <=> :query_vec LIMIT :k
  ```

  Cosine distance, exact scan. Check there is an index on `user_id`; add no vector index.
- In `build_prior_context`, when enabled: embed the query; `semantic = retrieve_semantic(..., k=8)`; `recent = retrieve_relevant(..., top_k=25)`. Open threads = threads from `semantic` in similarity order (at most 6), then `_extract_open_threads` over the rest, deduplicated. The caps of 8 and 6 are **starting values**, not evidence. Set them from the end-to-end test in §6.
- **Degrade, never fail.** If the query embedding times out or errors, write the `embed_query_fallback` ledger row and use the recency path exactly as today. The existing outer `try/except` stays. A parent whose embeddings are down gets today's behaviour.

### 5.5 Existing atoms and rollout tooling

- `scripts/backfill_embeddings.py`: selects atoms with `embedding IS NULL`, embeds sequentially with a sleep sized from P3's quota, resumable, with `--dry-run` and `--limit`. Run once at cutover.
- Migration: add nullable `embedding_model TEXT` to `story_atoms`. Check `alembic heads` for the current head before writing it. No dimension change.

### 5.6 Config and boot checks (`config.py`)

New settings: `EMBEDDINGS_ENABLED: bool = False`, `GOOGLE_CLOUD_PROJECT: str = ""`, `GOOGLE_APPLICATION_CREDENTIALS: str = ""`, `SEMANTIC_TOP_K: int = 8`, `SEMANTIC_THREADS_FIRST: int = 6`.

`validate_production_config` refuses to boot when `EMBEDDINGS_ENABLED` is true and the project is empty, the credentials path is empty, or the file is missing. A missing key must fail the deploy, not quietly turn every query into a fallback.

### 5.7 Privacy page, consent, and docs

- **Parent consent: no change.** `consent_processing` (13 languages in `backend/speech/messages/`) already says "To understand you and to speak, I use services from AI companies, some of them outside India", and buyer onboarding says "Katha uses AI companies, some outside India; the privacy policy names each one". Embeddings fit that wording, and changing it would mean new text in 13 languages and a re-consent through `consent_update` for every existing parent. Judgement call, not legal advice; flag it if you disagree.
- **Privacy page (`frontend/app/privacy/page.tsx`), required before enabling (P4).** It already names Google (USA) as speaking Katha's replies "with the request marked not to be stored". That clause must not be stretched to embeddings, which have no such flag. Add to the Google item that Google also turns what she says into numbers so Katha can find the right earlier story, and that the numbers are stored in India with the story. Keep "do not train their models on it" only if Google's Vertex AI terms support it for embedding requests: Claude Code reads Google's data-governance terms and reports; Krish confirms; if unconfirmed, soften the sentence.
- **Sensitive data:** stories may contain health information, which is sensitive under the SPDI Rules 2011 until May 2027. The existing parent consent is the basis; no extra flow is needed.
- **Update:** `embedding-strategy.md` (Route A superseded by the eval-based choice), `CLAUDE.md` Tech Stack "Memory" line, `docs/TECH_DESIGN.md`, `.env.example`, `docs/DEPLOYMENT.md` (the key file and where it lives), `docs/specs/ADMIN_METRICS.md`.
- **Deletion:** embeddings live in the `story_atoms` row, so "user can delete all data" already removes them.

### 5.8 Admin metrics (`core/metrics.py`, `core/metrics_page.py`)

Counts only. Never an atom's text, title or vector (`ADMIN_METRICS.md` §3, rule 1).

- **`story_atoms` section:** add `embeddings` with `atoms_total`, `atoms_embedded`, `atoms_failed` (`embedding_failed`), `atoms_pending` (no vector and not failed), and `share_embedded` (a rate, `null` when there are no atoms). Extend the atom query in `_load` to select `(embedding IS NOT NULL) AS has_embedding` and `embedding_failed`. **Never select the `embedding` column itself.**
- **`cost` units:** add `embedding_tokens`, `embedding_document_calls`, `embedding_query_calls`, `embedding_query_fallbacks`, `embedding_query_fallback_rate` (fallbacks over queries plus fallbacks). Handle `vendor == "google_embeddings"` in its own branch of `_units`; the existing `google` branch stays TTS-only.
- **`by_component`:** the new `google_embeddings` component. Check `metrics_page.py` renders it.
- All fields present and zero while the flag is off.

## 6. Tests

Write these before calling the work done; see `.claude/rules/testing.md`.

| Test | Covers |
|---|---|
| `tests/test_gemini_embeddings.py` (new) | Request body (task types, 1536, autoTruncate); vectors come back L2-normalised; host assertion refuses the global host; retry on 429/500/503; query ceiling and fallback signal; ledger row with `vendor="google_embeddings"` and tokens |
| `tests/test_vector_store.py` (update) | **`test_retrieve_relevant_makes_no_external_call` currently asserts the module has no `_embed`, `_client` or `embed_and_store`. It pins the S1.5 removal and will fail.** Rewrite it: with `EMBEDDINGS_ENABLED` false, no network call is made on any path. Add `retrieve_semantic` tests: user and model-tag filters, current-session exclusion, null embeddings skipped |
| `tests/test_story_extractor.py` (update) | Embeds after commit, and the text embedded is exactly title + narrative + quote as stored; a failure sets `embedding_failed` and still returns atoms and processes significant people; flag off means no call |
| `tests/test_orchestrator_phase*.py` (update) | Query embedding failure falls back to recency and writes `embed_query_fallback`; short transcript falls back to domain-only; frozen Layer 3 embeds once per session |
| `tests/test_pricing.py`, `tests/test_metrics_tts.py` (update) | `google_embeddings` prices at $0.15 per 1M tokens; a fallback row prices at zero, not unpriced; **embedding rows leave `tts_gemini_clips`, `tts_gemini_audio_minutes` and the TTS fallback rate unchanged** |
| Metrics integration test (extend the seeded world) | Exact `embeddings` counts for embedded, failed and pending atoms; no vector or atom text in the response; zeros with the flag off |
| `tests/test_config.py` (update) | Production refuses `EMBEDDINGS_ENABLED` with missing project, credentials, or key file |
| `tests/test_data_deletion.py` (update) | Deleting a user removes atoms and their embeddings |
| End to end, `evals/run_eval.py` (WS5.3) | **The seeded six-domain history where `childhood` vanished:** with the flag on and a stubbed adapter, the childhood threads must appear in Layer 3 when the session talks about childhood. This also sets the 8 and 6 of §5.4 |

Run TC-01 to TC-10: this touches the inputs to the orchestrator and Layer 3.

## 7. Rollout

| Stage | What | Exit condition |
|---|---|---|
| 0 | Send the P1 request (nothing waits on it). Complete P2, P3, P4 | P2 meets the §3.1 rule; P3 numbers recorded; privacy page change merged |
| 1 | Build §5 with the flag off | Tests and lint pass; PR merged; production behaviour unchanged |
| 2 | **Deploy dark.** Krish creates the service account key (D6) and puts it on the box; run the migration; run `./scripts/deploy.sh`. Boot check passes with the flag still false | `api.katha.life/health` ok; the ledger shows no `google_embeddings` rows |
| 3 | **Enable on Krish's own profile first**: set the flag, run the backfill, then two or three sessions of `docs/SELF_TRIAL.md` Stage B. Check what Layer 3 actually contained | A thread from an old domain shows up when the topic calls for it; reply latency not noticeably worse; the metrics page shows the new numbers |
| 4 | **Before family #1**, if stage 3 holds (D7) | Backfill complete; hourly sweep running |
| 5 | **Watch** for two weeks in admin metrics | Query fallbacks under 1% of sessions; `atoms_pending` trending to zero; no drop in replies per session |

**Rollback:** set `EMBEDDINGS_ENABLED=false`. Retrieval returns to today's recency behaviour immediately. Vectors stay in the database, harmless, and nothing further is sent to Google. No migration needs undoing.

If P2 or P3 fails its bar, do not enable. The pilot goes ahead on S2.5's recency; recheck at session 5, where the strategy doc says the recency window starts to bite.

## 8. Cost

Estimate, not measurement. The eval's 6,224 tokens covered 150 short texts (about 41 tokens each) and cost well under a cent.

- Per session: one query (about 40 tokens) plus about 3 new atoms at 60 tokens = roughly 220 tokens. Over 200 sessions that is about 44,000 tokens, which at $0.15 per 1M is **under ₹1 per family per year** (at ₹94.95 to the dollar). If real atoms are three times longer, still about ₹2.
- Backfill is one-off and small at pilot scale.
- No vector index and no extra server.

## 9. Open items

- **Does the first-reply query work, and what language are real atoms in?** P2 answers both. If the first-reply query does not clear 80%, the feature stays off.
- **Does the combined ordering help the final prompt?** Round one measured atom retrieval, not which threads survive the 12-thread cap. The end-to-end test in §6 measures that.
- **Latency from Lightsail** is unmeasured (P3).
- **Outside this spec:** `adapters/gemini_tts.py` calls the Gemini Developer API, not a regional Vertex endpoint. Worth raising with Google alongside P1.
