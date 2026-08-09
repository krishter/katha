# Katha — Pre-Pilot Remediation Plan

**Input:** `docs/ARCHITECTURE_REVIEW.md`
**Scope:** Correctness and safety only. Do not refactor for scale, performance, or elegance.
**Target:** Pilot-ready at 10–20 families on a single backend instance.
**Estimate:** 5–8 working days.

---

## Instructions for the implementer

Read this whole file before starting. Then work the workstreams **in order** — WS1 through WS5. They are ordered by dependency and by risk, not by size.

Rules that apply throughout:

- One branch per workstream: `fix/ws1-turn-persistence`, etc. Never commit to `main`. PR per workstream. (`.claude/rules/git.md`)
- Write tests before marking any item complete. Run targeted tests during implementation, full suite before PR. (`.claude/rules/testing.md`)
- Run `ruff check . && ruff format --check .` before every commit.
- Every item below has an **Acceptance** block. An item is not done until its acceptance criteria pass as an automated test, unless the criterion explicitly says manual.
- **Do not expand scope.** If you find something broken that is not in this plan, add it to a `## Found during remediation` section at the bottom of this file and keep going. Several known issues are deliberately excluded — see *Explicitly out of scope* at the end.
- After WS1 and WS3, re-run the TC-01–TC-10 eval set (`eval-runner` subagent). Prompt-adjacent changes in those workstreams can regress conversation quality.

---

## WS1 — Make story capture lossless

**Fixes:** C1, C2, C3
**Why first:** Everything else is worthless if stories are not being saved. This is also the workstream most likely to reveal further problems, so it should surface early.

---

### 1.1 — Add a `turns` table and persist every turn

Create `models/turn.py` + migration. Columns:

| Column | Type | Notes |
|---|---|---|
| `id` | UUID PK | |
| `session_id` | UUID FK → `sessions.id` ON DELETE CASCADE | |
| `user_id` | String, indexed | |
| `turn_number` | Integer | monotonic within session |
| `inbound_message_sid` | String, **unique**, nullable | Twilio `MessageSid`; the idempotency key for H3 |
| `transcript` | Text | STT output |
| `detected_language` | String | |
| `response_text` | Text | what Katha said |
| `extraction_json` | JSONB | raw extraction block for this turn |
| `input_tokens`, `output_tokens` | Integer | closes the cost-tracking gap in `CLAUDE.md` |
| `created_at` | timestamptz | |

Write the turn row inside `process_voice_turn`, **after** the LLM call and **before** TTS. TTS failure must not lose the transcript.

Add `turns` to the deletion sweep in `api/routes/admin.py`.

**Acceptance:**
- A 5-turn session produces exactly 5 `turns` rows with `turn_number` 1–5.
- Killing the process immediately after the LLM call still leaves the transcript and extraction persisted.
- `DELETE /user/{user_id}` removes all `turns` rows for that user.

---

### 1.2 — Persist story atoms per turn, not at session close

This is the core fix for C1.

Move the `process_extraction` call out of `run_post_session` and into `process_voice_turn`, immediately after the turn row is written. Every turn's atoms go to the database as that turn completes.

`run_post_session` keeps only the work that genuinely needs the whole session: entity extraction (now over the **concatenated transcript of all turns**, read from `turns` — not the single final transcript it currently receives) and memory card generation.

Make `process_extraction` idempotent: add a `turn_id` FK to `story_atoms` and skip insertion if atoms already exist for that `turn_id`.

**Acceptance:**
- A 5-turn session where turns 2, 3, and 4 each produce story atoms results in all of those atoms being present in `story_atoms` — verified by a test that asserts atoms from a non-final turn are persisted.
- Re-running `process_extraction` with the same `turn_id` inserts nothing new.
- Entity extraction receives text from all turns, not just the last one.

---

### 1.3 — Remove the duplicate session-close path

Fixes C2.

Delete the `background_tasks.add_task(run_post_session, ...)` block in `process_voice_turn` (`orchestrator.py:393–399`). Session close is scheduled by exactly one caller: the webhook (and `/conversation/close`, if that route survives WS3).

`close_and_process_session` becomes the single entry point, and it must be guarded — see 1.5.

**Acceptance:** A session that ends produces exactly one memory card and one entity-extraction pass. Assert with call counts on mocks.

---

### 1.4 — Fix embedding writes

Fixes C3. This one is subtle — read the finding in the review before touching it.

Delete the `asyncio.create_task(...)` fire-and-forget block in `story_extractor.py:84–88`. Those tasks resume after the request-scoped `AsyncSession` is torn down and fail with a use-after-close error that is then swallowed.

Replace with either:
- **(a)** `await` the embedding inline within the same DB session — simplest, correct, and adds ~200ms to an already-async path; or
- **(b)** a background task that opens its **own** session from `AsyncSessionLocal`.

Prefer (a) unless it measurably hurts turn latency.

Additionally: change `_embed_atom_safe`'s handler from a silent `logger.exception` to `logger.error` **plus** an `embedding_failed` boolean column on `story_atoms`, so failures are queryable rather than buried in logs.

**Acceptance:**
- After a session, `SELECT count(*) FROM story_atoms WHERE embedding IS NULL` returns 0.
- `vector_store.retrieve_relevant` returns non-empty results for a user with prior sessions.
- **Manual, and this is the important one:** run two sessions for the same test user and confirm Layer 3 of the system prompt in session 2 contains real facts from session 1 — not the "This is an early session" fallback. Log the assembled prompt and read it.

---

### 1.5 — Add explicit session lifecycle

Fixes C9. Without this, WS1's other fixes still leave users stranded after one session.

Add to `sessions`:
- `status` — enum/string: `active` | `completed` | `abandoned`
- `ended_at` — timestamptz, nullable
- `ended_reason` — string, nullable: `goal_met` | `llm_suggested` | `timeout` | `manual`

Then:

1. `get_active_session_by_number` filters on `status = 'active'` **and** `started_at > now() - interval '4 hours'`. Never rely on boolean inference again.
2. `close_and_process_session` sets `status = 'completed'`, `ended_at`, `ended_reason`.
3. Add a scheduler job that marks any `active` session older than 4 hours as `abandoned`. **A stale session must never block tomorrow's conversation.**
4. Fix `goal_met`: count cumulative `story_atoms` for the session against `domain.target_story_atoms`, not the current turn's atom count (`session_manager.py:100–103`).

**Acceptance:**
- A session left `active` with no user reply is marked `abandoned` within 4 hours, and the user receives their scheduled session the next day. Test with a frozen clock.
- `goal_met` becomes true only when the session's cumulative atom count reaches the domain target.
- No code path outside `session_manager` reads `session_end_suggested` or `goal_met` to determine whether a session is active.

---

### 1.6 — Advance session number and domain

Also C9. Currently every session is session 1, "Childhood & Home."

`start_session` must:
1. Count the user's prior `completed` sessions → `session_number = count + 1`.
2. Select the domain: advance to the next in `get_domain_sequence()` when the current domain's cumulative atom count across all sessions meets its target; otherwise stay.
3. Delete the hardcoded `domain_sequence[0]` in `session_initiator.py:88` and use the session's actual domain to build the opening prompt.

Keep the selection rule simple and deterministic. Do not build an adaptive domain planner.

**Acceptance:**
- Session 3 for a user with two completed sessions has `session_number = 3`.
- A user who has met the `childhood` target starts their next session in `family_ancestors`.
- The opening voice note text reflects the selected domain, not always `childhood`.

---

## WS2 — Make failure visible to the user

**Fixes:** C4, C5
**Why second:** These are the defects the pilot user actually experiences.

---

### 2.1 — Split dialogue from extraction

Fixes C5. This is the one structural change in the plan, and it is worth it.

The current design asks one 500-token call to produce both a warm conversational reply and a complete extraction JSON. It cannot, and the truncation failure is user-visible.

Split into two calls:

- **Dialogue call** — on the critical path. Returns `<response>` only. `max_tokens=300`. Fast, warm, low latency.
- **Extraction call** — off the critical path, fired as a background task after the reply is sent. Returns extraction JSON only. `max_tokens=2000`. Latency-tolerant.

This also removes extraction latency from the turn budget, which helps H2/the Twilio timeout margin.

Keep `check_post_turn`'s malformed-response guard for the dialogue call. Give the extraction call its own guard that retries once with a stricter instruction before giving up.

**Acceptance:**
- A turn where the user tells a long, detailed story produces both a complete conversational reply **and** a complete, parseable extraction block.
- Add a regression test with a ~400-word story transcript asserting no truncation and no `_MALFORMED_RESPONSE` fallback.
- Turn latency (STT → reply sent) drops measurably. Record before and after in the PR description.

---

### 2.2 — Guarantee a reply on every failure path

Fixes C4. **The elderly user must never receive silence.**

Restructure the webhook's exception handling into staged fallbacks:

| Stage that failed | User receives |
|---|---|
| STT | Voice note: *"I'm sorry, I couldn't quite hear that. Could you try sending it again?"* |
| LLM | Voice note: *"I'm having a little trouble right now. Give me a moment and try again?"* |
| TTS or audio conversion | **Text** message with the same content (degrade the channel, not the response) |
| Anything else | Text: *"Something went wrong on my side. I'll be here tomorrow at our usual time."* |

Requirements:
- Pre-synthesise the fallback audio clips per supported language at startup, or cache them. Do not call TTS to apologise for TTS failing.
- The fallback sender itself must be wrapped so its failure cannot re-enter the handler.
- Every fallback logs at ERROR with `session_id`, `turn_number`, and the failing stage.

**Acceptance:** A test that injects a failure at each of STT, LLM, TTS, and audio-conversion, and asserts an outbound message is sent in every case. This test is the single most important one in the suite — treat it as such.

---

### 2.3 — Add timeouts and one retry to every external call

Fixes H4.

- Anthropic: construct **one** module-level `AsyncAnthropic` with `timeout=20.0, max_retries=2`. Same for `AsyncOpenAI` in `vector_store.py`. Stop constructing a client per call.
- Sarvam STT/TTS: keep the 30s timeout, add one retry on connection error and 5xx only. Never retry a 4xx.
- Bound the `ffmpeg` subprocess with `asyncio.wait_for` (10s) and kill on timeout — currently it can hang indefinitely.

**Acceptance:** With a mock that fails once then succeeds, the turn completes. With a mock that always fails, the correct 2.2 fallback fires within the timeout budget.

---

### 2.4 — Widen crisis detection

Fixes H6. Not architecture, but the population makes it non-negotiable.

- Add native-script crisis phrases for Devanagari, Tamil, Telugu, Bengali, and Marathi at minimum. Get these reviewed by a native speaker — do not machine-translate distress language and ship it.
- Run the crisis check on the **LLM's response** as well as the user's transcript. If Katha ever generates content resembling encouragement of self-harm, the crisis override must take precedence.
- Log every crisis detection to a dedicated `crisis_events` table with `session_id`, `turn_id`, matched pattern, and timestamp. Someone must be able to review these during the pilot.

**Acceptance:** Crisis phrases in each supported script trigger the override and the iCall referral. `crisis_events` rows are written. Existing TC test for crisis handling still passes.

---

## WS3 — Close the security and consent gaps

**Fixes:** C6, C7, C8

---

### 3.1 — Remove or authenticate `/conversation/*`

Fixes C6.

Preferred: **delete `api/routes/conversation.py` and unmount it.** WhatsApp is the only production surface; these routes are Phase-1 scaffolding. Their tests move to exercising the orchestrator directly.

If they are still needed for manual testing, then: require `get_current_user`, derive `user_id` from the JWT (never from a form field), verify the caller owns the `session_id`, and mount them only when `settings.ENVIRONMENT != "production"`.

**Acceptance:** In a production-configured app, `POST /conversation/session` returns 404 or 401. No route accepts a caller-supplied `user_id`.

---

### 3.2 — Make S3 objects private and tracked

Fixes C7. Two independent problems; fix both.

**Private access.** Remove `ACL="public-read"` from `storage.upload_media`. Return a presigned URL (15-minute expiry) for Twilio to fetch. For the dashboard, add an authenticated backend route that checks JWT ownership and issues a short-lived presigned URL — do not store or serve permanent public URLs.

Audit the bucket's Block Public Access settings while you are here.

**Full enumeration.** Every uploaded object must be recorded in the database so it can be deleted:
- Add `response_audio_s3_key` to `turns` and populate it on every voice note send.
- Extend `admin.delete_user` to sweep those keys alongside `memory_cards.image_s3_key`.
- Write a one-off script to purge existing orphaned `audio/` objects from the pilot bucket.

`memory_cards.image_public_url` should be dropped or renamed — a column named "public url" should not exist after this change.

**Acceptance:**
- A direct unauthenticated GET to any S3 object URL returns 403.
- `DELETE /user/{user_id}` leaves **zero** objects in the bucket for that user. Verify by listing the bucket in the test, not by trusting the return value.
- The dashboard still renders memory card images for an authenticated family member.

---

### 3.3 — Fail fast on unsafe production config

Fixes C8.

Add a startup validator in `config.py` or `lifespan` that **raises and refuses to boot** when `ENVIRONMENT == "production"` and any of:

- `JWT_SECRET` equals the default or is shorter than 32 characters
- `SES_MOCK` is `True`
- `WHATSAPP_ADAPTER == "stub"`
- `ANTHROPIC_API_KEY`, `SARVAM_API_KEY`, `OPENAI_API_KEY`, or the Twilio credentials are empty
- `APP_BASE_URL` is not `https://`

Refusing to start is correct here. A backend running with a default JWT secret is worse than a backend that is down.

Also: hash magic-link tokens at rest (store SHA-256, compare on lookup), and add a simple per-email rate limit to `POST /auth/magic-link` — 3 per hour, a `last_requested_at` column is sufficient at this scale.

**Acceptance:** Production config with any unsafe value fails at startup with a clear message naming the variable. Development config is unaffected. Magic links still work end to end with hashed storage.

---

## WS4 — Fix deployment-blocking defects

**Fixes:** H1, H2, H3, H8

---

### 4.1 — Fix Twilio signature validation behind a proxy

Fixes H2. **This will break on first deploy if not fixed.**

`str(request.url)` yields `http://` behind a TLS-terminating load balancer, so the computed signature never matches Twilio's and every webhook returns 403.

Add a `PUBLIC_BASE_URL` setting and construct the validation URL from it plus the request path. Do not trust `X-Forwarded-Proto` unless you also configure `--proxy-headers` with an explicit trusted-host list.

**Acceptance:** A test simulating a proxied request (`X-Forwarded-Proto: https`, `http` scheme on the raw request) validates successfully. A genuinely invalid signature still 403s.

---

### 4.2 — Add webhook idempotency

Fixes H3. Depends on `turns.inbound_message_sid` from 1.1.

On inbound webhook, check whether a turn already exists for that `MessageSid`. If so, log and return 200 without reprocessing. Take a row-level lock or use the unique constraint to close the race between concurrent retries.

**Acceptance:** Posting the same webhook payload twice produces one turn row, one LLM call, and one outbound message.

---

### 4.3 — Move blocking calls off the event loop

Fixes H1. Do the minimum: wrap, do not rewrite.

Wrap in `asyncio.to_thread(...)`: Twilio `messages.create` (all three call sites in `whatsapp.py`), `boto3` `put_object`/`delete_object`/`generate_presigned_url`, SES `send_email`, and `generator.render_card` (Pillow is CPU-bound and takes hundreds of ms).

Do not migrate to `aioboto3` or the Twilio async client — out of scope.

**Acceptance:** A load test of 5 concurrent turns shows no turn blocked behind another for the duration of a Twilio API call. The APScheduler job continues to fire on schedule during concurrent turns — this is the failure mode that matters.

---

### 4.4 — Production-ready container command

Fixes H8. Remove `--reload` from the production `CMD`. Use `--workers 1` explicitly (required — APScheduler is in-process and multiple workers would double-send every scheduled session). Keep `--reload` in `docker-compose.yml` for local development only.

Add a comment in `main.py` next to the scheduler startup stating the single-worker constraint, so it is not violated later by accident.

**Acceptance:** Production image runs without the reloader. A second worker is impossible to configure by accident without hitting the comment.

---

## WS5 — Verification

Do not skip this. WS1 and WS2 change behaviour that the existing tests do not cover.

### 5.1 — End-to-end pilot rehearsal

Build a test that drives a complete realistic session through the stub adapter: 6 turns, a substantial story in turns 2–4, natural wind-down in turns 5–6. Assert:

- 6 `turns` rows persisted with transcripts
- Story atoms from turns 2, 3, **and** 4 present in `story_atoms` — not just the last turn
- Every atom has a non-null `embedding`
- Exactly one memory card generated and delivered
- Session `status = 'completed'` with `ended_reason` set
- No duplicate rows in any table

### 5.2 — Failure-injection suite

Parametrised test injecting failure at each stage — STT, LLM, TTS, ffmpeg, S3, Twilio send — asserting in every case: (a) an outbound message reaches the user, (b) an ERROR is logged with session context, (c) no partial or corrupt rows are left behind.

### 5.3 — Two-session continuity check (manual)

Run two real sessions for one test user, a day apart. Read the assembled Layer 3 prompt for session 2 and confirm it contains actual facts and open threads from session 1. **This is the check that proves C3 is fixed and that Katha's memory works at all.** Automated tests will not catch a subtly empty context block. Paste the assembled prompt into the PR.

### 5.4 — Eval regression

Run TC-01 through TC-10 via the `eval-runner` subagent. Target per `.claude/rules/testing.md`: 80%+ objective, 75%+ rubric. WS2.1's dialogue/extraction split is the most likely source of regression — if scores drop, tune the dialogue prompt, do not revert the split.

### 5.5 — Consent audit (manual)

Create a full test user with sessions, atoms, cards, and voice notes. Call `DELETE /user/{user_id}`. Then verify by direct inspection — not by the endpoint's return value — that: no rows remain in any table except the anonymised `consent_records`, and the S3 bucket contains zero objects for that user.

---

## Explicitly out of scope

Do not do these. They are either correctly deferred or genuine Phase-8 work.

- Queue-based turn processing (Celery/SQS/Redis). The synchronous pipeline is acceptable at 10–20 families and WS2.1 reduces its latency.
- Multi-replica support, distributed scheduler, distributed locking.
- A `users` table and referential-integrity cleanup.
- pgvector `ivfflat`/`hnsw` indexes.
- Streaming STT/TTS.
- Structured logging, tracing, metrics dashboards.
- Moving the freemium cooldown out of memory.
- Any prompt-engineering improvement not required to fix a listed defect.

Adding indexes on the four unindexed foreign-key-ish columns is optional and cheap — take it if you are already writing a migration.

---

## Suggested sequencing

| Days | Workstream | Ship gate |
|---|---|---|
| 1–3 | WS1 — lossless capture | Stories from every turn reach the archive with embeddings |
| 3–5 | WS2 — visible failure | No silence, no truncation, no untimed calls |
| 5–6 | WS3 — security and consent | No public data, no unauthenticated route, no unsafe boot |
| 6–7 | WS4 — deployment blockers | Webhooks validate behind a proxy; no duplicate processing |
| 7–8 | WS5 — verification | 5.1–5.5 pass; eval set at target |

WS1 must complete before WS2.1 (extraction split depends on per-turn persistence) and before WS4.2 (idempotency depends on the `turns` table). WS3 is independent and can be parallelised if a second implementer is available.

---

## Found during remediation

<!-- Append anything discovered that is not covered above. Do not fix in-scope. -->

- **(WS3.2) Session-open voice note's S3 key is untracked.** `send_voice_note`
  now returns `(message_sid, s3_key)` and turn-level replies persist that
  key on `turns.response_audio_s3_key`, but `scheduler/session_initiator.py`'s
  opening voice note has no `Turn` row to attach to — its key is discarded
  (`_s3_key` unpacked and ignored). The object is private (no public ACL,
  per the fix applied everywhere), just not enumerable for per-user
  deletion. Same class of bug as the one WS3.2 fixed for turns; would need
  a column on `sessions` (e.g. `session_open_audio_s3_key`, mirroring the
  existing `session_open_message_id`).
- **(WS3.2) Memory-card delivery uploads the image twice, under two keys.**
  `core.orchestrator._generate_and_deliver_memory_card` uploads the card to
  `cards/{session_id}.png` (tracked in `memory_cards.image_s3_key`, swept
  on deletion). `TwilioWhatsAppAdapter.send_image` then uploads the *same*
  bytes again under a random `cards/katha-{uuid}.png` key just to get
  something to hand Twilio — that second object is private now (no more
  public ACL) but has no tracking column and is never deleted. Cleanest
  fix is probably to change `send_image`'s signature to accept a
  presigned URL directly (the caller already has the key) instead of raw
  bytes, eliminating the redundant upload rather than tracking it.
- **(WS3.2) S3 bucket's Block Public Access setting needs a manual audit.**
  The architecture review's own instruction ("Audit the bucket's Block
  Public Access settings while you are here") is an AWS console/IAM
  action, not something a code change can verify. `storage.upload_media`
  no longer sends `ACL=public-read`, but if Block Public Access is off at
  the bucket level, a future regression could re-expose objects silently.
  Needs a manual check before pilot launch — flagging rather than
  claiming it's done.
- **(WS5.4) `named_entities` has no schema in `build_extraction_prompt` and
  is dead code.** Live eval runs (TC-06) confirmed `story_extractor.process_extraction`
  never reads `extraction_json["named_entities"]` at all — the real
  fact-store-feeding path is the separate, once-per-session
  `entity_extractor.extract_entities` call, which has its own schema
  (`people`/`places`/`dates`/`institutions`) with no slot for
  household-composition facts (e.g. "joint family, 15 people"). Fixing
  this properly means deciding whether the per-turn `named_entities` field
  should be wired into `process_extraction`/`fact_store` at all (risking a
  second, possibly-conflicting write path into the same fact store), or
  dropped entirely in favor of extending `entity_extractor`'s schema.
  Deferred as a genuine design decision, not a schema typo like the
  `story_atoms` fix this workstream did make.
- **(WS5.4) TC-06's transcription-accuracy gap is not a prompt issue.**
  With the code-mixed text fed directly into the extraction call
  (bypassing STT), entity extraction correctly captures `joint`/`15` — so
  the remaining TC-06 failure is Sarvam Saaras V3's transcription accuracy
  on code-mixed Hindi-English audio, which `system_prompt.py` has no
  influence over. Out of scope for this workstream; would need real
  code-mixed audio samples and Sarvam-side evaluation to address.
- **(WS5.4) TC-09 closing instruction loses to follow-up pull when the
  user's last line still carries narrative momentum.** The Layer 4
  closing/preview instruction (fires when `goal_met=True`) works reliably
  when the user's own words signal they're done for now, but in 8/8 live
  samples where the user's last utterance was a very ordinary
  mid-story-sounding line (e.g. "...it took almost a year, but it worked
  out"), Katha asked another follow-up question instead of closing. This
  is a real gap for a product whose default state is "user is still
  talking" — worth a dedicated regression case (TC-09b) and a stronger
  instruction (e.g. "even if their last answer feels unfinished, do not
  ask another question this turn — the domain goal is already met")
  before pilot, but is additional prompt-tuning beyond this workstream's
  chartered defects.
