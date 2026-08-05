# Katha — System Architecture Review

**Date:** 1 August 2026
**Scope:** Post-Phase-7, pre-pilot
**Pilot assumption:** 10–20 families, single backend instance, single Postgres
**Review bar:** Correctness and safety. Performance, scale, and elegance are recorded as debt, not blockers.

---

## 0. How to read this

The question this review answers is not "is Katha well-engineered?" It is narrower and more useful:

> **If we onboard 15 families next month, what will break in a way that loses stories, exposes data, or silently degrades without anyone noticing?**

Everything else is deferred. The codebase is, on the whole, cleanly written — good adapter boundaries, honest docstrings, real tests, sensible migrations. The problems found here are not sloppiness. They are the specific class of bug that only appears when seven phases built in isolation are wired end to end: each phase's seam is correct locally and wrong globally.

There are **9 critical findings**. Five of them are in the single path that matters most — a voice note arriving and a story being preserved.

---

## 1. The design framework

Seven principles, derived from what Katha actually is: a product where the user is elderly and non-technical, the data is irreplaceable and legally sensitive, and the primary interface is a messaging channel we do not control.

### P1 — The archive is the product. Capture must be lossless.

Everything else in Katha — the dashboard, the memory cards, the conversation quality — is downstream of one thing: a story the elderly user told was written down correctly and permanently. Any path where a story can be spoken and not persisted is a product-level failure, not a bug. Capture must be durable, idempotent, and independent of whether the rest of the turn succeeded.

**Test:** Kill the process at any point in a session. How many told stories are lost?

### P2 — The elderly user must never experience silence.

A 72-year-old who sends a voice note and receives nothing does not open a support ticket. They conclude it is broken, or that they did it wrong, and they stop. Silence is the highest-cost failure mode in this product and it is invisible in logs. Every inbound message must produce an outbound message — including, especially, on failure.

**Test:** For each way a turn can fail, what does the user see?

### P3 — Every entry point authenticates; no personal data is public by default.

Katha holds voice recordings and life histories of vulnerable people. There is no such thing as a low-stakes endpoint here. Every route that touches user data must establish who is calling and what they may see. Personal media must not be reachable without authorisation, ever, including by URL guess or leak.

**Test:** For each route and each stored object, who can reach it and how is that enforced?

### P4 — Consent must be enforceable end to end.

DPDP compliance is not a consent checkbox — it is the guarantee that when someone says "delete everything," everything is enumerable and actually deleted. Any personal byte the system writes that is not tracked in the database is a byte that cannot be deleted, and therefore a compliance defect at the moment it is written.

**Test:** Run the deletion endpoint. What personal data still exists afterwards?

### P5 — State machines are explicit and cannot deadlock.

Sessions, domain progression, and freemium gating are state machines. They are currently implicit — expressed as combinations of booleans read by different modules with different assumptions. An implicit state machine will eventually enter a state no one wrote a transition out of. For a product whose entire value depends on *daily recurrence*, a stuck state means the user silently stops being served.

**Test:** Can a user reach a state where they stop receiving sessions and nothing alerts anyone?

### P6 — External dependencies are assumed to fail, and failure is bounded.

Katha's critical path calls five third parties (Twilio, Sarvam STT, Anthropic, Sarvam TTS, S3) inside a webhook handler owned by a sixth party's timeout. Every one of those calls must have a timeout, a defined retry posture, and a defined user-visible fallback. Unbounded inline work inside a webhook is a design error regardless of how fast it usually is.

**Test:** For each external call — what is the timeout, what happens on failure, and who finds out?

### P7 — The system must be observable enough to prove its own go/no-go criteria.

The PRD's launch gate includes "story extraction accuracy ≥75%, manually validated on the first 50 story atoms" and "80%+ pass on TC-01–TC-10." Both require data the system is not currently keeping. A pilot that cannot produce evidence about itself is not a pilot; it is a demo with users in it.

**Test:** After 30 days of pilot, can you answer the go/no-go questions from stored data?

---

## 2. Assessment

| # | Principle | Verdict | Critical findings |
|---|---|---|---|
| P1 | Lossless capture | **Fails** | C1, C2, C3 |
| P2 | Never silent | **Fails** | C4, C5 |
| P3 | Authenticated entry points | **Fails** | C6, C7 |
| P4 | Enforceable consent | **Fails** | C7, C8 |
| P5 | Explicit state machines | **Fails** | C9, C2 |
| P6 | Bounded external failure | **Partial** | H1–H4 |
| P7 | Provable go/no-go | **Fails** | C3, H5 |

Six of seven principles fail. This sounds worse than it is: the failures cluster tightly. Roughly 70% of the risk is removed by fixing the turn pipeline (C1–C5). The rest is a security pass and a lifecycle pass.

---

## 3. Critical findings

Each finding below was confirmed by reading the code, and several by executing it. File and line references are to the current `main`.

---

### C1 — Only the final turn's story atoms are ever persisted. Everything told before that is discarded.

**Principle violated:** P1
**Severity:** Critical — this defeats the product's core purpose.

`process_voice_turn` extracts story atoms on **every** turn (`orchestrator.py:337`), but never writes them. The extraction JSON is returned to the caller and dropped. Story atoms only reach the database via `run_post_session`, which is invoked exactly once — at session close — with the extraction JSON from the **last turn only** (`orchestrator.py:393–399`, `webhook.py:137–143`).

The last turn of a reminiscence session is the wind-down: *"That was lovely, thank you, talk tomorrow."* It contains the least story content of any turn in the session.

The practical consequence: an elderly user talks for twenty minutes about their childhood home, their mother's kitchen, the neighbour who taught them to cycle — and the archive receives whatever the goodbye turn happened to produce. Which is usually nothing.

This is almost certainly why the system will appear to "work" in manual testing (the pipeline runs, audio comes back) while producing an empty family dashboard.

**Root cause:** persistence was designed as a session-close concern rather than a per-turn concern. The extraction data has no home between turns.

---

### C2 — Session-close runs twice, producing duplicate story atoms.

**Principle violated:** P1, P5
**Severity:** Critical — data corruption.

When the LLM signals `session_end_suggested`, two independent code paths schedule post-session processing with the same payload:

1. `orchestrator.py:393–399` — `process_voice_turn` schedules `run_post_session` directly.
2. `webhook.py:137–143` — the webhook separately schedules `close_and_process_session`, which itself calls `run_post_session` (`orchestrator.py:260`).

`process_extraction` performs unconditional `db.add()` with no idempotency key (`story_extractor.py:52–71`). Both runs insert the same atoms. The family dashboard will show every story from the final turn twice, and memory card generation runs twice against a doubled set.

Combined with C1, the archive is simultaneously nearly empty *and* internally duplicated.

---

### C3 — Embeddings are written against a closed database session and fail silently. RAG returns nothing, permanently.

**Principle violated:** P1, P7
**Severity:** Critical — silently disables the memory feature that differentiates the product.

`process_extraction` fires embedding work as detached tasks:

```python
# story_extractor.py:76–80
asyncio.create_task(_embed_atom_safe(atom, db), name=f"embed-{atom.id}")
```

These tasks are never awaited. FastAPI tears down the `get_db` dependency once background tasks return — which happens before these orphaned tasks resume. They then attempt to use a closed `AsyncSession`.

I confirmed the ordering empirically against FastAPI 0.128 semantics: background tasks run *before* dependency teardown (so passing `db` to `add_task` is fine), but `asyncio.create_task` work scheduled *from within* a background task resumes *after* teardown and fails with a use-after-close error.

`_embed_atom_safe` catches everything and logs (`story_extractor.py:112–116`). So:

- `story_atoms.embedding` is `NULL` for every row, forever.
- `vector_store.retrieve_relevant` filters on `embedding.isnot(None)` (`vector_store.py:59`) and therefore always returns `[]`.
- `build_prior_context` returns empty `recent_stories` and empty `open_threads` on every turn.
- Layer 3 of the system prompt permanently reports "This is an early session" (`system_prompt.py:96–100`).

**Katha never remembers anything across sessions.** The cross-session continuity that TC-05 tests, that the "unforgettable person" mechanism depends on, and that constitutes the product's emotional payoff, is inert. And because the failure is caught and logged at INFO-adjacent severity, nothing surfaces it.

There is a second, quieter bug in the same three lines: tasks created without a retained reference can be garbage-collected mid-flight.

---

### C4 — Any failure in the webhook produces total silence for the elderly user.

**Principle violated:** P2
**Severity:** Critical — the highest-cost failure mode, and it is the default one.

The webhook wraps its entire body in a bare `except Exception` that logs and returns 200 with no outbound message (`webhook.py:149–151`).

Every foreseeable transient failure — Sarvam STT 503, Anthropic rate limit, TTS timeout, S3 credential expiry, `ffmpeg` non-zero exit — lands here. The user sent a voice note. They receive nothing. No apology, no retry prompt, no indication the system is alive.

For this user population, a single unexplained silence is plausibly session-ending and possibly product-ending. There is no path in the current design that turns an internal failure into a human-appropriate response.

Related: the same handler sends *"Hi! Your session isn't scheduled yet."* whenever no active session is found (`webhook.py:107–110`). Given C9, this will become the most common response the system produces.

---

### C5 — `max_tokens=500` cannot fit the required dual output. The malformed-response fallback will be the common case.

**Principle violated:** P2, P1
**Severity:** Critical — high-frequency, user-visible degradation.

The LLM is asked to return, in a single completion, both a conversational reply and a full extraction block: story atoms with narrative, 5W fields, verbatim quotes, open threads, named entities, significant people, themes, energy signal, and gap list.

The budget is 500 tokens (`llm.py:47`).

A warm two-sentence reply in code-mixed Hindi costs 80–150 tokens. A single well-formed story atom with a verbatim quote costs 200–400. The response will be truncated mid-JSON on any turn where the user actually tells a story — that is, on every turn that matters.

Truncation means no closing `</extraction>` tag. `check_post_turn` then rejects the response (`conversation_policy.py:75–83`) and the user hears:

> *"I'm so sorry, I lost my train of thought just now! Could you tell me that again?"*

So the failure mode is inverted from what you would want: **the richer the story the user tells, the more likely Katha is to tell them it wasn't listening.** And because the fallback path returns `_EMPTY_EXTRACTION`, the story is discarded too.

The deeper design issue is that dialogue generation and structured extraction are coupled into one latency-critical, token-limited call. They have different requirements — dialogue needs to be fast and warm, extraction needs to be complete and can be async.

---

### C6 — The `/conversation/*` routes are unauthenticated and accept arbitrary user and session IDs.

**Principle violated:** P3
**Severity:** Critical — trivially exploitable, and it costs real money.

```python
# conversation.py:27–34
@router.post("/session")
async def create_session(user_id: str = Form(...), ...)
```

No authentication dependency. Anyone who can reach the backend can:

- Create sessions for any `user_id`, bypassing the freemium gate's intent and consuming Sarvam and Anthropic credits without limit.
- Post audio to any `session_id` and inject fabricated content into a real family's archive.
- Read back the transcript, detected language, energy signal, and crisis flag of the turn via response headers (`conversation.py:55–70`) — including, potentially, another user's session state.

These routes appear to be Phase-1 development scaffolding that survived into Phase 7. They are mounted in production (`main.py:60`).

---

### C7 — All user media is uploaded to S3 with `ACL="public-read"`, and voice notes are never deleted.

**Principle violated:** P3, P4
**Severity:** Critical — DPDP defect plus an undeletable-data defect.

```python
# storage.py:36–43
client.put_object(..., ACL="public-read")
```

Two distinct problems.

**Public exposure.** Every synthesised voice note and every memory card — containing verbatim quotes from an elderly person's life history, with their name printed on the image — sits at a permanently public HTTPS URL. Memory card URLs are additionally stored in the database and served to the dashboard, so they leak through browser history, referrer headers, and anywhere a family member forwards them. Under DPDP this is personal data made available without a lawful basis. The comment justifies the ACL with "so Twilio can fetch it" — Twilio supports time-limited signed URLs, which is the correct mechanism.

**Undeletable data.** Voice note objects are keyed with a random UUID (`whatsapp.py:65`) and that key is never recorded anywhere. The deletion endpoint can only enumerate `memory_cards.image_s3_key` (`admin.py:52–54`). Every voice note Katha has ever sent — audio of an AI speaking a person's memories back to them — remains in the bucket, publicly readable, after that person has exercised their right to erasure and been told *"All data has been permanently removed."*

That last part matters: the endpoint returns a factual claim that is not true.

---

### C8 — No production guard on secrets or environment.

**Principle violated:** P3, P4
**Severity:** Critical — one missed environment variable and every session cookie is forgeable.

`JWT_SECRET` defaults to `"dev-only-insecure-secret-change-me"` (`config.py:36`) and `SES_MOCK` defaults to `True` (`config.py:41`). Nothing validates these at startup.

The failure is silent and total: deploy with the default secret and anyone can mint a valid `katha_token` for any `user_id`, which grants full read access to that family's story archive and the ability to call the deletion endpoint against it. Deploy with `SES_MOCK=True` and magic-link emails are never sent — every family is locked out of the dashboard, and the only symptom is a log line.

Secondary: magic-link tokens are stored in plaintext (`magic_link_token`), and `/auth/magic-link` has no rate limit (`api/routes/auth.py:23–30`), making it usable as an email-bombing relay against arbitrary addresses.

---

### C9 — Sessions never terminate, so the daily conversation silently stops after the first one.

**Principle violated:** P5
**Severity:** Critical — kills the product's core loop within days, invisibly.

There is no session lifecycle. `sessions` has no `status` or `ended_at` column (`models/session.py`). "Active" is inferred as `session_end_suggested = false AND goal_met = false`, with no time bound (`session_manager.py:126–141`).

The scheduler skips a user entirely if that query returns a row (`session_initiator.py:63–70`).

So: a user starts a session and drifts off without the LLM ever emitting `session_end_suggested` — they got tired, the network dropped, the reply was the malformed-response fallback from C5. That session stays "active" **forever**. The scheduler skips that user **every day, permanently**. The 30-minute follow-up also stops firing, because it filters on `last_user_message_at IS NULL` and the user did reply once (`session_initiator.py:146`).

The user simply stops hearing from Katha. Nothing errors. Nothing alerts. The go/no-go criterion is *"≥50% of pilot users complete 10+ sessions."*

Two compounding defects in the same area:

- **Domain progression is hardcoded and never advances.** `start_session` always sets `session_number=1` and `domain=get_domain_sequence()[0]` (`session_manager.py:54–55`); the scheduler independently hardcodes `domain_sequence[0]` (`session_initiator.py:88`). Every session, forever, is session 1, "Childhood & Home." The 8-domain framework in `domains.py` and TECH_DESIGN §2.2 is never exercised.
- **`goal_met` uses the wrong accumulator.** It compares a *single turn's* atom count against the domain target (`session_manager.py:100–103`) rather than the session's cumulative count, so it fires early or never.

---

## 4. High-severity findings (fix before pilot, not architecture-level)

| ID | Finding | Where | Why it matters |
|----|---------|-------|----------------|
| **H1** | Blocking I/O on the async event loop — Twilio SDK `messages.create()`, all `boto3` calls, Pillow rendering, SES send | `whatsapp.py:67,82,109`, `storage.py:33,51`, `generator.py`, `auth.py:121` | One user's turn stalls the entire process, including the APScheduler job. With `misfire_grace_time=30` (`session_initiator.py:178`), a stalled loop causes *silently skipped session initiations*. |
| **H2** | Twilio signature validated against `str(request.url)` | `webhook.py:85` | Behind any TLS-terminating proxy the scheme is `http`, the signature never matches, and **every webhook 403s**. This will break on first deploy, not in production drift. |
| **H3** | No idempotency on `MessageSid` | `webhook.py` | Twilio retries and user double-sends both cause duplicate LLM calls, duplicate charges, and two voice replies to the same message. |
| **H4** | No timeout or retry on Anthropic; new `AsyncAnthropic` / `AsyncOpenAI` client per call | `llm.py:33`, `vector_store.py:19` | Any transient 429/503 becomes C4 (total silence). Per-call clients also discard connection pooling. |
| **H5** | Transcripts and audio are never persisted anywhere | no `turns` table exists | Makes the "≥75% extraction accuracy, manually validated on 50 atoms" gate unmeasurable, and makes every production bug unreproducible. |
| **H6** | Crisis detection is substring matching on one turn's transcript, in English + romanised Hindi only | `conversation_policy.py:25–41` | Users speak Devanagari, Tamil, Telugu. Native-script distress will not match. Given the population, a missed crisis signal is the most serious non-technical risk in the system. |
| **H7** | No conversation history within a session — the LLM receives one user message with no prior turns | `orchestrator.py:327` | Katha cannot reference what was said two minutes ago. Layer 2's "graceful repetition handling" and natural follow-up are structurally impossible. |
| **H8** | Production image runs `uvicorn --reload` | `backend/Dockerfile:13` | Dev server in production: file-watcher overhead, unpredictable worker restarts mid-session. |

---

## 5. Recorded as debt (do not fix before pilot)

These are real but correctly deferred at 10–20 families:

- **No indexes** on `sessions.user_id`, `sessions.whatsapp_number`, `story_atoms.user_id`, `memory_cards.user_id`. Sequential scans are free at this row count. *(Cheap enough to fix opportunistically.)*
- **No pgvector index** (`ivfflat`/`hnsw`). Exact scan is correct and fast below ~10k atoms.
- **No `users` table.** `user_id` is an unconstrained string with no referential integrity; `family_accounts.user_id` is not unique, so two accounts can silently point at the same elder. Worth fixing eventually; unlikely to bite in a curated pilot.
- **In-memory freemium cooldown** (`freemium.py:25`) — already documented in-code, correct for single-process.
- **In-process APScheduler** — fine at one replica. Note it breaks the moment a second replica exists: both will initiate sessions, and each user gets duplicate opening voice notes.
- **Synchronous webhook pipeline.** STT + LLM + TTS + ffmpeg + S3 + send runs inline, plausibly 8–20s against Twilio's ~15s webhook timeout. Because the reply is sent via the REST API rather than TwiML, a timeout degrades to a Twilio error log rather than a lost message. Acceptable for the pilot; the correct end-state is a queue, which is a Phase-8 restructure, not a pre-pilot fix.
- **No per-session API cost tracking**, despite the constraint in `CLAUDE.md`. Token counts are logged but never stored.
- **No structured logging or correlation IDs.** Debugging a specific family's bad session means grepping unstructured logs.

---

## 6. What is well designed

Worth stating plainly, because the remediation plan should preserve it:

- **Adapter boundaries are clean.** `sarvam_stt`, `sarvam_tts`, `llm`, `whatsapp`, `storage` are all narrow, swappable modules with a `Protocol` for the WhatsApp adapter and a working stub. Swapping Twilio for Meta Cloud API, or Sarvam's LLM for Claude, is a contained change. This is the single best structural decision in the codebase.
- **The prompt architecture is genuinely good.** Five layers, composed from typed dataclasses, with the therapeutic protocol expressed as explicit numbered principles. It is testable and it is legible to a non-engineer — which matters for a product whose quality *is* the prompt.
- **The deletion endpoint's failure isolation is thoughtful.** Each step is independently wrapped so one S3 error cannot strand the rest of a user's data, and consent records are anonymised rather than deleted for audit. The design intent is right; only the enumeration is incomplete (C7).
- **Enumeration protection on magic links is correctly placed** — the no-op lives inside `send_magic_link` with a comment explaining that nothing above it may branch on the result (`auth.py:57–64`). That is the right boundary.
- **Multi-script font resolution in memory cards** resolves fonts per word by Unicode script. Someone thought carefully about what a code-mixed Hindi-English quote actually looks like when rendered. This is the kind of detail that determines whether the product feels made-for-India or ported-to-India.
- **Test coverage is real** — 27 test files tracking the phase structure, plus lint and type gates in CI.

---

## 7. Recommendation

**Do not launch the pilot on the current build.** Not because the architecture is wrong — the shape is sound and the boundaries are in the right places — but because five defects in the turn pipeline (C1, C2, C3, C5, C9) mean the pilot would generate an empty archive, a silent bot, and no evidence about either. You would burn 15 families' goodwill and learn nothing.

The corrective work is bounded. See `docs/REMEDIATION_PLAN.md`. Estimated 5–8 working days, mostly in `orchestrator.py`, `session_manager.py`, `storage.py`, and a small number of new tables. No rewrite, no reshaping of the phase structure, no new infrastructure.

---

*Reviewed against `main` @ `fba83c7`.*
