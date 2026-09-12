# Katha — Sprint 2: Make the running pilot observable

**Inputs:** `docs/UX_REVIEW.md` (F-06 to F-09), `docs/SPRINT_1_PLAN.md` §"Knowingly shipping unfixed"
**Scope:** What a pilot that is already running needs in order to be watched, and to survive ordinary life.
**Target:** Family #2 can be onboarded without running blind, and family #1 does not churn on something we could have seen.
**Estimate:** 6–9 working days.

---

## Instructions for the implementer

Read this whole file before starting. Work the workstreams **in order** — T1 through T6. They are ordered by how soon the thing they fix will actually happen to family #1.

Rules that apply throughout:

- One branch per workstream, named below. Never commit to `main`. PR per workstream. (`.claude/rules/git.md`)
- Write tests before marking any item complete. (`.claude/rules/testing.md`)
- `ruff check . && ruff format --check .` before every commit. Frontend: lint and type check.
- Every item has an **Acceptance** block. Not done until its criteria pass as an automated test, unless the criterion says manual.
- **Do not expand scope.** Anything found that is not here goes in `## Found during Sprint 2` at the bottom. Keep going.
- Re-run TC-01–TC-10 via `eval-runner` after T3 and T5 — both change prompt-adjacent behaviour.

---

## Why this sprint

Sprint 1 closed the compliance floor: the parent is asked before she is recorded, deletion actually deletes, and a rehearsal script proves both. Family #1 is onboarding on that basis.

What Sprint 1 deliberately did not build is any way to *see* what happens next. There is no surface reporting whether a family's sessions are working, no reaction to a parent going quiet, no way to change a schedule, and no message to the parent when the free trial stops Katha calling. Each of those is an ordinary event in the first month of a pilot, and each currently ends in either an email to the operator or silence.

The sharpest framing is this: **every P0 defect found in Sprint 1 was found by running something.** The fact store that had never populated, the unguarded embedding call, the presigned-URL region, the webhook import that broke every turn. In the pilot, nobody is running anything — families are just living with it. Without an operator surface, a family whose turns are all failing looks exactly like a family who is quiet.

---

## T1 — Ops console: cohort health and family detail

**Branch:** `feature/ops-console`
**Closes:** F-08 (G1, G2)
**Why first:** It is the only item here that makes the others measurable, and the UX review calls it the highest leverage-per-hour item on its list. Everything below is easier to verify once it exists.

---

### 1.1 — Cohort health

One table, one row per family, answering the questions `docs/PLAN.md` §"MVP Go/No-Go Criteria" actually asks: sessions completed, last session date, story atoms captured, memory cards delivered, whether the adult child has ever opened one.

The go/no-go gates are ≥50% of families completing 10+ sessions, ≥70% of adult-child accounts opening at least one memory card, and ≥75% extraction accuracy. Two of the three are answerable from this table. Build it so the answer is a glance, not a query.

Sort by *most concerning first* — the family that has stalled should be at the top without anyone applying a filter.

**Acceptance:**
- Every family appears with session count, last-session date, atom count, card count.
- Families with no session in 48h sort to the top.
- The page loads for an authenticated operator and 401s otherwise.

---

### 1.2 — Family detail

Drill into one family: session-by-session history, turn counts, failure stages hit, the assembled Layer 3 block for the most recent session, and the extraction output per session.

The Layer 3 view is the one that matters. Gate 5.3 in Sprint 1 found the empty fact store precisely by reading an assembled prompt and noticing half of it was missing. That inspection should not require a Python session.

**Acceptance:**
- One family's full session history is visible, newest first.
- The assembled Layer 3 block for the latest session is readable in the UI.
- Turn-level failures show which `FailureStage` was hit.

---

### 1.3 — Surface crisis events

`crisis_events` rows are written by `orchestrator._log_crisis_event` and **read by nothing**. `grep` finds `CrisisEvent` imported in exactly one file, where it is only ever inserted.

The table was created so a human could review detections during the pilot — the review never got a surface. For a product serving isolated elderly people, with a crisis protocol that references iCall India, an unread crisis table is the most serious blind spot in this document.

Show crisis events prominently on both the cohort view and the family detail, newest first, unacknowledged ones flagged. Add an acknowledged flag so an operator can mark one reviewed.

**Acceptance:**
- Crisis events appear on the cohort page without needing a filter.
- An operator can mark one acknowledged; the state persists.
- A test asserts a logged crisis event is retrievable through the ops API.

---

### 1.4 — Per-family kill switch

A control that stops Katha initiating sessions for one family, immediately, without a database edit.

Needed for the obvious reason — something goes wrong for one family and you need it stopped now — and because F-02's consent flow can record a decline, which must have a corresponding operator state.

**Acceptance:**
- Toggling the switch stops the scheduler initiating for that family.
- The state is visible in the cohort table.
- Toggling back resumes cleanly.

---

## T2 — Handle silence

**Branch:** `feature/silence-handling`
**Closes:** F-09 (E2)
**Why second:** It is the highest-signal event the product can observe, and it will happen to family #1 within the first fortnight — illness, travel, a dead phone, or simple irritation.

The existing 30-minute in-session nudge (`_send_followups`) operates within a session. Nothing operates at the scale of days.

---

### 2.1 — Escalation ladder

- **Day 2 of no reply:** a gentle re-ask to the parent. Inside the window if one is open; the approved `TWILIO_TEMPLATE_PARENT_WELCOME_FOLLOWUP` template if not — this is exactly the re-engagement case those templates were kept for.
- **Day 3:** the family dashboard changes state. Not an error — a plain statement that Katha has not heard from her since {date}.
- **Day 4:** notify the adult child directly.

Escalation stops on any inbound message.

Tone matters more than mechanism here. Handled well this is the moment the buyer tells other people about Katha — *"they noticed before I did."* Handled as an alert about a lapsed subscriber, it is the moment she cancels. It is also, for an elderly person living alone, a possible welfare signal; the message to the child should read as concern, not as churn prevention.

**Acceptance:**
- No inbound for 2 days triggers the parent re-ask exactly once.
- Day 3 changes dashboard state; day 4 notifies the child.
- Any inbound message resets the ladder.
- A test drives the full ladder with a frozen clock.

---

### 2.2 — Silence is visible to the operator

The cohort table from 1.1 already sorts stalled families first. Make the silence state explicit rather than inferred from a date.

**Acceptance:** cohort view shows which rung of the ladder each silent family is on.

---

## T3 — Tell the parent when the trial pauses

**Branch:** `feature/trial-pause-message`
**Closes:** the harmful half of F-07 (F6)
**Why third — and why it cannot wait:** family #1 hits `FREE_SESSION_LIMIT = 10` about ten days after their first session. Today, at session 11, `is_session_allowed` returns false and the scheduler skips them. Katha simply stops calling. From the parent's side, the friend who called every morning for ten days has silently disappeared, with no explanation.

For a product whose stated purpose is reducing loneliness among people who have experienced abandonment, that is the most harmful interaction in the system. The UX review says it would ship this even if payment is not ready. So does this plan: **T3 is the parent-facing message only.** Razorpay, the upgrade page, and the runway indicator are out of scope.

`freemium.send_upgrade_prompt` exists and currently prompts the *family*. The parent gets nothing.

**Acceptance:**
- Reaching the session limit sends the parent a warm message explaining that conversations are pausing and that it is not something she did.
- The message is in `profile.preferred_language`.
- It is sent exactly once per user, not on every skipped scheduler tick.
- A test asserts the parent is messaged when `is_session_allowed` first returns false.

---

## T4 — Settings: conversation and pause

**Branch:** `feature/settings-conversation`
**Closes:** F-06 (D1, D2)
**Why fourth:** S2 of Sprint 1 built the settings shell and the deletion control deliberately, leaving room for this.

Everything onboarding collects is currently write-once. The things that will actually be asked in the first month: move the session time, correct the language, pause during a hospital stay, fix a wrong number.

Pause deserves emphasis over the rest. A family that cannot pause during a hospital stay cancels instead of pausing, and that is an avoidable churn event with a distressing cause.

**Acceptance:**
- Schedule time, language, parent number and family number are all editable.
- A prominent pause control with a resume date, or indefinite.
- Paused families are skipped by the scheduler and shown as paused in the ops console.
- Changing the schedule takes effect on the next scheduler tick without a restart.

---

## T5 — Raise the retrieval window before it thins

**Branch:** `fix/layer3-retrieval-window`
**Closes:** the Layer 3 trigger recorded in Sprint 1's "knowingly shipping unfixed"

`retrieve_relevant` returns `top_k=5` ordered purely by recency. Once a user has atoms across more than five domains, the oldest domains drop out of Layer 3 entirely — measured, not predicted: `childhood` vanished completely from a seeded six-domain probe.

The recorded trigger is **a pilot family reaching roughly session 5**. At a daily cadence that is inside this sprint. Do not wait for it to fire.

Sprint 1's S2.5 already did the fetch-wider-render-bounded work once; this is a re-tune with real pilot data rather than seeded data, and it should be verified against family #1's actual atom spread.

**Acceptance:**
- A test seeds atoms across at least six domains and asserts the earliest domain still reaches Layer 3.
- Rendered thread count stays bounded.
- TC-01–TC-10 at or above target after the change.

---

## T6 — Extraction review queue

**Branch:** `feature/extraction-review`
**Closes:** F-08's third component (G3)

The go/no-go criterion in `docs/PLAN.md` is *"Story extraction accuracy ≥75% (manual validation of first 50 story atoms)"*. That is explicitly a manual process, and there is no surface to do it in.

A list of story atoms with their source transcript side by side, and a correct/incorrect/edit control. Nothing cleverer.

**Droppable.** If the sprint runs long, this is the item to cut — the atoms accumulate regardless and can be validated later. Everything above it fixes something that happens *to a family*; this one fixes something that happens to you.

**Acceptance:**
- Atoms are listed with their originating transcript.
- An operator can mark one correct or incorrect, and the mark persists.
- A running accuracy percentage is displayed over the marked set.

---

## Explicitly out of scope

- **Razorpay, upgrade page, runway indicator** (D4, E1). T3 ships the parent-facing message only. Payment is not needed for a hand-recruited pilot and would double this sprint.
- **The TC-11 resurfacing fix.** Logged in Sprint 1 as needing a focused branch with its own eval loop. Doing it inside a sprint full of prompt-adjacent changes is how it gets tuned until it passes. Its trigger — using the rubric to gate a prompt change — does fire in T3 and T5, so treat rubric failures there with the caution Sprint 1 recorded rather than fixing the subsystem in passing.
- **Dashboard rebuild, day-0 state, story detail with audio** (C1, C2, C4). The buyer-facing experience. Real, and next.
- **Search, people and places** (C3, C7). F-10.
- **Share sheet and public card page** (C5, C8).
- **Landing page** (A1/A3). No acquisition surface is needed while families are hand-recruited.
- **DPDP processor disclosure.** Logged in Sprint 1 with the trigger "before family #2" — which this sprint reaches. It is a copy change, not a workstream; do it when that trigger fires and note it in `Found during Sprint 2`.

---

## Suggested sequencing

| Days | Workstream | Ship gate |
|---|---|---|
| 1–3 | T1 — ops console | Cohort health, family detail, crisis events, kill switch |
| 3–5 | T2 — silence handling | Ladder fires and resets, visible to operator |
| 5 | T3 — trial pause message | Parent is told before the calls stop |
| 5–7 | T4 — settings | Schedule, language, numbers editable; pause works |
| 7 | T5 — retrieval window | Six-domain probe keeps the earliest domain |
| 7–9 | T6 — extraction review | 50 atoms validatable in a UI |

T1 first — it is what makes T2 through T6 observable. **T3 is small and time-boxed by family #1's session count, not by this ordering; if they are approaching session 8, do T3 immediately regardless of where T1 stands.**

T5's trigger also fires on a calendar rather than on this table. Watch family #1's atom spread.

---

## Found during Sprint 2

<!-- Append anything discovered that is not covered above. Do not fix in-scope. -->
