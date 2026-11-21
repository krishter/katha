# Stories (Phase B and C): build plan

**Status:** Plan agreed 2026-11-10 (Krish took every recommendation in §3). Stage 1 built (§6a, merged #67); Stage 2 built (§6b); Stage 3 next.
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
| D-a | Does Krish review every Story before she hears it? | ~~**Yes**, as for cards, with a flag to turn it off once the fidelity record is long enough.~~ **Reversed 2026-11-15 (Krish): no.** Reading another family's stories is a privacy invasion by a stranger. No one at Katha reviews Stories or cards; see §6c. |
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

## 6a. Stage 1 record (2026-11-10)

Built behind `STORIES_ENABLED` (off) and `user_profiles.stories_enabled` (off):

- **Storage:** the `stories` and `story_versions` tables, plus `story_atoms.story_id`. The CHECK constraint refuses an approval unless the operator has passed the Story and `approved_hash = readback_hash`. Tested against Postgres.
- **The pass** (`stories/consolidate.py`) runs in `_produce_session_outputs`, before card drafting.
  - It reads only turns that aren't private and whose extraction is done, and passes her private topics to the grouping step. The grouping prompt is unchanged when there are none.
  - It carries her 15 most recent open threads and their source turns, so a thread is finished and composed from every telling.
  - It never runs twice for one session.
- **Read-back** (`stories/readback.py`): written in her language, then checked both ways against the English body: nothing added, nothing left out. There is one rewrite; a Story whose read-back still fails can't be passed.
- **Operator page:** `/admin/stories/view`, with pass, rewrite read-back and retire.
- **D-f:** a Story's atoms leave the card pool.
- **Family side:**
  - Erasure deletes stories and versions.
  - `/family/export` adds `first_person_stories`, approved only, through `stories.store.approved_stories()`.
  - `scripts/backfill_stories.py` is written; it hasn't been run.
- **Cost:** each step is its own `api_usage` operation (`story_group`, `story_compose`, `story_fidelity`, `story_readback`, `story_readback_check`).

**Evals.** TC-32 to TC-38, renumbered from Phase A, pass 7/7 on Sonnet 4.6.

**D-e, the Haiku trial:** with `LLM_STORY_GROUP_MODEL=claude-haiku-5-5`, TC-36 failed. The thread finished in session 3 was never promoted, and carrying a thread across sessions is the case Stories need most. Haiku's grouping also wrote about twice the output tokens. Grouping stays on Sonnet. This was one sample; a later trial needs TC-36 at 3+ samples.

## 6b. Stage 2 record (2026-11-15)

Built behind the same two switches, both off. Branch `feature/stories-readback`.

- **The ask** (`stories/asks.py`, wired into `cards/asks.py`): `story_readback_offer` and `story_readback`. A Story is offered only when the operator has passed it, its read-back passed its check, its fingerprint still matches its words, it has been offered fewer than 3 times, and her sharing preference isn't `never`. It shares the session's one ask with cards and child questions; whichever has waited longest goes first, and on a tie the order cards always used holds (D-b). The read-back is the stored text, spoken with `story_readback_tts`.
- **Her answer:** `approve` counts only on the fingerprint she heard, and is refused otherwise. `keep_private` gives `kept_private`. `not_now` and unclear answers leave it `pending`. After 3 offers it stays `pending` and is never sent. Her answer and its turn are stored (`parent_answer`, `parent_answer_turn_id`, `parent_answered_at`; migration `a8c3e5f7b2d4`).
- **Correction** (`stories/correction.py`): runs after the correction turn's extraction, so a private correction is never a source. The version she heard goes to `story_versions`. The Story is composed again from every telling plus her correction (an optional correction block in the compose and fidelity prompts; without one they are byte-identical to Phase A's), checked, given a new read-back, and sent back to the operator with its offer count reset. A second correction, or one that fails its check, goes to the operator by hand with an alert. If the check cut sentences, a note on the Story says so.
- **Privacy after the fact** (`stories/privacy.py`): when a turn becomes private, every Story citing it becomes `kept_private`, at any stage, approved included.
- **Read-back check:** gives its reason before its verdict. One Malayalam check had marked a sentence unsupported while its own reason concluded it was supported.
- **Requires cards.** The ask lives in the cards flow, so a family needs conversation cards on for a Story to be offered. New families have them; the two current pilot families do not.

**Evals** (`run_eval.py stories`, 3 runs, about ₹13 a run):

| Case | Result |
|---|---|
| TC-39 private turns | 2/3; no leak in any run. The third promoted no Story (a fidelity cut broke the arc), so there was nothing to judge |
| TC-40 family sees only approved | Integration tests, deterministic |
| TC-41 correction | First build **0/3**: her correction "it was my uncle who carried grandmother; Appa was away" made compose say the uncle also woke them, the check cut that sentence, and the Story lost its turn while the arc test still passed. Option (b), chosen by Krish: when the check cuts sentences from a corrected Story, compose runs once more, told what was cut and why, and the second try is kept only if it is a Story with fewer cuts. After it, 3 runs: **1/3 right** (run 3: the retry ran, uncle carried her, Appa away, nothing invented). In the other 2 the fidelity check passed "My uncle Kunjappan woke us up", which she never said, so nothing was cut and no retry ran. The scorer scores those 3/3 (after fixing a bug: "Kunjappan" contains "appa"), because it has no check for an invented detail. Open: the fidelity check on corrections |
| TC-42 one ask | 3/3 |
| TC-43 read-back, ml/ta/hi | 9/9 |
| TC-32 to TC-38 consolidation | 7/7 |

**Regression, cards on:** TC-01 to TC-11 objective 13/15 and rubric 13/15, with TC-03 at 6/6. TC-08 was 1/3 and TC-11 1/3 (resurfacing Mr. Iyer), their known weaknesses. With Stories off, the turn's path is the same as `main`'s; pooled with the same day's gate run on `main`, they are 3/6 and 4/6. The cards cases TC-12 to TC-18: objective 5/5, rubric 2/2. TC-19 to TC-31 were not run: nothing they exercise changed.

## 6c. No one at Katha reviews (2026-11-15)

**Decision (Krish):** no one at Katha reads a family's Stories or cards before she hears them. "It feels like a privacy invasion by a complete stranger." This reverses D-a here and D3 in `CONVERSATION_CARDS.md`.

- **Off by default:** `STORIES_OPERATOR_REVIEW` and `CARDS_OPERATOR_REVIEW`. A card or Story that passes its checks goes straight to her read-back. The safeguards before the family are the machine checks and her own yes on the exact words she heard.
- **Alerts carry ids, never a title or story text.** Kept: the crisis alert, which includes her words so a person can check on her the same day, and the alert for a child's message that needs a person.
- **A Story she never heard whose read-back fails its check** is set aside (`retired_reason = fidelity`), and its pieces go back to the card pool.
- **When Katha can't write her correction** (the rewrite or its read-back fails its check, or her correction can't be used), she decides, not Katha:
  - The card or Story keeps the words she last heard, and waits (`needs_retelling`).
  - In a later session, one ask, never mid-story, Katha says as its own shortcoming that it couldn't write that part the way she told it. It names the part as a question from the check's reason, never repeating the cut sentence, and asks whether she'd like to tell it again or set it aside (`retell_offer`, classified by `parent_answers.RETELL`).
  - **Tell it again:** Katha asks for that part in her own words (`retelling`); her answer becomes a source (`added_source_turn_ids`), and the rewrite runs after that turn's extraction settles its privacy.
  - **Set it aside:** a Story retires (`set_aside`) and its pieces return to the card pool; a card becomes `kept_private`.
  - **Not now / unclear:** asked again another day.
  - From the second failed rewrite, Katha mentions that setting it aside is perfectly all right. There is no limit: it goes on until she chooses.
  - The second-correction limit applies only with operator review on. Migration `b9d4f6a8c3e5`; TC-45.
- **The spoken notice** (`consent_processing`, now `parent-1.2`) says no one at Katha reads her stories, except to check on her safety or to fix a problem with the service. A 1.1 agreement still stands, because 1.2 says less is done with her words. The 12 translations are machine-written and unread by native speakers. The privacy page is version 1.2. Counsel reviewed both (Krish, 2026-11-21).
- **Still open:**
  - ~~A child's question screened "sensitive" still waits for an operator to release it.~~ **Decided 2026-11-21 (Krish): she decides.** With `CARDS_OPERATOR_REVIEW` off, no one at Katha reads it and no operator is alerted. At a greeting or the closing exchange, one ask per session like any other, Katha tells her that her child sent a question about something personal, without saying what it is, and asks whether she would like to hear it now, another day, or not at all (`question_offer`, classified by `parent_answers.QUESTION_OFFER`; a yes must quote her). Yes: released and asked in that same reply, credited to the child. No: `declined`, never asked again, and the child is not told. Not now or unclear: asked again another day, at most 3 times (`family_questions.parent_offers`), within the usual 21-day expiry. The admin page lists held questions by id only. With review on, the operator path returns. Migration `c3e7a9d5f1b8`; TC-46.
  - ~~`/admin/cards/view` and `/admin/stories/view` still show full text.~~ **Done 2026-11-21:** with `CARDS_OPERATOR_REVIEW` / `STORIES_OPERATOR_REVIEW` off (the default) both pages show ids, states and dates only: no title, card or Story text, quotes, read-back, image, fidelity sentences or transcripts. The full review view returns only if review is turned back on. Both pages are reachable only through the operator's SSH tunnel since SH-10 (#73). Held child questions are still shown in full (the item above).
- **Evals, 2026-11-20** (after rebasing on main):
  - **TC-41, 3/3 right**, with the fidelity rule for corrections (693f812) run live for the first time: uncle carried her, Appa away, and no run invented "uncle woke us up" (the unscored check found it in none). This closes the open TC-41 line in §6b.
  - **TC-45.** The first runs failed every ask on `no_unsaid_detail`, which banned the word "woke". This section tells Katha to name the part as a question from the check's reason ("she never said who woke them up"), so the scorer was banning what the spec asks for. With Krish's approval it now fails only the cut detail: uncle or Kunjappan in the same sentence as "woke". Rescored, the ask after one failure asked two questions in 3 of 6 samples ("Who was it that woke everyone up? Would you like to tell me that part again, or…"), because the prompt asked for the choice and, separately, for the part as a question. The prompt now puts the part inside the one question. After it, 3 runs: **every TC-45 check passed** (asks 6/6, her answer 8/8 each run, rewrite 3/3).
  - **Regression (TC-01 to TC-31, 3 samples):** objective 91/102, rubric 48/54. TC-08 1/3 (known), TC-09 2/3, TC-30 en 0/3. The changed instruction only reaches a prompt when a Story or card needs retelling, which no regression case seeds.

## 7. Risks

- **Words in her mouth.** This is the risk that matters most. Fidelity runs on the body and the read-back, and her approval comes before the family sees it. With no operator review (§6c), a detail the checks miss is heard by her before anyone else: TC-41 is the case to watch.
- **The read-back translation.** The family reads English; she approved her own language. The two texts are checked against each other (TC-43), and the label says "translated".
- **Cost.** Consolidation roughly doubles a session's LLM cost. That's measured per session in `api_usage`, and D-e is the lever.
- **Pending pile-up.** Stories wait if read-backs are skipped. Pending age goes on the ops console. If it grows, the read-back design needs work, not the approval rule.
- **Over-merging two incidents.** It's covered by the interleaving case, and the operator can retire a Story and split its atoms back into the pool.
