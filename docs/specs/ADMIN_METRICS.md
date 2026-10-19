# Admin metrics endpoint — spec

**Status:** Ready to build · **Owner:** Krish · **Written:** 2026-10-16
**Branch:** built on `feature/cost-to-serve` as part of `docs/specs/COST_TO_SERVE.md`, which amends this spec in its §12 (never commit to `main`; open a PR per `.claude/rules/git.md`)

## 1. Why

During the pilot we need to answer, at any moment: how many families are onboarded, how
many conversations actually happened, how much story material came out of them, and
whether families are coming back. Every one of those facts is already a row in Postgres
on the Mumbai box. This endpoint computes them in place: no analytics vendor, no event
pipeline, no data leaving `ap-south-1`, and DPDP deletions are reflected automatically
because we read the live tables.

The success metrics this serves are PRD §11.1 (L1 engagement, L2 story quality, parts of
L3) and the pilot go/no-go in PRD §12.1 ("≥50% of pilot users complete 10+ sessions").

## 2. Scope

**In:**
- `GET /admin/metrics` → JSON
- `GET /admin/metrics/view` → one server-rendered HTML page of the same numbers
- Operator auth by a static token (there is no operator auth today; see §4)
- One shared definition of "a conversation", reused by `core/freemium.py`
- Integration tests against real Postgres with a seeded world and exact expected numbers

**Out (do not build):**
- Stories metrics. The consolidation pass is Phase A and writes nothing to the database
  (`consolidation/pipeline.py`, `scripts/consolidate_stories.py`). Return the placeholder
  in §6.7 and add real numbers when the stories table from
  `docs/proposals/first-person-stories.md` §8 lands.
- Charts, JavaScript, a Next.js page, PostHog or any third-party analytics.
- New tables, migrations, or indexes of its own. The metrics code reads `api_usage`, which
  `docs/specs/COST_TO_SERVE.md` adds. Plain aggregates are fast at pilot row counts.
- Query parameters. Windows are fixed (7 and 30 days).

## 3. Non-negotiables

1. **No personal data in any response.** Never select or return: `user_profiles.name`,
   `whatsapp_number`, `family_whatsapp_number`, `onboarding_context`, `pronouns`;
   `family_accounts.email`; any `transcript`, `response_text`, `narrative`, `title`,
   `what/why/who/when_approx/where_approx`, `verbatim_quote`, `open_threads`;
   `crisis_events.matched_pattern`; `consent_records.ip_address`, `user_agent`,
   `email_hash`. Families are identified only by `user_id` (`user_<hex>`). The point is
   that the page is safe to screenshot.
2. **Read-only.** Run the request's transaction as `SET TRANSACTION READ ONLY` before the
   first query, the same way `scripts/consolidate_stories.py` protects itself. If the
   statement fails, return 500. Do not run unprotected.
3. **Off by default.** If `ADMIN_API_TOKEN` is empty, both routes return **404**, as if
   they don't exist.
4. **Day boundaries are IST** (`Asia/Kolkata`). Everything else in the codebase stores UTC
   and stays UTC; convert only when bucketing by date and when displaying timestamps.

## 4. Auth

New settings in `config.py`:

| Setting | Default | Meaning |
|---|---|---|
| `ADMIN_API_TOKEN` | `""` | Operator token. Empty → routes 404. |
| `METRICS_EXCLUDED_USER_IDS` | `""` | Comma-separated `user_id`s left out of every metric (Krish's self-trial accounts, rehearsal users). |

Dependency `require_admin_token` (put it in `core/auth.py` next to the family auth):
- Accept either `Authorization: Bearer <token>` or `Authorization: Basic <b64(any-username:token)>`.
  Basic exists so the HTML page works in a browser with its native login prompt.
- Compare with `secrets.compare_digest`. Wrong or missing → 401. On the `/view` route,
  include `WWW-Authenticate: Basic realm="katha-admin"` so the browser prompts.
- **Never** accept the family `katha_token` cookie or a family JWT. A logged-in family
  must get 401.
- In production, refuse to start if `ADMIN_API_TOKEN` is set but shorter than 32
  characters. Add the check to `validate_production_config()` in `config.py`, next to
  the existing `JWT_SECRET` length check.
- Response headers on both routes: `Cache-Control: no-store`, `X-Robots-Tag: noindex`.

## 5. Code layout

- `core/metrics.py`: `async def compute_metrics(db, *, now: datetime, excluded_user_ids: frozenset[str]) -> dict`.
  One private function per section in §6. `now` is injected so tests are deterministic.
  No FastAPI imports in this module.
- `api/routes/admin_metrics.py`: the two routes and nothing else. Register in `main.py`
  next to the existing routers. (Don't add to `api/routes/admin.py`: despite the name,
  that file is the family's own DPDP deletion route and runs on family auth.)
- The HTML page: one f-string or Jinja template rendered from the same dict. Inline CSS,
  no external assets, no JS. Must be readable on a phone (Krish will check it from one).

### 5.1 One definition of "a conversation"

`core/freemium.get_session_count` already holds the right definition, with the reasoning
in its docstring: a session row counts as a conversation when
`status != CONSENT_SESSION_STATUS AND exchange_count > 0`. It is deliberately **not**
`status == 'completed'`, because most real conversations end by the 4-hour stale sweep
marking them `abandoned` (see `abandon_stale_sessions` in `core/session_manager.py`).

Move the predicate into `core/session_manager.py` as a reusable SQLAlchemy expression
(e.g. `is_conversation()`), use it in `freemium.get_session_count`, and use it everywhere
in `core/metrics.py`. The free-tier counter and the metrics page must never disagree.
Existing `tests/test_freemium.py` and `tests/integration/test_freemium_counting.py` must
pass unchanged.

## 6. Metric definitions

Vocabulary used below:
- **Storyteller**: a `user_profiles` row whose `user_id` is not excluded.
- **Family**: a `family_accounts` row whose `user_id` is not excluded.
- **Conversation**: §5.1.
- **Session started**: any `sessions` row with `status != 'consent'`.
- **Parent-consented**: storyteller with a `consent_records` row where `principal = 'parent'`.
- Windows: `all_time`, `last_7d`, `last_30d`, measured back from `now`, with day buckets in IST.

Counts in sections 6.2–6.8 come as `{all_time, last_7d, last_30d}` unless stated
otherwise. Rates are fractions 0–1 rounded to 3 decimals, and `null` when the
denominator is 0 (never divide by zero, never report 0% for "no data").

### 6.1 Onboarding funnel (cumulative, all time)

| Key | Definition |
|---|---|
| `family_accounts` | Families |
| `onboarding_complete` | Families with `onboarding_complete = true` |
| `buyer_consented` | Families whose `user_id` has a `consent_records` row with `principal = 'buyer'` |
| `storytellers` | Storytellers |
| `parent_consented` | Parent-consented storytellers |
| `parent_consent_stalled` | Storytellers with `parent_consent_asks >= 2` and no parent consent record (Katha stops asking after two unclear answers; see `core/parent_consent.py`) |
| `first_conversation` | Storytellers with ≥1 conversation |
| `reached_3_conversations` | ≥3 |
| `reached_10_conversations` | ≥10 (the PRD §12.1 go/no-go line) |
| `go_no_go_rate` | `reached_10_conversations / parent_consented` |

### 6.2 Conversations

| Key | Definition |
|---|---|
| `sessions_started` | Sessions started |
| `conversations` | Conversations |
| `empty_sessions` | Started, `exchange_count = 0`, `status != 'active'`. These are sessions where Katha delivered nothing (no reply, or every turn failed), so this is a **health signal**: show it prominently. |
| `active_now` | `status = 'active'` (point in time, not windowed) |
| `ended_completed` | Conversations with `status = 'completed'` |
| `ended_timeout` | Conversations with `status = 'abandoned'` |
| `ended_reason_breakdown` | Conversations grouped by `ended_reason` (all time) |
| `median_exchanges` | Median `exchange_count` over conversations |
| `goal_met_rate` | Share of conversations with `goal_met = true` |
| `median_length_minutes` | Median of `last_user_message_at − started_at` over conversations where `last_user_message_at` is not null. Do **not** use `ended_at`: for timed-out sessions it is the sweep time, hours later. |
| `share_10_min_plus` | Share of those with length ≥ 10 min (PRD §11.1 "session completion rate", target 70%) |
| `daily_last_30d` | 30 rows `{date_ist, conversations, storytellers}`, oldest first, zero-filled |

### 6.3 Engagement and retention

| Key | Definition |
|---|---|
| `active_storytellers_7d` | Storytellers with ≥1 conversation in last 7 days |
| `daily_active_rate_7d` | Distinct (storyteller, IST date) pairs with a conversation in the last 7 days ÷ eligible storyteller-days. Eligible days for a parent-consented storyteller are the days in the window on or after their parent-consent date. PRD target 60%. |
| `week2_retention` | Of storytellers whose first conversation was ≥14 days ago: share with a conversation in days 7–13 after it |
| `week4_retention` | Of storytellers whose first conversation was ≥28 days ago: share with a conversation in days 21–27 after it. The closest honest proxy for PRD "30-day retention"; label it as week 4 in the UI. |

Return the cohort sizes next to each retention rate (`{rate, cohort}`). With a pilot this
small, a rate without its denominator misleads.

### 6.4 Story atoms

| Key | Definition |
|---|---|
| `atoms` | `story_atoms` rows for storytellers |
| `atoms_per_conversation` | atoms ÷ conversations (same window) |
| `avg_completeness` | Mean `completeness_score` (0–5, the 5W score). PRD target 3.5. |
| `completeness_distribution` | All-time counts for scores 0,1,2,3,4,5 |
| `share_completeness_3_plus` | Share with score ≥ 3 |
| `median_atoms_per_storyteller_30d` | Over storytellers with ≥1 conversation in last 30 days. PRD target 20+/month. |
| `domain_coverage` | Per storyteller, distinct `story_atoms.domain` values out of the 8 in `prompts/domains.py` (count them from that module; don't hardcode 8). Report the histogram `{0..8: n}` and `storytellers_6_plus` (PRD target 50% by month 3). |

### 6.5 Memory cards

| Key | Definition |
|---|---|
| `generated` | `memory_cards` rows |
| `delivered` | `delivery_status` in `core.delivery.DELIVERED_STATUSES` (import it, don't copy it) |
| `read` | `delivery_status = 'read'`. Caveat in the UI: WhatsApp only reports `read` when the recipient has read receipts on, so this is a floor. |
| `failed` | `delivery_status` in (`failed`, `undelivered`) |
| `pending` | Anything else, including null |
| `failure_codes` | All-time failed cards grouped by `delivery_error_code` (63016 is the known one) |

### 6.5b Conversation cards

*(Added 2026-10-19, `docs/specs/CONVERSATION_CARDS.md` §13.)* Families with cards on get conversation cards instead of the per-session memory card, so both sections are shown while families of both kinds exist. `conversation_cards` in the JSON (`core/metrics.py`, `_conversation_cards`):

| Key | Definition |
|---|---|
| `pipeline.drafted` | `conversation_cards` rows, by `created_at` window |
| `pipeline.selection_calls`, `selection_none_rate` | `card_select` ledger rows; the share of those that chose no card (`1 − rows / calls`) |
| `pipeline.failed_fidelity`, `operator_approved`, `operator_edited`, `operator_rejected`, `waiting_for_review` | by status or `operator_action` |
| `pipeline.median_hours_in_review` | `operator_reviewed_at − created_at` |
| `parent.share_preference` | split of `user_profiles.share_preference` among families with cards on |
| `parent.readback_offers_per_card` | mean `readback_offers` over cards offered at least once |
| `parent.outcomes` | approved (approved, scheduled or sent), corrected, kept private, expired, withdrawn, waiting for her |
| `parent.median_days_draft_to_sent` | `scheduled_for − created_at` over sent cards |
| `delivery.whatsapp`, `whatsapp_by_country` | sent / delivered (`DELIVERED_STATUSES`) / rate, split by the child's country code (+91, +1, other), computed in SQL so no number is read |
| `delivery.whatsapp_skipped` | by `wa_skipped_reason` (`marketing_us`, `child_stopped`, `no_template`, `no_number`) |
| `delivery.email_accepted` | `email_status = accepted`: accepted by SES, not delivered |
| `delivery.email_turned_off`, `whatsapp_stopped` | families that turned a channel off |
| `child.*` | taps (`didnt_know`, `knew`), questions, questions per sent card, by screening label, asked to her |
| `calls_brought_up`, `calls_tagged` | hand-tagged on `/admin/cards/view`: she brought up a call about the card, unprompted |

The families table gains `cards_sent_30d`, `last_card_sent_at_ist` and `pending_card_status`. Card cost appears as `claude_cards` (selection, writing, fidelity, her answers, screening) and `ses` (card emails); `outbound_conversation_card` sends price as templates. As everywhere on this page, only status, time and count columns are read: no card text, question, or phone number.

### 6.6 Family dashboard

| Key | Definition |
|---|---|
| `sign_ins` | `magic_link_tokens` with `used = true` whose `email` belongs to a family, by `created_at`. Join on email inside the query and return only counts. |
| `families_signed_in_30d` | Distinct families with ≥1 used token in last 30 days, and the rate over families. PRD L3 target 40%. |

This is a proxy (sign-ins, not visits). Label it that way.

### 6.7 Stories (placeholder)

Return `{"available": false, "reason": "Stories are not persisted yet (consolidation Phase A)."}`
and show the same sentence on the page.

### 6.8 Cost (all vendors)

*Replaced by `docs/specs/COST_TO_SERVE.md` §12.*

Computed at read time from `api_usage` units multiplied by `core/pricing.py`. Price changes then need no backfill, and the page states which price table it used.

| Key | Definition |
|---|---|
| `inr_total` | Sum over rows `{all_time, last_7d, last_30d}`, fixed hosting excluded |
| `inr_per_conversation` | ÷ conversations (§5.1 predicate), same window |
| `by_component` | `{claude_dialogue, claude_extraction, claude_entities, claude_other, sarvam_stt, sarvam_tts, twilio, meta}`, each `{inr, share}` |
| `units` | Claude tokens by operation (including cache read and write), `stt_minutes_received`, `stt_minutes_billed`, `tts_characters`, `tts_gemini_clips`, `tts_gemini_audio_minutes`, `tts_vendor_fallbacks`, `tts_vendor_fallback_rate`, `messages_in`, `messages_out` |
| `dialogue_cache_hit_rate` | `cache_read / (cache_read + cache_creation + uncached input)` over dialogue rows, last 7 days. Target > 0.6. |
| `dialogue_input_tokens_by_tenure` | Median dialogue input tokens per call, bucketed by session number (1–10, 11–30, 31–100, 100+). After C2 this should stay flat. |
| `wasted_outbound` | Outbound rows with status `failed` or `undelivered`: `{count, inr}` by `error_code`, last 30 days |
| `unpriced` | Any `(vendor, model)` with no price in `core/pricing.py`, with row counts. So a model switch can't silently read as ₹0. |
| `prices_as_of`, `usd_inr_rate` | From `core/pricing.py` and settings |

### 6.9 Safety

`crisis_events` counts `{all_time, last_7d, last_30d}`, plus all-time counts grouped by
`source`, restricted to sessions of storytellers. Counts only, nothing else from the
row. The page shows it so a missed follow-up is visible. The follow-up itself stays in
the runbook.

### 6.10 Plans and deletions

| Key | Definition |
|---|---|
| `plans` | Families grouped by `plan` |
| `at_free_limit` | Free-plan storytellers with conversations ≥ `freemium.FREE_SESSION_LIMIT` (import it) |
| `families_deleted` | Distinct `email_hash` in `consent_records` where `user_id = 'DELETED'` (the DPDP deletion anonymises consent rows rather than deleting them; see `api/routes/admin.py`). Exclusions don't apply. Count only. |

### 6.11 Per-family table

One row per storyteller, sorted by last conversation (most recent first, never-talked last):

`user_id`, `onboarded_at_ist` (profile `created_at`), `parent_consented` (bool),
`parent_consent_asks`, `plan`, `conversations_total`, `conversations_7d`,
`last_conversation_at_ist`, `atoms_total`, `avg_completeness`, `domains_covered`
(`"5/8"`), `cards_delivered`, `cards_failed`, `cost_30d_inr`, `cost_per_conversation_30d_inr`.

## 7. Response shape

```json
{
  "generated_at": "2026-10-16T12:30:00Z",
  "timezone": "Asia/Kolkata",
  "excluded_user_count": 2,
  "funnel": { "family_accounts": 0, "...": 0 },
  "conversations": { "conversations": {"all_time": 0, "last_7d": 0, "last_30d": 0}, "...": {} },
  "engagement": { "week4_retention": {"rate": null, "cohort": 0}, "...": {} },
  "story_atoms": { "...": {} },
  "memory_cards": { "...": {} },
  "dashboard": { "...": {} },
  "stories": { "available": false, "reason": "..." },
  "cost": { "...": {} },
  "safety": { "...": {} },
  "plans": { "...": {} },
  "families": [ { "user_id": "user_…", "...": null } ]
}
```

Key names as in §6. Report `excluded_user_count`, never the excluded IDs themselves.

## 8. HTML page layout (`/admin/metrics/view`)

Top to bottom: generated-at time (IST) and excluded count → **health strip**
(empty sessions 7d, failed cards 7d, crisis events 7d, active now, ₹ per conversation 7d,
wasted outbound 7d) → funnel as a vertical
list with counts and step-to-step % → conversations (headline numbers + the 30-day daily
table, with a CSS width bar per row) → engagement and retention (rates **with** cohort
sizes) → atoms → cards → dashboard → stories placeholder → cost, with `by_component` as a CSS-bar list
→ per-family table (horizontally scrollable on mobile). Plain, legible, system font.

## 9. Tests

Follow `.claude/rules/testing.md`. Run targeted tests during the build.

**Unit (`tests/test_admin_metrics_auth.py`)**, using the `dependency_overrides` pattern in
`tests/test_family_api.py`:
- token unset → 404 on both routes
- missing or wrong token → 401; `/view` carries `WWW-Authenticate`
- correct Bearer → 200 JSON; correct Basic → 200 HTML
- valid family JWT cookie and no admin token → 401
- `Cache-Control: no-store` present
- production with a short token → `validate_production_config()` raises

**Integration (`tests/integration/test_admin_metrics.py`)**, on real Postgres:
- Seed a small fixed world with hand-computed expected values, and assert **every** key
  in §6. At minimum: 3 storytellers + 1 excluded one (whose rows must change nothing); a
  consent session; an empty session; an abandoned session with exchanges (counts as a
  conversation); a completed one; one with null `last_user_message_at`; atoms across
  several domains and scores; cards in delivered, read, failed (63016) and null states;
  buyer and parent consent records; one `DELETED` consent record; one crisis event; used
  and unused magic links; token counts on turns. Freeze `now`. Put at least one
  conversation just either side of an IST midnight to test the day bucketing.
- **PII test:** seed a profile named `"Kamala Zz-Marker"` with phone `+910000000001`, a
  family email `marker@example.invalid`, an atom narrative and verbatim quote containing
  `"NARRATIVE-MARKER"`, and a crisis `matched_pattern` of `"PATTERN-MARKER"`. Assert none of
  those strings appear anywhere in the JSON body or the HTML body.
- **Read-only test:** call `compute_metrics` inside the read-only transaction and assert a
  stray write in the same transaction raises.
- Add `ConsentRecord` to `_TABLES_IN_DELETE_ORDER` in `tests/integration/conftest.py`. It
  is missing today, and these tests create consent rows.

## 10. Config and deploy

- Add both new settings to `deploy.env.example` with comments.
- `docs/DEPLOYMENT.md`: a short section on generating the token
  (`python -c "import secrets; print(secrets.token_urlsafe(32))"`), setting it on the
  Lightsail box, setting `METRICS_EXCLUDED_USER_IDS` to the self-trial user_ids, and
  opening `https://api.katha.life/admin/metrics/view`.
- No Caddy change: `api.katha.life` already proxies to FastAPI, and the token is the gate.

## 11. Acceptance criteria

- [ ] Both routes 404 with no token, 401 with a wrong one, work with the right one
- [ ] Every §6 key present, matching the seeded integration world exactly
- [ ] PII test and read-only test pass
- [ ] Freemium uses the shared conversation predicate; its existing tests pass unchanged
- [ ] Page renders legibly at 375 px width with no horizontal page scroll (tables may scroll inside their container)
- [ ] Lint and type checks pass; PR titled per `.claude/rules/git.md`

## 12. Later (not this PR)

- Stories section once the §8 stories table exists (promoted, pending approval, approved, fidelity failed)
- Optional: storyteller first names in the per-family table if the pseudonymous IDs prove impractical (deliberately omitted for now)
