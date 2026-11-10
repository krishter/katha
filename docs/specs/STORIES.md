# Stories (Phase B and C): build plan

**Status:** Plan agreed 2026-11-10 (Krish took every recommendation in §3). Stage 1 in progress.
**Branch:** `feature/stories`, one PR per stage (§6). Never commit to `main`; see `.claude/rules/git.md`.
**Design:** `docs/proposals/first-person-stories.md` (the proposal; its decisions D1 to D5 stand). This document is how to build it on today's code, and where today's code changes the proposal.
**Depends on:** Phase A (`consolidation/`, merged in October), conversation cards (`cards/`, merged in #46), finishing stories (#66, deployed 2026-11-10).

---

## 1. Where we are

- **Phase A ran on 2026-11-07,** over one pilot parent's 11 conversations. It found 14 incidents and 3 complete Stories. All 3 passed the fidelity check. Of the 9 open threads, 8 had no turn: the conversation never asked what happened next.
- **#66 changed the conversation to fix that.** Katha now follows an incident to its end and doesn't close mid-story. Whether more incidents now finish shows up in the Phase A rerun on new sessions, around 2026-11-24 (`FINISHING_STORIES.md` §5, target D1 40%).
- **The proposal's read-back now exists, built for cards.** Cards already do most of what Stories need on the parent's side:

| Stories needs (proposal) | Already built for cards |
|---|---|
| Read the text back in her language, in her pinned voice (§5.4) | `readback_text` written with the card, spoken through `media/tts.py` |
| Offer once per session, never mid-story, up to 3 sessions (§5.4, Q-A) | `cards/asks.py`: one ask per session, `MAX_READBACK_OFFERS = 3`, timing rules |
| Classify her answer: approve, correct, keep private, unclear | `core/parent_answers.py`, with TC-15's no-false-approve check |
| Approval can't be faked by the app | CHECK constraint: `approved` needs `approved_hash = readback_hash` |
| Private turns never feed anything shared | `cards/privacy.py`, `not_private_atom()` |
| Sentence-by-sentence fidelity check | `consolidation/pipeline.compose_and_check`, reused by `cards/write.py` |
| Operator review before she hears it | `/admin/cards/view` |
| Delivery on WhatsApp and email, before the child's call, cadence limit | `cards/schedule.py`, `cards/deliver.py` |
| Sharing preference | `user_profiles.share_preference`, asked in session 2 |

So the build is mainly three things: storing threads and Stories, running consolidation after every session, and showing Stories to the family. The read-back reuses the cards code rather than being built a second time.

## 2. What the build does

### 2.1 After each session: consolidate (Stage 1)

`close_and_process_session` runs `consolidation.consolidate_session` after `run_post_session` and before card drafting. It never raises (the same rule as all post-session work).

- **Inputs:** this session's turns and atoms; the user's open threads from the `stories` table (cap 15, most recently seen, per proposal §6.2); the source turns of those threads, so a thread opened last week can be finished and composed today; significant people from the fact store.
- **Private turns are removed before anything is sent to the model.** A thread can't be built from a private turn, and an atom from one is never assigned to a thread.
- **Output:** new and extended threads written as `stories` rows with `status = thread`. Threads whose arc passes D1 (setting, event, turn), and whose draft survives the fidelity check, become `status = story` with `approval_status = pending`.
- **A promoted Story gets a read-back,** written in her language the way a card's is, and checked against her transcripts like the English body. The same fingerprinting as cards ties her approval to the exact text she heard.
- **Model:** `claude-sonnet-4-6`, the model Phase A was evaluated on. Phase A measured 6,500 input and 1,070 output tokens per session, about $0.036 (~₹3) per session. That roughly doubles a session's LLM cost (about ₹3 for eight exchanges in the evals) and adds about 13% to its total of ~₹23 (`docs/specs/COST_TO_SERVE.md`). Every call is recorded in `api_usage` under its own purposes (`story_group`, `story_compose`, `story_fidelity`, `story_readback`). §3 D-e covers whether to try Haiku for grouping.

### 2.2 Operator review (Stage 1)

As with cards (cards D3), Krish reads every Story before she hears it, on an admin page beside `/admin/cards/view`. The page shows the body, its source sentences, and the read-back. The operator can pass a Story, hold it, or retire it. A flag turns review off later.

### 2.3 In the next session: read it back (Stage 2)

A Story waiting for her becomes a new ask kind in `cards/asks.py` (`story_readback_offer`, `story_readback`). It shares every cards rule:

- one ask per session;
- never while she is telling something;
- offered in up to 3 sessions, then left `pending`, never sent.

Her answer goes through the existing classifier:

| She says | Result |
|---|---|
| Approve | `approved`, and the family is told (§2.4) |
| A correction | The correction turn is added as a source, the Story is re-composed and re-checked as a new version, and it goes back to `pending` for one more read-back (proposal §6.4) |
| Keep it private | `kept_private`, never shown to the family |
| Not now / unclear | Stays `pending`, counts as one offer |

A Story read-back is longer than a card's. Phase A's three Stories were 80 to 165 words, about one minute of speech (~₹1.14 at Gemini's rate, doubling from 1 January 2027). Q-C stands: read it in full, keep Stories short by design.

If her sharing preference is `never`, no Story is offered. Threads are still kept, for Stage 4.

### 2.4 For the family (Stage 3)

- **Dashboard.** The Stories page lists approved Stories only, in life order by chapter. Each shows its title, the first-person body labelled "In Amma's words, translated from Malayalam", her original-language quotes with a gloss, and "told across N conversations". Atoms move to a quieter "Moments" list (proposal §7.1, §7.3). The headline count becomes Stories, not atoms. `sort_year` orders the list and is never displayed.
- **Story-ready message.** It's sent when she approves a Story, on WhatsApp and email, timed by `cards/schedule.py` like a card. It contains the title, the opening lines and a link. A Story and a card share the cadence limit (§3 D-b), and no family gets more than one message a day (proposal D2). The WhatsApp template needs Meta approval. Its category decides whether +1 numbers get it at all (`META_TEMPLATE_CATEGORIES`, unknown counts as Marketing), so the template is registered at the start of Stage 3, not at the end.
- **Export and deletion.** `/family/export` includes approved Stories. Her own export includes everything, `kept_private` too. `stories` and `story_versions` join the deletion list in `api/routes/admin.py`, with an end-to-end test.

### 2.5 Unfinished stories in the conversation (Stage 4)

This is the proposal's §9, deferred from #66 (`FINISHING_STORIES.md` §2.3) because counting asks per incident needs the incident stored. Layer 3 gets an "unfinished stories" block of at most two threads, each naming its missing element. It's raised at most once a session, at a natural pause. A thread asked twice and not taken up is retired. The remembered-person anchor still goes first, and set-aside topics are never raised. It's a prompt change, so the full regression runs.

## 3. Decisions

Decided 2026-11-10 (Krish): every recommendation below.

| # | Question | Decision |
|---|---|---|
| D-a | Does Krish review every Story before she hears it? | **Yes**, as for cards, with a flag to turn it off once the fidelity record is long enough. |
| D-b | Do Stories and cards share the one-ask-per-session budget and the 2-in-7-days delivery cadence? | **Share both.** The waiting item that has been waiting longest goes first. Two systems each asking once a session would make two asks, which breaks cards' non-negotiable 7. |
| D-c | Do we consolidate existing history for the current pilot families? | **Yes, once**, as a backfill script that writes threads and pending Stories through the same review and read-back. Otherwise the 9 open threads Phase A found are lost to Stage 4. |
| D-d | Start now, or wait for the 2026-11-24 rerun? | **Build now, flag off. Turn it on after the rerun and a self-trial.** The schema, pipeline and dashboard are needed whatever the rerun shows; only the go-live depends on it. |
| D-e | Sonnet 4.6 for all three steps (~₹3 a session), or try Haiku for grouping? | **Ship on Sonnet.** Run the consolidation eval with Haiku grouping in Stage 1 and switch only if it holds. Grouping is the largest share of tokens. |
| D-f | Can material in a pending or approved Story still be used for a card? | **No.** Its atoms leave the card pool, so the child never gets the same moment twice, and a card can't leak a Story she chose to keep private. |

## 4. Data model

Proposal §8 stands, with these changes for today's code:

- **Approval fields mirror `conversation_cards`:** `readback_text`, `readback_language`, `readback_hash`, `approved_hash`, `readback_offers`. The same CHECK constraint holds: `approval_status = 'approved'` requires `approved_hash = readback_hash`.
- **`story_versions` keeps each prior body and read-back.** The family sees `approved_version` while a newer one waits.
- **`story_atoms.story_id`** (nullable FK). An atom belongs to at most one thread.
- **`retired_reason`:** merged, operator, declined twice (Stage 4), or the arc failed after fidelity.
- **Deletion and the approved-only filter are tested,** not just written (proposal §8). The family API reads `stories` only through one helper, `approved_stories()`, the way atoms go through `not_private_atom()`.

## 5. Evaluation

- **Numbering.** TC-12 to TC-18 mean two things today: the cards cases in `TECH_DESIGN.md` §3.3, and the consolidation fixtures in `run_consolidation_eval.py`. The consolidation cases are renamed to TC-32 to TC-38, and new cases continue from TC-39.
- **Existing consolidation cases** (grouping, interleaving, fidelity, arc test, cross-session, code-mixing, hedged dates) run in every stage that touches `consolidation/`.
- **New cases:**
  - **TC-39, private turns:** a thread whose turns are partly private never uses a private turn in its body, read-back or quotes.
  - **TC-40, the family sees only approved Stories:** `pending` and `kept_private` Stories never appear in any `/family/*` response, the export, or a story-ready message. Objective, 100%.
  - **TC-41, correction:** a correction at read-back produces a new version that uses it and goes back to `pending`; the family still sees the previous approved version, or nothing.
  - **TC-42, one ask:** with a Story and a card both waiting, a session makes one ask, never mid-story.
  - **TC-43, read-back fidelity:** the read-back in her language says nothing the English body doesn't. Run in Malayalam, Tamil and Hindi.
  - **TC-44, unfinished story declined (Stage 4):** asked twice and passed over, it isn't asked a third time.
- **Rubric, human:** Krish listens to Malayalam read-backs before go-live, as for #66.
- **Regression:** Stage 2 and Stage 4 change the conversation, so TC-01 to TC-31 run on both, with TC-03 at 6+ samples (see #66).

## 6. Stages

| Stage | Scope | Changes for families |
|---|---|---|
| **1. Store and consolidate** | Migration, models, deletion and export, consolidation in `close_and_process_session` behind `STORIES_ENABLED` (off), read-back writing, operator page, cost tracking, Haiku grouping trial, backfill script (not run) | None while the flag is off |
| **2. Read-back** | Story asks in `cards/asks.py`, classifier, corrections and versions, shared cadence; TC-39 to TC-43 | None while off; full regression |
| **3. Family** | Dashboard Stories and Moments, life order, story-ready message and template, export | None while off |
| **4. Unfinished stories** | Layer 3 block, ask limit, retirement; TC-44 | Prompt change when turned on; full regression |
| **Go-live** | After the 2026-11-24 rerun: self-trial, then the backfill (D-c), then the flag on per family, as for cards | Yes. Needs Krish's explicit go |

Each stage is one PR. Stages 1 and 2 are the bulk of the work; Stage 3 needs Meta's template approval, so its template is submitted on the first day of Stage 3.

## 7. Risks

- **Words in her mouth.** This is the risk that matters most. Fidelity runs on the body and the read-back, operator review comes before she hears it, and her approval comes before the family sees it.
- **The read-back translation.** The family reads English; she approved her own language. The two texts are checked against each other (TC-43), and the label says "translated".
- **Cost.** Consolidation roughly doubles a session's LLM cost. That's measured per session in `api_usage`, and D-e is the lever.
- **Pending pile-up.** Stories wait if read-backs are skipped. Pending age goes on the ops console. If it grows, the read-back design needs work, not the approval rule.
- **Over-merging two incidents.** It's covered by the interleaving case, and the operator can retire a Story and split its atoms back into the pool.
