# Cost-to-serve update: spec

**Status:** Ready to build · **Owner:** Krish · **Written:** 2026-10-16
**Branch:** `feature/cost-to-serve`, one PR. Never commit to `main`; see `.claude/rules/git.md`.
**Carries:** `docs/specs/ADMIN_METRICS.md`, built as written except where §12 below amends it. Do not open a separate `feature/admin-metrics` PR.
**Background:** the "Katha cost to serve" analysis (2026-10-16), recommendations 1–7. Recommendation 8 (leaving Twilio) is out of scope.

---

## 1. Why

A typical session costs about **₹43** to serve today. "Typical" means session 30, a Hindi speaker, 7 exchanges and about 10 minutes of her speech. At the PRD's 200 sessions a year, that is about ₹9,500 per family against a ₹5,000 plan.

The money goes to four places:
- **Claude: 54%.** Two full-price Sonnet 4.6 calls per turn, with no caching.
- **Twilio's per-message fee: 18%.**
- **Sarvam TTS: 16%.**
- **Sarvam STT: 11%.**

This update does two things:

1. **Measure every paid call.** Today the only record is `turns.input_tokens/output_tokens`. That covers part of the Claude spend and none of Sarvam's or Twilio's. Every number above is a model estimate, and after this PR it should come from the ledger.
2. **Cut the cost of a session to about ₹23.** The estimate includes the newer tokenizer, which produces about 30% more tokens on the 5.5 models; see §16.

Decided 2026-10-16:
- Stay on the **Anthropic API with the 5.5 models**, not Bedrock India.
- **The parent initiates.** Katha does not message first, so no session-open template is needed (Option B in `docs/proposals/outbound-messaging-window.md`).

## 2. Scope

**In**

| Id | Change | Expected saving at session 30 |
|---|---|---|
| A | Usage ledger: one row per paid call, across all four vendors | (enables measurement) |
| B | Admin metrics endpoint (`ADMIN_METRICS.md`), with the full-cost section in §12 | (enables measurement) |
| C1 | Per-turn extraction and entity extraction on Claude Haiku 5.5 | ~₹11 |
| C2 | Bound Layer 3 and freeze it for the session | ~₹2 (₹6 at session 100), and stops growth |
| C3 | Prompt caching on the dialogue call | ~₹2 |
| C4 | Dialogue on Claude Sonnet 5.5 | ~₹2 |
| C5 | Parent initiates: stop the scheduled opener and the 30-minute nudge, which Meta rejects outside the 24-hour window | ~₹1.28 |
| C6 | Sarvam Bulbul v4 Flash behind a flag, plus a cache for fixed system phrases | ~₹2 once the flag is flipped |
| C7 | Shorten long pauses before STT | ~₹0.7 |

**Out**
- Leaving Twilio (recommendation 8).
- Bedrock India.
- Consolidation pipeline changes.
- Structured outputs.
- Any change to prompt *wording*. C2 changes how facts are *rendered*. C3 changes nothing in the prompt text.
- **The privacy copy is inaccurate today and is tracked separately.** It says "All data is stored in India", but transcripts go to Anthropic's API, which stores at rest in the US. This PR doesn't change that, and it shouldn't be forgotten.

## 3. Non-negotiables

1. **Every behaviour change can be rolled back with an env change and a restart, no redeploy.** Each of C1–C7 has a setting (§4). The old path stays in the code until the pilot has run a month on the new one.
2. **Eval gate before merge.** TC-01 to TC-10 must meet the targets in `.claude/rules/testing.md` (80% objective, 75% rubric). They must also show no regression against `evals/results/20261015T123452Z.json`. C1 additionally runs `evals/run_consolidation_eval.py`. Commit the result files.
3. **The ledger holds no personal data.** It stores `user_id`, `session_id`, `turn_id`, counts, model names and message SIDs. No text, no phone numbers.
4. **Recording usage can never cost a turn.** Every ledger write is wrapped, logged on failure, and never raises.
5. **Never silence (P2).** Every new failure path degrades the way the existing ones do.
6. **DPDP deletion covers the ledger.** `DELETE /user/{user_id}` deletes that user's `api_usage` rows.

## 4. Settings (`config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `LLM_DIALOGUE_MODEL` | `claude-sonnet-5-5` | Rollback value: `claude-sonnet-4-6` |
| `LLM_DIALOGUE_EFFORT` | `medium` | `output_config.effort` for dialogue. The eval compares `low` and `medium`; keep whichever passes at the lower cost. |
| `LLM_EXTRACTION_MODEL` | `claude-haiku-5-5` | Per-turn extraction, its retry, and entity extraction. Rollback value: `claude-sonnet-4-6`. |
| `LLM_EXTRACTION_EFFORT` | `low` | Haiku 5.5 has no thinking-off switch; low effort is the lever |
| `PROMPT_CACHE_ENABLED` | `true` | C3 |
| `PROMPT_CACHE_TTL` | `1h` | `5m` or `1h`. Parents take minutes between voice notes. |
| `LAYER3_FREEZE_PER_SESSION` | `true` | C2 snapshot. `false` rebuilds per turn as today. Rendering caps always apply. |
| `SCHEDULED_OPENER_ENABLED` | `false` | C5. `true` restores today's daily voice opener and nudge. |
| `TTS_MODEL` | `bulbul:v3` | Flip to `bulbul:v4-flash` only after the listening test in §10 |
| `STT_TRIM_PAUSES` | `true` | C7 |
| `STT_MAX_PAUSE_SECONDS` | `1.0` | Pauses longer than this are shortened to 0.5 s |
| `USD_INR_RATE` | `95.06` | Used only for reporting |

Remove `LLM_PRICE_INPUT_PER_MTOK_USD` and `LLM_PRICE_OUTPUT_PER_MTOK_USD` from `ADMIN_METRICS.md` §4. Prices now live in `core/pricing.py` (§12.1).

Add every new setting to `.env.example` with a comment.

## 5. Change A: usage ledger

### 5.1 Table `api_usage`

| Column | Type | Notes |
|---|---|---|
| `id` | uuid pk | |
| `created_at` | timestamptz, default now | indexed |
| `user_id` | text, null | Indexed. Null only for calls with no user (startup preloads). |
| `session_id` | uuid, null | **No FK.** Rows outlive nothing, because deletion removes them by `user_id`. |
| `turn_id` | uuid, null | Set when known |
| `vendor` | text | `anthropic` · `sarvam` · `twilio` |
| `operation` | text | See 5.2 |
| `model` | text, null | `claude-haiku-5-5`, `saaras:v3`, `bulbul:v4-flash`, … |
| `units` | jsonb | Counts only. See 5.2. |
| `message_sid` | text, null | Indexed, for status callbacks |
| `status` | text, null | Twilio's final status, from the callback |
| `error_code` | text, null | |

### 5.2 Operations and units

| Vendor | `operation` | `units` |
|---|---|---|
| anthropic | `dialogue`, `extraction`, `extraction_retry`, `entities`, `consolidation`, `eval` | `input_tokens`, `output_tokens`, `cache_creation_input_tokens`, `cache_read_input_tokens`, `cache_ttl`, `latency_ms`, `stop_reason` |
| sarvam | `stt` | `seconds_received`, `seconds_billed` (after C7), `chunks` |
| sarvam | `tts_reply`, `tts_opener`, `tts_system`, `tts_fallback` | `characters` |
| twilio | `inbound` | `messages: 1` |
| twilio | `outbound_voice`, `outbound_text`, `outbound_template`, `outbound_card` | `messages: 1`, plus `meta_category` (`utility` / `marketing`) when it is a template |

Thinking tokens are billed as output and arrive inside `output_tokens`. Nothing separate is needed.

### 5.3 How calls get recorded

- `core/usage.py` provides `usage_scope(user_id=None, session_id=None, turn_id=None)`, a context manager over a `ContextVar`, and `async def record(vendor, operation, units, *, model=None, message_sid=None)`.
- `record` reads the current scope and writes in **its own short-lived `AsyncSessionLocal` session**. A failed ledger write therefore can never roll back or poison the request's transaction. It catches everything and logs at warning.
- **The adapters record their own calls.** That way any future call site is covered automatically:
  - `adapters/llm.chat` records after every response, using a required `purpose` argument as the operation.
  - `adapters/sarvam_stt.transcribe` records seconds.
  - `adapters/sarvam_tts.synthesize` records characters under a required `purpose`.
  - `TwilioWhatsAppAdapter.send_*` records one outbound row per message, with its SID.
  - `WhatsAppStubAdapter` records too, so tests can assert on it.
- **Entry points set the scope:**
  - `whatsapp_incoming`, once the profile and session are known. Also record the `inbound` row here, after the idempotency check.
  - `initiate_sessions`, per profile.
  - `run_extraction_for_turn` and `produce_session_outputs`, which run in background tasks; set the scope explicitly rather than relying on inheritance.
  - `process_voice_turn` widens the scope with `turn_id` after `_persist_turn`. That means the dialogue row carries `session_id` but no `turn_id`, which is fine.
- **The status callback** (`twilio_status_callback`) sets `status` and `error_code` on the matching row by `message_sid`, as well as calling `record_card_delivery` as today.
- Keep writing `turns.input_tokens/output_tokens` as today, for compatibility.

## 6. Change C1: extraction on Haiku 5.5 (and the `llm.chat` refactor C3/C4 also need)

**New signature:**

```python
async def chat(messages, system=None, max_tokens=500, *, model: str, purpose: str,
               effort: str | None = None, cache_system_prefix: str | None = None) -> LLMResponse
```

- `model` and `purpose` are required keywords. Update every call site:
  - `core/orchestrator.py`: dialogue, extraction and its retry.
  - `extraction/entity_extractor.py`
  - `scripts/consolidate_stories.py`
  - `evals/run_eval.py` and `evals/run_consolidation_eval.py`, which read the same settings so the evals test what production runs.
  - The patch target in `scripts/pilot_rehearsal.py`.
- **Sampling.** Send `temperature=0.7` only for legacy models (`claude-sonnet-4*`, `claude-haiku-4*`). The 5.5 models return a 400 for any non-default value. Put the rule in `_legacy_sampling(model)` so the rollback path behaves exactly as today.
- **Thinking.**
  - Sonnet 5.5 dialogue: `thinking={"type": "between_tools"}` with `output_config.effort = LLM_DIALOGUE_EFFORT`. This means no up-front thinking, which keeps latency and output cost down. Sending `{"type": "disabled"}` returns a 400.
  - Haiku 5.5: leave thinking unset (adaptive) and set `output_config.effort = LLM_EXTRACTION_EFFORT`.
  - Legacy models: send neither field.
- **SDK.** Keep the `anthropic<1.0` pin; its comment explains why. If the pinned SDK doesn't accept `output_config` or the `between_tools` thinking type as typed arguments, pass them through `extra_body`.
- **Response text.** Responses may now begin with `thinking` blocks. Replace `response.content[0].text` with the concatenated text of all `type == "text"` blocks. If there are none (thinking hit `max_tokens`), return an empty string with `stop_reason="max_tokens"`, so `conversation_policy` takes its existing truncation path.
- **`max_tokens`.** Thinking counts toward it, and the new tokenizer yields about 30% more tokens:

  | Call | Old | New |
  |---|---|---|
  | Dialogue | 1,200 | 1,600 |
  | Extraction | 2,000 | 4,000 |
  | Entity extraction | 500 (default) | 1,500 (explicit) |

- `LLMResponse` gains `model`, `cache_creation_input_tokens` and `cache_read_input_tokens`.
- Extraction's JSON contract and parsing are unchanged.
- **Eval addition.** If the TC set doesn't already check it, assert that every non-null `verbatim_quote` is a substring of the turn transcript, using Unicode NFC comparison. Haiku must not paraphrase a parent's words in Devanagari or Tamil.

## 7. Change C2: bound Layer 3 and freeze it per session

**Freeze**
- Add `sessions.prior_context` (jsonb, null).
- On a session's first turn, `build_prior_context` runs as today and the result is stored in that column. Every later turn in the session reads it back.
- Sessions that started before the deploy have null and get filled on their next turn.
- Atoms extracted mid-session are already in the conversation itself, and the next session picks them up.
- **A behaviour change to check in the eval.** A significant person first flagged *during* the current session is no longer available as the Layer 4 recall anchor until the next session. The anchor already ranked such people last (`_pick_recall_anchor`). Confirm TC-03 and TC-11 are unaffected.

**Bound** (in `prompts/system_prompt.py`, always on)
- Render the fact store as compact lines, not Python reprs: `People: Kamala (sister); Mr. Iyer (teacher); …`, one line per category.
- Caps:
  - People: the first 10 (earliest, usually closest family) plus the last 5.
  - Places: first 5 plus last 5.
  - Dates: last 8.
  - Institutions: first 5.
- Other keys: up to 5 values each.
- De-duplicate case-insensitively when rendering.
- The render-coverage test in `tests/test_system_prompt.py` must still pass.
- **New test.** With a synthetic session-100 fact store (70 people, 50 places, 50 dates, 20 institutions), the whole dialogue system prompt stays under 8,000 characters. Today it is about 18,300.

## 8. Changes C3 and C4: caching and Sonnet 5.5 on the dialogue call

- **No layer reorder is needed.** Layers 1–3 are stable within a session once C2 freezes Layer 3, and Layers 4–5 already come last.
- `build_system_prompt` returns two parts: `stable` (L1+L2+L3) and `per_turn` (L4+L5). Keep a joined `str` helper for the eval runner and any other caller that wants one string.
- `llm.chat` sends `system` as two text blocks, with `cache_control: {"type": "ephemeral", "ttl": PROMPT_CACHE_TTL}` on the first.
- The stable part is about 1,300+ tokens, comfortably above Sonnet 5.5's 512-token minimum. (Sonnet 4.6's minimum is 1,024, so the rollback path still caches once a user has some history.)
- **Expected result:** a cache write on turn 1, and reads on turns 2 onward whenever replies come within the TTL.
- `LLM_DIALOGUE_MODEL=claude-sonnet-5-5`. Layer 5's "begin with `<response>`, no reasoning" instruction stays.
- **Latency.** Log `latency_ms` (it is in the ledger) and compare p50/p95 against a pre-change run. p95 must not rise more than 20%.
- **Manual check before merge.** Listen to 10 replies each in Hindi and Tamil, generated by the eval runner and synthesised. The bar is warmth and register, not just correctness.

## 9. Change C5: the parent initiates

**With `SCHEDULED_OPENER_ENABLED=false` (the default):**
- `initiate_sessions` still runs every minute, but it does only two things:
  - Step 0, the stale-session sweep.
  - Step 4, producing outputs for abandoned sessions.
- It skips the due-profile loop: no session start, no TTS, no send. It also skips `_send_followups`.
- Sessions open only when the parent sends a voice note, through the existing on-demand path (`_open_session_on_demand`). The one-a-day rule, consent gate and freemium gate are unchanged.

**With `true` (rollback):** today's behaviour, with one fix. The opener goes through `convert_wav_to_ogg`; today the raw WAV is sent labelled `audio/ogg`. Use `purpose="tts_opener"`. That way a rollback restores a path that works.

Keep the `_send_followups` code and the `followup_sent_at` column. They sit behind the flag.

**Product follow-ups, not in this PR:**
- The onboarding wizard asks the family for a scheduled time, and its copy implies Katha will call every day. It should say the parent starts the conversation. `scheduled_time` stays in the schema for now.
- `docs/PILOT_RUNBOOK.md` should tell the adult child that their parent starts each conversation, and that the child may need to prompt them at first.
- Mark `docs/proposals/outbound-messaging-window.md` as decided: Option B, 2026-10-16.

## 10. Change C6: Bulbul v4 Flash behind a flag, and cached system speech

**v4 Flash**
- `TTS_MODEL` selects the model.
- v4-flash voices are per language and style (for example `ritu_hi_customer_utility`), and v3's short names are rejected there. Add `media/voices.py`, mapping language to voice for each of the 13 `SUPPORTED_LANGUAGES`. v3 keeps `ritu`.
- A language with no v4 voice in the map falls back to v3 for that call. The ledger records the model actually used.
- Check the request field name. The code sends `target_language_code`, but the current docs show `language_code`. Send whatever the docs say for each model, and cover both models in the adapter test.
- **Before flipping the flag:**
  - Run a blind A/B on 10 replies each in Hindi, Tamil and English.
  - Listeners: Krish plus two pilot parents.
  - Flip only if v4 Flash is at least as good.

**Cached system speech**
- Text with no name in it should be synthesised once, not on every use. That covers the fallback clips, `_ALREADY_TALKED_TEXT` and the consent prompts.
- Add `media/system_speech.py` with `get_system_speech(text, language) -> bytes`.
- It keeps the OGG in S3 at `system/speech/{model}/{voice}/{lang}/{sha256(text)[:12]}.ogg` and in an in-process dict, and synthesises only on a miss.
- `preload_fallback_audio` uses it, so a restart no longer re-synthesises 39 clips.
- These objects hold no user data and are outside deletion.

## 11. Change C7: shorten long pauses before STT

- Add `media/audio_split.compress_pauses(audio_bytes) -> (bytes, seconds_in, seconds_out)`. Call it in `sarvam_stt.transcribe` before `split_for_stt`.
- **What it does:** pauses longer than `STT_MAX_PAUSE_SECONDS` are shortened to 0.5 s, using ffmpeg `silenceremove` (`stop_periods=-1`, `stop_duration=<max>`, `stop_silence=0.5`).
- **Threshold:** tune `stop_threshold` on fixtures. Start at `-35dB`, which is quieter than the split's `-25dB`, so soft speech is never treated as silence.
- **Failure:** on any ffmpeg failure, pass the original through (the existing pattern).
- **Audio handling:** in memory only, as today. Nothing is stored; the privacy page promises that voice notes are discarded after transcription.
- **Ledger:** record `seconds_received` and `seconds_billed`.
- **Acceptance:**
  - On `tests/fixtures/sample.ogg` and 5 self-trial notes (local only, never committed), the transcript word count differs by ≤2%.
  - No word is lost at a cut. Check by diffing transcripts with and without compression.

## 12. Amendments to `ADMIN_METRICS.md`

Build that spec as written, with these changes:

- **§2 Out.** Delete the "Sarvam STT/TTS cost" exclusion. Change "No new tables, migrations" to say the metrics code adds none of its own and reads `api_usage` from this PR.
- **§4.** Delete the two `LLM_PRICE_*` settings.
- **§6.8 is replaced** by:

  ### 6.8 Cost (all vendors)

  Computed at read time from `api_usage` units multiplied by `core/pricing.py`. Price changes then need no backfill, and the page states which price table it used.

  | Key | Definition |
  |---|---|
  | `inr_total` | Sum over rows `{all_time, last_7d, last_30d}`, fixed hosting excluded |
  | `inr_per_conversation` | ÷ conversations (§5.1 predicate), same window |
  | `by_component` | `{claude_dialogue, claude_extraction, claude_entities, claude_other, sarvam_stt, sarvam_tts, twilio, meta}`, each `{inr, share}` |
  | `units` | Claude tokens by operation (including cache read and write), `stt_minutes_received`, `stt_minutes_billed`, `tts_characters`, `messages_in`, `messages_out` |
  | `dialogue_cache_hit_rate` | `cache_read / (cache_read + cache_creation + uncached input)` over dialogue rows, last 7 days. Target > 0.6. |
  | `dialogue_input_tokens_by_tenure` | Median dialogue input tokens per call, bucketed by session number (1–10, 11–30, 31–100, 100+). After C2 this should stay flat. |
  | `wasted_outbound` | Outbound rows with status `failed` or `undelivered`: `{count, inr}` by `error_code`, last 30 days |
  | `unpriced` | Any `(vendor, model)` with no price in `core/pricing.py`, with row counts. So a model switch can't silently read as ₹0. |
  | `prices_as_of`, `usd_inr_rate` | From `core/pricing.py` and settings |

- **§6.11 per-family table.** Add `cost_30d_inr` and `cost_per_conversation_30d_inr`.
- **§8 page.** The health strip adds "₹ per conversation (7d)" and "wasted outbound (7d)". The cost section shows `by_component` as a CSS-bar list, matching the rest of the page.
- **§9 tests.** The seeded world includes `api_usage` rows covering every vendor, cache hits, an undelivered 63016 send and one unpriced model. Assert exact INR to 2 decimals. The PII test also asserts that no `message_sid` appears in the HTML.
- **§12 Later.** Delete "Record audio seconds per turn". This PR does it.

### 12.1 `core/pricing.py`

Dated constants, each with its source URL in a comment, and `PRICES_AS_OF = "2026-10-16"`:

- **Anthropic, $ per million tokens:**

  | Model | Input | Output | 5m write | 1h write | Cache read |
  |---|---|---|---|---|---|
  | `claude-sonnet-4-6` | 3 | 15 | 3.75 | 6 | 0.30 |
  | `claude-sonnet-5-5` | 2 | 10 | 2.50 | 4 | 0.10 |
  | `claude-haiku-5-5` | 0.10 | 0.50 | 0.125 | 0.20 | 0.01 |

  Haiku 5.5 has a long-prompt tier above 100K tokens. It is unreachable here, but implement it anyway.
- **Sarvam:** STT ₹29.40 per hour; TTS v3 ₹30 and v4-flash ₹20 per 10K characters.
- **Twilio:** $0.005 per message, inbound or outbound.
- **Meta, India:** Utility ₹0.115, Marketing ₹0.8631, service replies ₹0.

`cost_inr(row) -> Decimal | None` returns None for unpriced models.

## 13. Migration

One Alembic revision, `down_revision = "c8f1a4d7b230"` (`add_parent_pronouns`, the current head). It does the following:
- Creates `api_usage` with its indexes.
- Adds `sessions.prior_context jsonb null`.
- Adds `user_profiles.last_inbound_at timestamptz null`, backfilled from sessions.

`downgrade` drops all three. Add `ApiUsage` to `api/routes/admin.py`'s delete list, to `evals/run_eval.py`'s per-user cleanup, and to `_TABLES_IN_DELETE_ORDER` in `tests/integration/conftest.py`.

## 14. Order of work inside the PR

Each commit is green on its own targeted tests:

1. Migration, `api_usage`, `core/usage.py`, `core/pricing.py`, adapter recording. No behaviour change.
2. Run the eval on the **old** config (`LLM_*=claude-sonnet-4-6`, cache off, freeze off) to get the first *measured* per-session cost. Commit the result as the baseline.
3. Admin metrics (§12).
4. C2.
5. The `llm.chat` refactor, then C1, C4 and C3. Run the eval and the consolidation eval. Commit the results.
6. C5.
7. C6 (flag off) and system speech.
8. C7.
9. The full targeted test list (§15), lint and type checks. Open the PR titled `Cut cost to serve and measure every paid call`.

## 15. Tests (targeted, per `.claude/rules/testing.md`)

| File | Covers |
|---|---|
| `tests/test_llm.py` (update) | Required `model`/`purpose`; `temperature` sent only on legacy models; `between_tools` thinking and `output_config` on Sonnet 5.5; effort on Haiku 5.5; text taken from text blocks when thinking blocks lead; empty text plus `max_tokens` handled; system sent as two blocks with `cache_control` when enabled, one string when disabled; usage fields carried through |
| `tests/test_usage.py` (new) | Scope propagation; `record` swallows DB errors; no text fields in `units`; status callback updates by SID |
| `tests/test_pricing.py` (new) | Each vendor's cost from known units; cache read and write priced separately; unpriced model gives None |
| `tests/test_system_prompt.py` (update) | Compact facts rendering; caps; session-100 prompt under 8,000 characters; `stable` + `per_turn` join equals the old string; render-coverage test still passes |
| `tests/integration/test_prior_context_freeze.py` (new) | Snapshot written on turn 1 and reused on turn 2; legacy null session filled |
| `tests/test_session_initiator.py` (update) | Window open sends OGG voice; window closed with template sends the template and makes no TTS call; no template sends nothing and starts no session; nudge skipped when closed |
| `tests/test_webhook.py` (update) | `last_inbound_at` set; `OPEN_SESSION` tap speaks the opener (active session and on-demand cases); inbound ledger row written once despite Twilio retries |
| `tests/test_sarvam_tts.py` (update) | Model and voice selection; v4 fallback to v3 for an unmapped language; request field names per model |
| `tests/test_system_speech.py` (new) | Cache hit skips synthesis; key includes model, voice and text hash |
| `tests/test_audio_split.py` (update) | `compress_pauses` shortens a synthetic 3 s gap to about 0.5 s; passes through on ffmpeg failure; short speech untouched |
| `tests/integration/test_deletion_e2e.py` (update) | `api_usage` rows removed on deletion |
| `tests/integration/test_admin_metrics.py` | Per `ADMIN_METRICS.md` §9, plus §12 above |

## 16. Expected result

Model estimates, to be replaced by step 2's measured baseline and a post-change measurement. They include about 30% more tokens on the 5.5 models and some thinking output on Haiku.

| | ₹ per session (session 30) | ₹ per family per year at 200 sessions |
|---|---|---|
| Today | 43.0 | 9,500 |
| This PR, `TTS_MODEL=bulbul:v3` | ~25.5 | ~5,100 |
| This PR, after the v4 Flash flip | ~23.4 | ~4,700 |
| Plus leaving Twilio (not in scope) | ~15.7 | ~3,100 |

The biggest line still left is Twilio, at ₹7.75 per session. The pricing-by-cadence question in the cost analysis is still open.

## 17. Acceptance criteria

- [ ] Every paid call in a self-trial session shows up in `api_usage` with the right vendor, operation and units: inbound, STT, dialogue, extraction, TTS, outbound, entity extraction and memory card
- [ ] Admin metrics live with §12. ₹ per conversation over 3 self-trial sessions is within ±20% of §16.
- [ ] Dialogue cache hit rate > 0.6 on those sessions
- [ ] Eval gate met (§3.2); results committed for the baseline and the new config
- [ ] Dialogue p95 latency within +20% of baseline
- [ ] The opener reaches a parent whose window is closed (template), and no TTS is billed for it
- [ ] Each of C1–C7 rolls back by env change alone (tested by flipping each in a local run)
- [ ] Deletion removes ledger rows; PII test passes
- [ ] Lint and type checks pass; PR titled per `.claude/rules/git.md`
