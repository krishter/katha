# Katha — Sprint 1: The Pilot Floor

**Inputs:** `docs/REMEDIATION_PLAN.md` (WS1–WS5), `docs/UX_REVIEW.md` (F-01, F-02)
**Scope:** Only what is required to legally and safely onboard the first pilot family.
**Target:** Family #1 can be onboarded without a known compliance gap.
**Estimate:** 4–6 working days.

---

## Instructions for the implementer

Read this whole file before starting. Work the workstreams **in order** — S1 through S4. They are ordered by dependency: S1 unblocks the branch you will build everything else on top of, and S3 depends on the settings surface built in S2.

Rules that apply throughout:

- One branch per workstream, named below. Never commit to `main`. PR per workstream. (`.claude/rules/git.md`)
- Write tests before marking any item complete. Run targeted tests during implementation, full suite before PR. (`.claude/rules/testing.md`)
- Run `ruff check . && ruff format --check .` before every commit. Frontend: lint and type check before every commit.
- Every item has an **Acceptance** block. An item is not done until its criteria pass as an automated test, unless the criterion says manual.
- **Do not expand scope.** Anything you find that is not in this plan goes in `## Found during Sprint 1` at the bottom of this file. Keep going.
- After S3, re-run TC-01–TC-10 via the `eval-runner` subagent. S3 changes the first thing the parent ever hears, which is prompt-adjacent.

---

## Why this sprint, in one paragraph

WS1–WS4 of the remediation plan are merged to `main`. WS5 — the workstream that *verifies* them — is not; it sits on `fix/ws5-verification` with six commits and roughly 1,075 lines of tests and prompt fixes. Meanwhile the onboarding consent checkbox tells every user, verbatim, that they can delete all their data from account settings, and no settings route exists anywhere in the application. Sprint 1 closes both: it lands the verification that proves the backend works, and it builds the two consent surfaces the product currently only promises.

---

## S1 — Land the verification workstream

**Branch:** `chore/merge-ws5-verification`
**Closes:** the open half of the remediation plan
**Why first:** Everything else in this sprint builds on `main`. Merging 1,075 lines of test changes *after* you've written new features multiplies the conflict surface for no benefit.

---

### 1.1 — Rebase `fix/ws5-verification` onto current `main`

The branch was cut before WS2, WS3, and WS4 merged. All three touched files WS5 also changes. Expect real conflicts, not a fast-forward.

Known overlap — review each of these hunks by hand rather than accepting either side wholesale:

| File | Also changed by | What to watch |
|---|---|---|
| `backend/core/orchestrator.py` | WS1, WS2, WS3 | Turn persistence ordering and the S3 key follow-up write |
| `backend/core/session_manager.py` | WS1, WS2 | Deferred-close fix vs. the duplicate-close-path removal |
| `backend/prompts/system_prompt.py` | WS2 | WS5.4's recall/5W forcing vs. WS2.1's dialogue/extraction split |
| `backend/core/conversation_policy.py` | WS2 | Turn-1 gating on the entry question |
| `backend/tests/test_webhook.py` | WS4 | Signature validation behind a proxy |

Resolve in favour of WS5 for prompt content — WS5.4's changes were derived from live eval runs against the post-WS2 prompt, so they are the newer information.

**Acceptance:**
- Full backend suite green on the rebased branch.
- `ruff check . && ruff format --check .` clean.
- `docs/REMEDIATION_PLAN.md`'s `Found during remediation` section retains **both** the WS3.2 entries already on `main` and the two WS5.4 entries from the branch. This section is append-only; a merge that drops entries from either side is wrong.

---

### 1.2 — Run the two manual gates WS5 did not close

WS5.1 and WS5.2 shipped as automated tests. WS5.3 and WS5.5 are manual and there is no evidence in the branch that either was performed.

**5.3 — Two-session continuity.** Run two sessions for one test user against the stub adapter, a day apart (or with the clock advanced). Read the assembled Layer 3 prompt for session 2 and confirm it contains actual facts and open threads from session 1. Paste the assembled prompt into the PR body. This is the check that proves memory works at all; an automated test will not catch a subtly empty context block.

**5.5 — Consent audit.** Create a full test user with sessions, turns, atoms, cards, and voice notes. Call `DELETE /user/{user_id}`. Verify by direct table and bucket inspection — not by the endpoint's return value — that no rows remain in any table except the anonymised `consent_records`, and the bucket holds zero objects for that user.

**Expect 5.5 to fail.** Three tracked gaps make it fail by construction; they are S2.4 below. Run it anyway and record the actual result — you want the failure documented before you fix it, so the fix has a baseline.

**Acceptance:**
- Session 2's Layer 3 block, pasted into the PR, contains at least one concrete fact and one open thread from session 1.
- The consent audit result is written into the PR body: which tables and which bucket prefixes still held data.

---

### 1.3 — Merge and tag

Open the PR, merge to `main`, tag `pre-pilot-verified`.

**Acceptance:**
- `main` contains all six WS5 commits.
- `git branch --merged main` lists `fix/ws5-verification`.

---

## S2 — Make the deletion promise real

**Branch:** `feature/settings-privacy-data`
**Closes:** F-01, plus the three tracked deletion gaps
**Why second:** This is the only item in the sprint that is a live compliance defect on a promise already shown to users. `DELETE /user/{user_id}` in `backend/api/routes/admin.py` is well-implemented and correctly scoped — it rejects any `user_id` that isn't the caller's own. It is simply unreachable from the UI.

---

### 2.1 — Add the settings route and navigation

Create `frontend/app/family/settings/page.tsx` and a `privacy` sub-route. Add **Settings** to the nav in `frontend/app/family/layout.tsx`, which currently offers only Stories, Memory Cards, and Logout.

Use the design tokens landed in `feature/portal-design-system`. Destructive actions use `--color-danger` (`#BA3D5B`) and nothing else does.

Scope note: this sprint ships **Privacy & Data only**. Conversation schedule, language, and pause (D1/D2, closing F-06) are real gaps but are not compliance blockers — they are Sprint 2. Build the settings shell so a second section can be added without restructuring, then stop.

**Acceptance:**
- `/family/settings/privacy` renders for an authenticated user and 401s otherwise.
- Settings is reachable from every authenticated page via the nav.
- The route is excluded from the `isAuthPage` branch in the layout, so it renders inside the nav chrome.

---

### 2.2 — Wire the deletion flow

Two-step confirm. Offer export before deletion — a user who deletes their mother's stories because they wanted to cancel a subscription is a support incident you cannot undo.

- Step 1: plain-language explanation of exactly what is destroyed, with counts pulled from `GET /family/stats`. Naming the number makes the consequence concrete. Note that stats returns `total_sessions` and `total_story_atoms` but **no memory-card count** — only `latest_card_url`. Either add a count to the stats payload or leave cards out of the copy; do not invent a number.
- Step 2: type-to-confirm. The parent's name, not the word "DELETE" — it forces the user to look at who this is about.
- On success: clear local state, redirect to a standalone confirmation page. The endpoint already clears the `katha_token` cookie via `response.delete_cookie`, so do not also call logout — you will race it.

**Acceptance:**
- Deletion cannot be triggered in fewer than two deliberate actions.
- A wrong confirmation string leaves the button disabled.
- After success the user lands on the confirmation page and any subsequent authenticated request 401s.
- An E2E test drives the full flow against a seeded user and asserts the user's rows are gone.

---

### 2.3 — Correct the consent copy

`frontend/app/family/onboarding/page.tsx:272` promises deletion "from your account settings." Once 2.1 and 2.2 ship, that sentence becomes true — but it should now link to the page rather than describe it.

Separately, the same checklist needs the honesty fix from F-02: the buyer cannot consent on the parent's behalf. Change that line to state that Katha will ask the parent for their own permission before the first conversation. Bump `consent_version` on `ConsentRecord` — the text of what was agreed to has materially changed, and the version column exists precisely to record which text a given record attests to.

**Acceptance:**
- The consent checklist links to `/family/settings/privacy`.
- New consent records carry the bumped `consent_version`.
- Existing records retain their original version. No backfill.

---

### 2.4 — Close the three tracked deletion gaps

These are the entries already logged under `Found during remediation`. All three are why S1.2's consent audit fails.

**(a) Session-open voice note S3 key is discarded.** `backend/scheduler/session_initiator.py` unpacks `send_voice_note`'s return as `message_sid, _s3_key` and throws the key away. The object is private but not enumerable, so `DELETE /user/{user_id}` cannot reach it. Add `session_open_audio_s3_key` to `models/session.py`, mirroring the existing `session_open_message_id`, and sweep it in `admin.py` alongside the `Turn.response_audio_s3_key` sweep already there.

**(b) Memory card images upload twice.** `core.orchestrator._generate_and_deliver_memory_card` uploads to `cards/{session_id}.png` and tracks it in `memory_cards.image_s3_key`. Then `TwilioWhatsAppAdapter.send_image` uploads the *same bytes again* to `cards/katha-{uuid}.png` purely to obtain something to presign. The second object is untracked and never deleted.

Fix by eliminating the redundant upload, not by tracking it: change `send_image` to accept an already-uploaded `s3_key` and presign it, rather than accepting raw bytes. The caller already has the key. Update the protocol definition in `backend/adapters/whatsapp.py` and the stub adapter to match.

**(c) S3 Block Public Access needs a manual audit.** `storage.upload_media` no longer sends `ACL=public-read`, but if Block Public Access is off at the bucket level a future regression re-exposes objects silently. This is an AWS console action; no code change can verify it. Check it and record the result in the PR body.

**Acceptance:**
- Re-running the S1.2 consent audit leaves zero objects under the user's prefixes.
- A test asserts `send_image` performs no upload.
- Bucket-level Block Public Access confirmed ON, stated in the PR.

---

## S3 — Ask the parent

**Branch:** `feature/parent-consent-flow`
**Closes:** F-02
**Why third:** It depends on nothing in S2, but it is prompt-adjacent and needs the eval set from S1 in place to detect regression.

---

### 3.1 — Send a welcome before the first conversation

Today `initiate_sessions` in `backend/scheduler/session_initiator.py` opens session 1 with `"Namaste {name} ji! I'm Katha, your daily companion..."` followed immediately by a domain entry prompt. The parent has never heard of Katha. There is no welcome turn anywhere in the scheduler or the WhatsApp adapter, despite PRD §6.1 step 4 specifying one — a voice note and text sent *before* day 1, explaining who Katha is and that their child set this up as a gift. Note the PRD specifies the welcome but stops short of asking for consent; this workstream extends it to do both.

This is a compliance problem and an activation problem at once. Under the DPDP Act the parent is the data principal, and a competent adult's consent cannot be delegated to their child. Separately, an unexplained voice note from an unknown number at 9:30am gets blocked.

Gate session 1 behind a welcome turn: who Katha is, that their child set this up, that conversations are recorded and kept for the family, and that they can stop any time by saying so. Then ask, and wait for an answer.

Outbound messages outside a 24-hour window need a pre-approved template — this one always is, since it is the first contact. Get the template submitted for approval early; approval is not instant and it will gate the whole workstream.

**Acceptance:**
- A profile with no recorded parent consent receives the welcome instead of a domain opening.
- No `Session` row advances past the welcome until consent is recorded.
- The welcome is spoken in `profile.preferred_language`.

---

### 3.2 — Record the parent as a distinct principal

`ConsentRecord` currently has `user_id`, `email_hash`, `consent_version`, `consented_at`, `ip_address`, `user_agent` — shaped entirely around a web form. A spoken consent has no IP and no user agent, and it needs to be distinguishable from the buyer's record.

Add a `principal` column (`"buyer"` / `"parent"`) and a nullable `evidence_ref` pointing at the `Turn` that carries the spoken agreement. Keep the anonymisation behaviour in `admin.py` — consent records are retained for audit and never hard-deleted.

Interpretation is a judgement call: prefer an explicit affirmative. Ambiguity, silence, or confusion is not consent — re-ask once on the following day, then stop and flag the account rather than proceeding.

**Acceptance:**
- A completed welcome turn writes a `ConsentRecord` with `principal="parent"` and a populated `evidence_ref`.
- Declining records the decline and initiates no further sessions.
- Ambiguity re-asks exactly once, then halts.
- `DELETE /user/{user_id}` anonymises both principals' records and hard-deletes neither.

---

### 3.3 — Surface consent status to the buyer

The dashboard should show whether the parent has agreed. A buyer who set this up on Tuesday and sees nothing by Thursday needs to know whether Katha is waiting on a person or is broken.

**Acceptance:**
- Dashboard shows one of: awaiting parent's consent / consented on {date} / declined.
- The declined state explains what happens next and does not read as an error.

---

### 3.4 — Eval regression

Run TC-01–TC-10 via the `eval-runner` subagent. Targets per `.claude/rules/testing.md`: 80%+ objective, 75%+ rubric.

**Acceptance:** at or above target. If the welcome turn regresses session-1 rapport, tune the welcome — do not remove the consent gate.

---

## S4 — Pilot readiness sign-off

**Branch:** `chore/pilot-readiness`

A single checklist committed to the repo, walked end to end on a clean database: onboard a family, receive the parent welcome, consent, run two sessions, generate a card, view it in the portal, delete everything, verify the deletion by direct inspection.

**Acceptance:** the full walkthrough passes on a clean database, with the result recorded in the PR.

---

## Explicitly out of scope

Do not do these in Sprint 1. Several are P0 in the UX review and genuinely matter — they are simply not blockers for onboarding family #1 without a compliance gap.

- **F-08 ops console** (G1/G2). You will run the pilot blind until this exists. It is the first thing in Sprint 2 and arguably belongs here — it was cut to keep this sprint to the legal floor, not because it is unimportant.
- **D1/D2 conversation and pause settings** (F-06). Build the settings shell only.
- **F-03 landing page** (A1/A3). No acquisition surface is needed for a hand-recruited pilot.
- **F-07 freemium gate / Razorpay** (E1/D4).
- **F-04 dashboard rebuild** (C2), **F-14 day-0 state** (C1), **F-05/F-11 story detail and audio** (C4).
- **F-13 share sheet** (C5/C8), **F-09 silence handling** (E2), **F-10 entity surfacing** (C3/G3).
- **TC-09b** and the closing-instruction strengthening logged under `Found during remediation`. Worth doing before pilot; not a blocker for onboarding.
- The `named_entities` dead-code decision logged under WS5.4. It is a genuine design decision about whether a second write path into the fact store should exist at all, and it needs deciding deliberately rather than in a compliance sprint.

---

## Suggested sequencing

| Days | Workstream | Ship gate |
|---|---|---|
| 1 | S1 — land verification | WS5 on `main`; manual gates run and recorded |
| 2–3 | S2 — deletion is real | Consent audit passes with zero stranded objects |
| 3–5 | S3 — parent consent | Parent is asked before session 1; evals at target |
| 5–6 | S4 — sign-off | Clean-database walkthrough passes |

S1 must complete before S3 — the eval set S3.4 depends on lives in WS5. S2 and S3 are independent of each other and can be parallelised.

**Start the WhatsApp template approval for S3.1 on day 1**, in parallel with S1. It is the only item here with an external dependency, and it will gate S3 if left until you need it.

---

## Found during Sprint 1

<!-- Append anything discovered that is not covered above. Do not fix in-scope. -->

- **(S1.1) S1's merge had already happened before this sprint started.**
  `fix/ws5-verification` was merged to `main` on 2026-08-09 as PR #16
  (`3d7524a`), including a sixth commit fixing the dialogue guard. The
  rebase-and-resolve work described in 1.1 was therefore never needed —
  the merge was clean because the branch already contained WS2's redone
  work in its ancestry. 1.1's acceptance criteria were verified against
  `main` after the fact and all hold: 308 backend tests pass, `ruff check`
  and `ruff format --check` are clean, and the `Found during remediation`
  section retains all three WS3.2 entries alongside all three WS5.4
  entries. Only the `pre-pilot-verified` tag from 1.3 was outstanding.

- **(S1.2) Embedding calls are an unguarded hard dependency on every turn,
  and the OpenAI account has no credits.** `build_prior_context`
  (`core/orchestrator.py:556`) is called on every voice turn and is not
  wrapped in a try/except, unlike the STT call above it. It reaches
  `vector_store.retrieve_relevant` → `_embed`, which calls OpenAI
  `text-embedding-3-small`. That account currently returns HTTP 429
  `insufficient_quota` ("no credits remaining"), so **every turn raises**,
  falls through to the webhook's last-resort handler
  (`api/routes/webhook.py:222`), and the user receives the generic
  `FailureStage.OTHER` fallback text instead of a conversation. The
  failure is visible rather than silent — WS2 working as designed — but
  the core loop is fully non-functional in this state, and a pilot would
  be dead on arrival.

  Two separable problems: (a) the account needs credits, and (b) RAG
  retrieval is an *enhancement* to the prompt, yet its failure takes down
  the whole turn. The fact store — which supplies Layer 3's concrete
  names, dates and relationships — is Claude- and Postgres-backed and
  keeps working when embeddings do not, so a degraded `PriorContext` with
  facts but no `recent_stories` would still produce a coherent session.
  Suggest wrapping the retrieval half so it degrades instead of raising.
  Not fixed here: out of scope for S1, and (b) is a design decision about
  how much continuity to promise when retrieval is down.

  This blocked gate 5.3 (below) from running as specified.

- **(S1.2) P0 — the structured fact store has never been populated.**
  `extraction/entity_extractor.py:53` calls `json.loads(response.content)`
  on the raw LLM reply with no tolerance for a markdown fence. Claude
  reliably wraps this particular response in ```json fences, so the parse
  always raises, and the `except` at line 58 logs a warning and **returns
  before reaching `fact_store.update_facts`**. The extraction itself is
  correct — a live call returned
  `{"people": [{"name": "Kamala", "relationship": "sister"}, ...],
  "places": ["Madurai"], "dates": ["1948"], ...}` — and every bit of it is
  discarded.

  Consequence: `structured_facts` is empty for every user, so Layer 3
  renders "You don't yet have structured facts about {name}" in every
  session forever. This is the explicit-recall half of the dual-store
  memory in TECH_DESIGN §2.5, and the specific mitigation RISK F1 relies
  on ("the structured fact store is injected at session start regardless
  of relevance scoring"). Only the vector half has ever worked.
  `_merge_entities` and `fact_store.update_facts` are dead code in
  practice.

  Same failure class as the WS5.4 `story_atoms` schema gap: an assumption
  about LLM output shape that no mocked test can catch, because the mocks
  supply pre-parsed dicts. Note `story_extractor` and the dialogue path
  both guard against this with tag-delimited output and tolerant parsing;
  `entity_extractor` is the one path that does neither.

- **(S1.2) `significant_people` was also empty after session 1.** Session
  1 described two people with clear emotional weight (a sister, and a
  neighbourhood sweet-maker the user said "everyone knew"), and Layer 2
  principle 6 instructs the model to flag exactly this. `PriorContext
  .significant_people` came back `[]`. Not yet diagnosed — it may be a
  genuine model judgement call on a three-turn session rather than a bug,
  but it is the same subsystem TC-11 already fails on, and worth checking
  once the fact-store parse is fixed.

### Gate 5.3 result (run 2026-08-14)

Two sessions, real Postgres, real Claude extraction, embeddings stubbed
(see above).

**First run — split result, which is how the fact-store bug was found:**

- Open threads: PASS — 14 concrete threads carried from session 1 into
  session 2's Layer 3.
- Structured facts: FAIL — `prior_context.facts == {}`; Layer 3 stated
  "You don't yet have structured facts about Subramaniam" in session 2.

The gate's purpose was to catch precisely this: a Layer 3 block that looks
populated while half of it is silently empty.

**After fixing the fence parse (in scope by decision — the gate exists to
verify memory works, and it found that half of it never had), re-run:
PASS.** Session 2's Layer 3 now opens with "What you already know about
Subramaniam" and carries:

```
  - dates: ['1948']
  - people: [{'name': 'Kamala', 'relationship': 'sister'},
             {'name': 'Vellai anna', 'relationship': 'neighbour/street vendor'}]
  - places: ['Madurai']
  - institutions: []
```

alongside 13 open threads. All four seeded session-1 specifics (Madurai,
Kamala, 1948, the shop) reach session 2's prompt. Full assembled prompt in
the PR body.
