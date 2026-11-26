# Metrics page layout and family-number tracking — spec

**Status:** Ready to build · **Owner:** Krish · **Written:** 2026-11-26
**Branch:** `feature/metrics-page-layout`, cut from `feature/semantic-retrieval` (the page's embedding lines live there and it is not on `main` yet), or from `main` once that branch is merged. Never commit to `main`; see `.claude/rules/git.md`.
**Amends:** `docs/specs/ADMIN_METRICS.md` §6.1, §6.11 and §8. Where the two disagree, this spec wins.
**Design:** the "Katha metrics page redesign" canvas (desktop, phone, and a today-vs-proposed map). Its numbers are sample data. Where the canvas and this spec differ, this spec wins.

---

## 1. Why

`/admin/metrics/view` (`backend/core/metrics_page.py`) shows 13 sections one after another at the same weight. The pilot go/no-go number (PRD §12.1, ≥50% of parents at 10+ conversations) is a sentence under the funnel. Problems that need action today, like a crisis event or an empty session, sit in a tile strip that looks the same whether everything is fine or not.

The family's side of onboarding is also missing. Before any card goes to the child's WhatsApp, the child has to send `CONFIRM <code>` from that phone (`core/family_number.py`). The code lasts 7 days. Until the child confirms, cards go by email only (`cards/deliver.py`, `core/orchestrator.py`). The metrics never read `family_number_confirmed_at`, so a child who never confirms is invisible.

This change reorders the page by urgency, without removing anything, and adds the family-number lifecycle.

## 2. Scope

**In:**
- New family-number keys in `compute_metrics` (§4)
- A rewrite of `render()` in `core/metrics_page.py` into four tiers (§5)
- Tests (§7) and the `ADMIN_METRICS.md` amendments (§8)

**Out (do not build):**
- JavaScript of any kind, a charts library, or external assets. Collapsing uses native `<details>`; navigation uses `#anchor` links. ADMIN_METRICS.md §2 still holds.
- New tables, migrations or indexes. Every new number reads existing columns.
- Changes to `GET /admin/metrics` other than the added keys. Nothing is renamed or removed.
- A per-family "opened a memory card" metric (PRD go/no-go #2). See §9.
- Median time to confirm. The canvas shows it, but it is deferred (§9).

## 3. Non-negotiables (unchanged from ADMIN_METRICS.md §3, restated because new columns are read)

1. **No personal data.** Never select `family_whatsapp_number` itself. Derive "a number was given" in SQL as `family_whatsapp_number IS NOT NULL`, the same way the country split does today (`core/metrics.py`, the `like("+91%")` block). Never select `family_confirm_code_hash`. Only `family_confirm_code_expires_at` is needed (`confirm()` clears both together on success).
2. Read-only transaction, 404 when the token is unset, IST day boundaries: all unchanged.
3. The page still holds only ids and numbers. It must stay safe to screenshot.

## 4. New metrics

### 4.1 Funnel: `funnel.family_number` (all time, current state)

Counted over families (`family_accounts`, excluded users removed) joined to their profile.

| Key | Definition |
|---|---|
| `given` | Profile has `family_whatsapp_number IS NOT NULL` |
| `confirmed` | `given` and `family_number_confirmed_at IS NOT NULL` |
| `waiting_code_live` | `given`, not confirmed, and `family_confirm_code_expires_at > now` |
| `waiting_no_live_code` | `given`, not confirmed, and `family_confirm_code_expires_at` is null or `<= now` (expired, or a code was never issued) |
| `waiting_over_48h` | `given`, not confirmed, and `family_accounts.created_at` more than 48 hours before `now` |
| `confirmed_rate` | `confirmed / given` (null when `given` is 0) |

`waiting_code_live + waiting_no_live_code = given − confirmed`; assert this in the test.

**Known distortion:** migration `b8e3f1c5d7a2` set `family_number_confirmed_at = now()` on every number that existed when it ran. Those families count as confirmed without having confirmed. Leave the count as it is, and put a note on the page: "Numbers that existed before confirmation shipped count as confirmed."

### 4.2 Per-family rows: `families[]`

Add to each row (ADMIN_METRICS.md §6.11):

| Key | Type | Definition |
|---|---|---|
| `family_number_given` | bool | as `given` above |
| `family_number_confirmed` | bool | as `confirmed` above |
| `confirm_code_live` | bool | `family_confirm_code_expires_at > now` |
| `days_since_last_conversation` | int or null | whole days from the last conversation to `now`; null if none |

Change the row order: sort by status severity (§5.4), then by `days_since_last_conversation` descending (null first). The JSON uses the same order.

## 5. Page layout

Top to bottom. Every section has an `id` so the top nav can link to it.

### 5.0 Header

Title; the generated-at time (IST); the excluded count; then a nav row of anchor links: Needs attention · Pilot scorecard · Growth · Engagement · Story quality · Family value · Cost · Safety & ops · Families. At phone width the nav scrolls sideways inside its own box, never the page.

### 5.1 Tier 1 — Needs attention (`#attention`)

A tile appears only when its line is crossed. Each tile is a link to its detail group (§5.3). Tiles that don't appear are listed in one line under the band: "All clear: …" (always include "active now N"). When nothing is crossed, the band is just that line.

| Tile | Shows when | Level | Links to |
|---|---|---|---|
| Crisis events | `safety.crisis_events.last_7d > 0` | red | `#ops` |
| Empty sessions | `conversations.empty_sessions.last_7d > 0` | orange | `#engagement` |
| Failed memory cards | `memory_cards.failed.last_7d > 0` | orange | `#family` |
| Wasted outbound | `cost.wasted_outbound.last_7d.count > 0` | orange | `#cost` |
| Voice fallback | `cost.units.last_30d.tts_vendor_fallback_rate > 0.01` | orange | `#ops` |
| Retrieval fallback | `cost.units.last_30d.embedding_query_fallback_rate > 0.01` | orange | `#ops` |
| Unreviewed languages | any `languages[]` row with `source == "machine"` and `families > 0` | orange | `#ops` |
| Parent consent stalled | `funnel.parent_consent_stalled > 0` | orange | `#growth` |
| Family number unconfirmed | `funnel.family_number.waiting_over_48h > 0` | red if any of those have no live code, else orange | `#growth` |

A null rate counts as clear (for example, retrieval while `EMBEDDINGS_ENABLED` is off). Tile text is a big count plus one line of context, e.g. "5 unconfirmed" / "2 with no live code · cards to them go by email only". Thresholds go in module constants at the top of `metrics_page.py` so they are easy to change.

### 5.2 Tier 2 — Pilot scorecard (`#scorecard`)

Eight tiles in a responsive grid (`repeat(auto-fit, minmax(240px, 1fr))`). Each tile has a label, a status pill, the big value, the group size, a progress bar with a black tick at the target, and a footnote.

| Tile | Value | Group size shown | Target | Footnote |
|---|---|---|---|---|
| Parents at 10+ conversations | `funnel.go_no_go_rate` | `reached_10_conversations` of `parent_consented` | 0.50 | "go/no-go" |
| Families signed in, 30 days | `dashboard.families_signed_in_30d.rate` | its `count` of families | 0.40 | "proxy for opened a card" |
| Daily active rate, 7 days | `engagement.daily_active_rate_7d` | `active_storytellers_7d` | 0.60 | |
| Conversations 10 min+ | `conversations.share_10_min_plus.last_7d` | `conversations.conversations.last_7d` | 0.70 | |
| Story completeness (5W) | `story_atoms.avg_completeness.last_7d` | `story_atoms.atoms.last_7d` atoms | 3.5 (bar scale 0–5) | |
| Week 4 retention | `engagement.week4_retention.rate` | its `cohort` | none | "read with cohort" |
| Cost per conversation | `cost.inr_per_conversation.last_7d` | 30-day value as the subline | none, no bar | link to `#cost` |
| Story extraction accuracy | always "—" | — | 0.75 | "manual review, not on this page"; dashed border |

Status pill: **On track** (blue) when value ≥ target; **Close** (orange) when value ≥ 90% of target; **Behind** (orange) otherwise; **No target** (grey) when there is none; **No data** (grey) when the value is null. When the group size is under 10, add "small group" to the footnote.

### 5.3 Tier 3 — Detail, by question

Six `<details>` groups, each a white card. The `<summary>` shows the group name, its question, and a headline line that can be read without opening the group. A group starts `open` when any tier-1 tile links to it, and closed otherwise.

| `id` | Name · question | Contents (existing helpers) | Summary line |
|---|---|---|---|
| `growth` | Growth · Are families getting through onboarding? | New two-track funnel (below) | `family_accounts` → `first_conversation` → **`reached_10`** at 10+ · `confirmed` of `given` numbers confirmed |
| `engagement` | Engagement · Are they talking, and coming back? | `_conversations`, `_engagement` | conversations 7d · median length · active storytellers 7d |
| `quality` | Story quality · Is the material good enough to become stories? | `_atoms` (with `_embeddings`) | atoms 7d · atoms per conversation · storytellers at 6+ chapters |
| `family` | Family value · Is anything reaching the children? | `_cards`, `_conversation_cards`, `_dashboard`, stories placeholder | memory cards delivered 7d · conversation cards sent · families email-only (`given − confirmed`) |
| `cost` | Cost to serve · What does a conversation cost, and where does it go? | `_cost` without the two vendor-health paragraphs | total 30d · top component and its share · cache hit rate |
| `ops` | Safety & operations · Crisis events, vendor health, plans, languages | `_safety_and_plans`, `_gemini_tts`, `_embedding_queries`, `_languages` | crisis events 7d · voice fallback rate · families deleted |

**Two-track funnel (Growth).** Two columns side by side that stack below 700px. Each step is a row: label, a CSS bar (width = value ÷ `family_accounts`), the count, and % of the step above.
- *Parent track:* family accounts → onboarding complete → buyer consented → parent consented → first conversation → 3 conversations → 10 conversations. Under it: "Parent consent stalled after two unclear answers: N."
- *Child track (runs alongside):* number given → **number confirmed** → indented: waiting, code live / waiting, no live code (red bar) → signed in, 30 days. Under it: "Until the child sends CONFIRM from that phone, nothing of hers goes to that WhatsApp number; cards go by email only. Codes last 7 days." Then the grandfathering note from §4.1.

**Window tables.** Change `_windows_table` / `_windows_row` to the column order **7 days | 30 days | All time**, with the 7-day value bold. This affects every section that uses them.

### 5.4 Tier 4 — Families (`#families`)

Heading note: "Quietest first, so the family who needs a nudge is at the top."

Status pill per row: the first rule that matches wins. The order is the sort severity.

| # | Rule | Pill | Level |
|---|---|---|---|
| 1 | `parent_consent_asks >= 2` and no parent consent | Consent stalled | red |
| 2 | parent consented and `days_since_last_conversation >= 7` | Quiet N days | red |
| 3 | `family_number_given`, not confirmed, not `confirm_code_live` | Number unconfirmed · no live code | red |
| 4 | no parent consent | No parent consent | orange |
| 5 | parent consented, never talked | No conversation yet | orange |
| 6 | `days_since_last_conversation` 3–6 | Quiet N days | orange |
| 7 | `family_number_given`, not confirmed, code live | Number unconfirmed | orange |
| 8 | otherwise | Active | blue |

The 3- and 7-day lines are module constants.

**Default table (8 columns):** Storyteller · Status · Last conversation · Convs 7d · Toward 10 (`"6 / 10"`, or `"10+ ✓"`) · Atoms · Chapters · ₹/conv 30d.
**Below it:** `<details>` "Show all columns" holding today's full table, plus a new "Family number" column (confirmed / waiting / no live code / none).
**Below 600px:** render the families as a stacked list instead (id, status pill, "Toward 10 · last date"). Emit both the table and the list in the HTML, and switch between them with a media query. This is CSS only.

### 5.5 Look

Keep the current palette and system font. Add: red `#9c1f18` on `#fbeae8`, orange `#8f4a06` on `#fdf0e0`, blue `#1f4f99` on `#e6eefb`, grey `#5b5872` on `#efedf3`. Each text/background pair must reach at least 4.5:1 contrast. Status words always carry the meaning, so colour is never the only signal. Touch targets for the nav, summaries and tiles are at least 44px tall.

## 6. Code layout

- `core/metrics.py`: load `family_accounts.created_at` and `family_confirm_code_expires_at`, plus `user_profiles.family_number_confirmed_at` and the derived boolean `family_whatsapp_number IS NOT NULL`, into `_World`. Add `_family_number(world, w)` and nest its result under `funnel`. Add the §4.2 keys in `_families` and change the sort.
- `core/metrics_page.py`: new `_attention(m)`, `_scorecard(m)`, `_group(id, name, question, summary, body, open)`, `_funnel_tracks(f)`, `_family_status(row)`, `_families_compact(rows)`; `render()` puts them together. **Keep the names and signatures of `_gemini_tts` and `_languages`**: `tests/test_metrics_tts.py` and `tests/test_speech_catalogue.py` import them.

## 7. Tests

Follow `.claude/rules/testing.md`; run targeted tests while building.

**Integration (`tests/integration/test_admin_metrics.py`).** In the existing seeded world, set the new columns on existing profiles and families. Add no rows, so every existing expected value stays the same. Cover:
- one confirmed number
- one given number, unconfirmed, with a live code
- one given number, unconfirmed, with an expired code, created more than 48h before the frozen `now`
- one profile with no family number

Assert every `funnel.family_number` key exactly, the given − confirmed identity, the four new per-family keys, and the new row order. Extend the PII test: seed a family number `+910000000099` and assert it appears in neither the JSON nor the HTML.

**Unit (new `tests/test_metrics_page.py`).** Feed `render()` hand-built metrics dicts. Build one small helper that returns a complete all-clear dict and lets each test change one field.
- All clear: there are no attention tiles and the "All clear" line is present.
- Crisis 7d = 1: the red tile is present, and `<details id="ops"` has `open`.
- Scorecard status at the edges: exactly at target → On track; 0.9 × target → Close; below that → Behind; null → No data.
- Window tables: the "7 days" header comes before "30 days", which comes before "All time".
- Family status: one row per rule in §5.4 gets the right pill, and rows come out in severity order.
- The output contains no `<script`, and no `http` except inside text.

**Existing:** `tests/integration/test_card_metrics.py` calls `render(m)`, so it must still pass. Then open the page at 375px and 1280px with Playwright, take a screenshot, and check there is no horizontal page scroll (ADMIN_METRICS.md §11).

## 8. Doc changes in the same PR

- `ADMIN_METRICS.md` §6.1: add `family_number` with a pointer to §4.1 here.
- `ADMIN_METRICS.md` §6.11: add the §4.2 keys and the new sort.
- `ADMIN_METRICS.md` §8: replace its layout paragraph with "See `docs/specs/METRICS_PAGE_LAYOUT.md`."

## 9. Later (not this PR)

- **Per-family "opened a memory card"** for PRD go/no-go #2 (≥70% of adult children). `memory_cards.read` is per card and only a floor, because of read receipts. Once a per-family count exists, it replaces the sign-in proxy tile.
- **Median time to confirm.** This would be `family_number_confirmed_at − family_accounts.created_at`, excluding grandfathered rows. That needs a way to tell those rows apart.
- Trend arrows on scorecard tiles (7d vs the previous 7d).

## 10. Acceptance criteria

- [ ] The JSON has the new keys; every key in ADMIN_METRICS.md §6 is unchanged
- [ ] The integration world matches exactly; the PII and read-only tests pass
- [ ] All unit tests in §7 pass; `test_metrics_tts`, `test_speech_catalogue` and `test_card_metrics` pass unchanged
- [ ] The page has no JavaScript; it reads at 375px with no horizontal page scroll
- [ ] The attention band is empty on an all-clear world and shows each tile at its line
- [ ] Lint and type checks pass; the PR title is "Reorder metrics page by urgency and add family-number tracking"

## 11. As built (2026-11-26)

Three small additions beyond the text above:

- **`funnel.family_number.waiting_over_48h_no_live_code`.** The §5.1 tile turns red when any family waiting over 48 hours has no live code, and none of the §4.1 keys gives that count, so it is its own key.
- **API keys stay on the page.** The old health strip's "API keys to look at" is an attention tile (red when a key is `bad`, orange when `warn`), and the API keys table (ADMIN_METRICS.md §6.12) sits in Safety & operations. Nothing is removed (§1).
- **The no-number case is a unit test.** The integration world has three storytellers, and §7 says add no rows, so "a profile with no family number" is tested on `_family_number` in `tests/test_metrics_page.py`. The integration world covers confirmed, live code and lapsed code.

The families table's status rules and lines (`family_status`, `QUIET_ORANGE_DAYS`, `QUIET_RED_DAYS`) live in `core/metrics.py`, because the JSON's row order depends on them; the page imports them.
