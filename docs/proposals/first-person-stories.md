# Proposal: From fragments to first-person Stories

**Status:** Draft. Open questions decided 2026-10-11 (see §13), not yet implemented
**Author:** Krishnaraj CK
**Date:** 2026-10-11
**Related:** `backend/prompts/system_prompt.py` (`build_extraction_prompt`), `backend/extraction/story_extractor.py`, `backend/core/orchestrator.py` (`close_and_process_session`, `build_prior_context`), `backend/memory_cards/generator.py`, `backend/api/routes/family.py`, `docs/TECH_DESIGN.md` §2.4, `docs/PRD.md` §6.3, §7.4

---

## 1. The observation

In the pilot sessions so far, people talk freely. They describe things in detail, jump between topics, and come back to an incident three voice notes, or three days, later. This is what we want: it means they are comfortable, and it is why Katha uses conversation and not a questionnaire.

But what Katha saves from these conversations is not a story. A story is an account of one incident with a beginning and an end: what was going on, what happened, the moment things turned, and how it ended or what it meant. Today we save something else: a short summary of each voice note.

This proposal keeps the free-flowing conversation exactly as it is. It adds a step afterwards that **assembles stories from the conversation** and writes them **in the storyteller's own first-person voice**.

## 2. What exists today

### 2.1 Extraction is per turn and only sees one turn

After each exchange (one user voice note plus Katha's reply), `run_extraction_for_turn` makes a background LLM call using `build_extraction_prompt`. The prompt contains **only that one exchange**. It returns zero or more `story_atoms`, and each atom is tied to a `turn_id`.

So a single incident told across four voice notes, with a detour in the middle, becomes four or five separate atoms. They may even be tagged with different domains. Nothing ever joins them back up:

- `story_extractor.process_extraction` inserts the atoms and stops.
- `run_post_session` does look at the whole session transcript, but only for entity extraction into the fact store. Its output is for Katha's memory and the family never sees it.

### 2.2 An atom is a third-person summary

The extraction prompt asks for `narrative: "a 1-3 sentence summary of what was said"`. The result reads like a case note ("He describes the house in Madurai…"), not like the person telling it.

### 2.3 Completeness measures metadata, not story

`compute_completeness` counts how many of who / what / when / where / why are filled in, and TECH_DESIGN §2.4 calls a score of 3 or more "publishable". A fragment that names a person, a year and a town scores 3 even with no beginning, middle or end. The quality bar cannot see the problem this proposal is about.

### 2.4 What the family sees

| Surface | Source | Problem |
|---|---|---|
| Dashboard "Stories captured" | `count(story_atoms)` | Counts fragments, so the number overstates what the family has |
| Stories list | All atoms, `ORDER BY created_at DESC` | Ordered by when it was said, not when it happened, so it reads as a summarised chat log |
| Story detail | One atom | Two or three summary sentences |
| Memory card | Highest-completeness atom's quote, once per session | Good as a daily touchpoint. It is a quote, not a story |

### 2.5 Promised but not supported by the data model

- **Printed memory book** (PRD: Family Plus, ₹2,000 add-on). A chapter cannot be made from 1–3 sentence fragments.
- **Audio clips.** `story_atoms.audio_timestamp_start/end` exist, but the extraction prompt never asks for timestamps, so they are always null. More basically, **the storyteller's own voice notes are not stored**: only Katha's outgoing audio (`turns.response_audio_s3_key`, `sessions.session_open_audio_s3_key`) is. Audio clips of a parent telling a story need inbound audio storage, which needs its own consent (PRD §13.1 point 4). That dependency is out of scope here and listed in §11.

## 3. The proposal in one picture

```
 voice notes ──► turns (transcripts) ──► story atoms        (unchanged: per turn, feeds Katha's memory)
                                                │
                       after each session       ▼
                                      ┌─ Consolidation pass ─┐
                                      │ 1. group by incident │
                                      │ 2. check the arc     │
                                      │ 3. write 1st person  │
                                      │ 4. fidelity check    │
                                      └──────────┬───────────┘
                                                 ▼
                         ┌───────────────────────┴───────────────────────┐
                    Thread (arc incomplete)                    Story (arc complete)
                    "the 1962 flood: no ending yet"            "The night the river came in"
                         │                                              │
                         ▼                                              ▼
              Katha asks for the missing piece,          Katha reads it back to the storyteller
              gently, in a later session                  ──► approved ──► family dashboard,
                                                                          story message, book
```

There are three layers, each with one job:

| Layer | What it is | Who uses it |
|---|---|---|
| **Atom** (exists) | What was said in one voice note | Katha's memory (Layer 3), the source material |
| **Thread** (new) | One incident we have heard part of | Katha, to ask for what's missing; optionally the family as "in progress" |
| **Story** (new) | One incident with a complete arc, written in the storyteller's own first-person voice | The storyteller first, to approve it. Then the family: dashboard, story message, book |

Atoms stay exactly as they are. The live conversation changes in two places only: the read-back of a finished Story for approval (§5.4) and, later, asking for missing endings (§9).

**A Story belongs to the storyteller.** Only they can approve, correct or withhold it. The family reads Stories; it never edits them (§7.5).

## 4. What counts as a Story: the arc test

The five W's describe a fact, while an arc describes an account. A Thread becomes a Story when the consolidation pass can find, **in what the person actually said**, at least the first three of:

| Arc element | Question it answers | Example (flood) |
|---|---|---|
| **Setting** | Where and when, and what life was like then | "We were living near the river in Kumbakonam, I was maybe ten" |
| **Event** | What happened | "The water came into the house in the night" |
| **Turn** | The peak: the decision, surprise or moment that mattered | "Appa carried my grandmother on his back to the temple steps" |
| **Resolution** *(strongly preferred)* | How it ended, or what it meant to them | "After that, he never let us build anything on the low side again" |

Rules:

- **Setting + Event + Turn** is the minimum for a Story *(decided, §13 D1)*. Resolution is preferred but not required. Phase A data will show whether to raise the bar.
- Each element must point to the turn(s) it came from. An element the model inferred but the person did not say **does not count**.
- An arc may be assembled from turns spread across sessions. That is the point.
- Some things people say are vivid but are not incidents: a description of a kitchen, a portrait of a grandmother. They stay as atoms (and may become "Moments", see §7.3). We do not force them into a story shape.

This replaces 5W completeness **as the bar for what the family sees**. `completeness_score` stays on atoms as an extraction-quality signal.

## 5. The first-person voice

Stories are written as the storyteller telling it: *"I was maybe ten when the river came into our house…"*, not *"He recalls that…"*.

This reads like a memoir, makes the book possible, and is what families will want to keep. It is also the riskiest choice in this proposal, because we would be writing sentences in a real person's voice, attributed to them, and their children will read those sentences as their parent's words. The rules below exist to keep that honest.

### 5.1 Composition rules (for the compose prompt)

1. **Source is the turn transcripts, not the atom summaries.** Atoms are already lossy third-person summaries. Writing first person from them means paraphrasing a paraphrase. The compose step reads the raw `turns.transcript` rows for the source turns.
2. **Their words first.** Keep their phrasing, their order of telling where it works, their idioms and their code-mixed words (`ammachi`, `thinnai`, "that fellow"). Edit for flow by **cutting and joining**, not by rewriting.
3. **Add nothing that wasn't said.** No invented dialogue, sensory details, emotions, motives or dates. If they didn't say they were scared, the story doesn't say it. Connecting words ("Later that night", "So") are allowed. New facts are not.
4. **Uncertainty stays uncertain.** "I think it was 1962, or maybe '63" is not tidied into "In 1962".
5. **Katha is not in the story.** Katha's questions are context for understanding the answers and are never quoted or paraphrased into the story.
6. **Length follows what was said.** A story told in four short voice notes becomes a short story. We do not pad.
7. **Title in their words** where a phrase of theirs works ("The night the river came in"). Otherwise a plain descriptive title.

### 5.2 Language

The MVP rule is "capture in the user's language; English summary only" (CLAUDE.md, MVP scope). For Stories:

- The **story body is in English** for MVP and labelled on the dashboard as *"In Amma's words, translated from Tamil"* (or the detected language). We do not present a translation as if it were a transcript.
- **Quotes are kept in the original language and script**, with an English gloss underneath. The original line is what a grandchild will want to hear or read out at a birthday.
- An original-language Story body is a Phase 2 item, once multi-language output is in scope.

### 5.3 Fidelity check (separate call)

After composing, a second call checks the draft against the source transcripts **sentence by sentence**. Each sentence is marked `supported`, `connective`, or `unsupported`.

- Any `unsupported` sentence is removed and the draft is re-checked. If the arc no longer holds after that, the Story goes back to Thread status.
- The result is stored (`fidelity_status`, plus each sentence's source turn ids), so the dashboard can show "told across 3 conversations" and so we can audit any Story later.

### 5.4 Storyteller approval (required before the family sees a Story)

*Decided, §13 D4.* No Story reaches the family until the storyteller has heard it and agreed. This is the strongest protection against putting words in someone's mouth, and it keeps the Story theirs.

**How it works:**

1. When a Story is promoted, it waits with `approval_status = pending`. The family sees nothing yet: no story message and no dashboard entry.
2. In the **next session**, Katha offers it once, at the start or at a natural pause: *"Last time you told me about the night the river came in. I've written it down the way you told it. Shall I read it to you?"* If they say yes, Katha sends the story as a Bulbul voice note in their language (the English body is translated back for the reading, see Q-B).
3. Their answer decides what happens next:

| They say | Result |
|---|---|
| Yes / that's right | `approved`, and the family gets the story message (§7.2) |
| A correction ("it was my uncle, not my father") | The correction is a new turn. The Story is re-composed with that turn as a source, re-checked, and goes back to `pending` for one more read-back |
| "Don't show the children" / they don't want it shared | `kept_private`. The Story is never shown to the family. It stays in the storyteller's own record and is deleted with everything else on request |
| Not now / no clear answer | Stays `pending`. Offered again in a later session (see Q-A) |

**Guardrails:**

- At most **one** read-back per session, so the conversation stays a conversation.
- A read-back never interrupts a story being told.
- The storyteller can come back to a Story later. Saying "change the flood story" in any session re-opens it.

**Cost:** one Bulbul read of a few hundred words per Story (~₹0.50/min) plus some session time. This is small next to the trust it protects, but it goes into per-session unit economics.

This is no longer an optional later phase. It is part of the build (§11), because without it no Story ever reaches the family.

## 6. The consolidation pass

### 6.1 When it runs

It runs in `close_and_process_session`, after `run_post_session` and before the memory card. A Story promoted here waits for approval (§5.4). It is not sent to the family from this step. It is off the critical path like the rest of post-session work. It logs failures and never raises.

Sessions that close without a clear end (no `goal_met` or `session_end_suggested`, for example the user simply stops replying) are already closed through the same entry point. They need no special handling.

### 6.2 Inputs

- This session's turns (transcripts) and atoms.
- The user's **open Threads**: their arc state and source turn ids, so a session that finally supplies the ending can complete a thread from last week.
- Known significant people from the fact store, so the same person is recognised under a different name ("Appa", "my father", "Ramaswamy").

The open-thread list is capped at the N most recent (start with 15), so the prompt stays bounded as history grows. This is the same starvation trade-off described in `embedding-strategy.md`: older threads drop out of view. The fix is the same as there (semantic retrieval in Phase 2), and until then raising the cap is the cheap stopgap.

### 6.3 Steps (three LLM calls, all `claude-sonnet-4-6`)

1. **Group and assess.** Input: this session's atoms (ids + narrative + who/when/where), their source transcripts, and the open Threads. Output as structured JSON: for each incident, which atoms belong to it, whether it extends an existing Thread or starts a new one, and for each arc element whether it is present and which turn ids support it. An atom can belong to at most one incident.
2. **Compose** (only for incidents that pass the arc test): the first-person story from the source transcripts, following §5.1.
3. **Fidelity check** (§5.3).

Steps 2 and 3 run per promoted incident. On most sessions we expect zero or one promotion, so the typical cost is 1–2 calls, with 3 or more on a strong session. **Token cost per session must be measured during the self-trial** and added to per-session unit economics alongside the Sarvam costs, as CLAUDE.md requires.

### 6.4 Updating an existing Story

If a later session adds to an incident that is already a Story (a new detail, a correction), the pass re-composes it as a **new version**. A new version goes back through approval (§5.4). Until it is approved, the family keeps seeing the last approved version. Earlier versions are kept. If the storyteller contradicts themselves, the newer telling wins in the body and both tellings remain in the source trail. We do not ask the model to reconcile them.

## 7. How the family uses it

### 7.1 Dashboard

| Today | Proposed |
|---|---|
| "Stories captured: 23" (atoms) | **"Stories: 4"** (complete Stories), plus a quieter "23 moments captured" |
| Stories list, newest first | **Approved Stories only**, **in life order**, grouped by life chapter. Ordering uses a `sort_year` estimated from what they said ("I was ten" + known birth year). Stories with no estimate go at the end of their chapter |
| Story detail = one atom | **Story detail** = title, first-person body, original-language quote(s) with gloss, people and places, "Told across 3 conversations: 2, 5 and 9 October" |
| Domain progress (atom count per domain) | Progress = Stories per chapter. An empty chapter is a prompt to the family, not a failure |

Atoms remain available behind each Story as "what Amma said", which links back to the source. This keeps the archive auditable.

### 7.2 Memory cards and story messages

*Decided, §13 D2.*

- **Keep the daily quote card** as it is. It is the buyer's main sign that Katha is working, and with approval in the loop Stories will arrive more slowly than before.
- **Add a "story ready" message**, sent when the storyteller **approves** a Story (not when it is promoted). It contains the title, the opening lines and a dashboard link.
- **One message per day at most.** If a Story is approved in a session that would also produce a quote card, the story message replaces the card.

Before building, check whether the story message needs its own pre-approved template or can reuse the card's, against `outbound-messaging-window.md`.

### 7.3 Threads and Moments

- **Moments** are atoms that are not part of any Story (descriptions, portraits, one-liners). They stay visible as a secondary list. Some of the most loved material will be here ("my mother always kept jasmine in a brass bowl"). It just isn't a story.
- **Threads** (unfinished stories) are **not shown to the family** *(decided, §13 D3)*. We revisit this after we have seen real Threads from the pilot. Stories waiting for approval are hidden too.

### 7.4 Printed book

Chapters come from life periods or domains. Stories within a chapter are ordered by `sort_year`, and each chapter opens with one or two Moments. This becomes a straightforward export once Stories exist. It is not designed further here.

### 7.5 Families read; they do not edit

*Decided, §13 D5.* A Story is the parent's account and stays that way. The family cannot correct dates, names or wording, and the dashboard has no edit control. If a child thinks something is wrong, the right path is to talk to their parent. The parent can then tell Katha, which creates a new version that goes through approval like any other.

Small factual metadata the system estimated (such as `sort_year`) is not shown as fact, so there is nothing there for the family to dispute either.

## 8. Data model

New table `stories`. A Thread is simply a story row whose status is `thread`, which avoids a second table and makes promotion a status change.

| Column | Type | Notes |
|---|---|---|
| `id` | uuid PK | |
| `user_id` | string | indexed |
| `status` | enum `thread` / `story` / `retired` | `retired` = merged into another, or rejected on review |
| `approval_status` | enum `pending` / `approved` / `kept_private`, nullable | null while `thread`. Only `approved` is ever returned by `/family/*` |
| `approval_offered_count` | int | read-back attempts with no clear answer (Q-A) |
| `approved_version` | int, nullable | the version the family sees while a newer one is pending |
| `title` | text | |
| `domain` | string | primary life chapter |
| `when_approx` | text | as said ("when I was ten") |
| `sort_year` | int, nullable | estimate for timeline ordering; never displayed as fact |
| `arc` | jsonb | `{setting, event, turn, resolution}` → `{present, summary, turn_ids[]}` |
| `body_first_person` | text, nullable | null while `thread` |
| `body_language` | string | `en` for MVP |
| `source_language` | string | from `turns.detected_language` |
| `quotes` | jsonb | `[{original, gloss, turn_id}]` |
| `people` / `places` | text[] | |
| `sentence_sources` | jsonb | fidelity map: sentence → turn ids |
| `fidelity_status` | enum `passed` / `revised` / `failed` | |
| `version` | int | previous versions in `story_versions` (same shape) |
| `approved_at` | timestamptz, nullable | set when the storyteller agrees |
| `created_at`, `updated_at`, `promoted_at` | timestamptz | |

Also:

- `story_atoms.story_id` uuid FK, nullable (an atom belongs to at most one Story or Thread).
- `memory_cards.story_id` uuid FK, nullable (for story-complete cards).

**Deletion.** `api/routes/admin.py` deletes per table from an explicit list (line ~111). `stories` and `story_versions` **must be added**, with a case in `test_data_deletion.py` / `test_deletion_e2e.py`. A Story is the most identifying artifact Katha will hold, so missing it there would be a DPDP failure, not a bug.

**Family API.** Every `/family/*` query on `stories` filters to `approval_status = 'approved'` and returns `approved_version`. This needs a test of its own (TC-20), because one missed filter would leak a parent's withheld story to their children.

**Export.** `/family/export` gains approved `stories`. The storyteller's own data export (a DPDP right of theirs, not the family's) includes everything, including `kept_private`. PRD §13.1 point 1 promises export in standard formats.

## 9. Conversation changes (Phase C)

Today Layer 3 offers Katha up to 12 `open_threads`: free-text follow-up hints from atoms, ordered breadth-first across domains (`_extract_open_threads`). These are good, but they are not about finishing anything.

Proposed: Layer 3 gets a small **"unfinished stories"** block above the open threads. It has at most 2 items, each naming the incident and the **specific missing arc element**:

> Unfinished stories (raise at most one, only at a natural pause, never as the session opener):
> - The 1962 flood at Kumbakonam: you know how it started and that Appa carried his mother out. You don't know what happened after.

Guardrails:

- **At most one** unfinished-story question per session, so it never feels like an interview checklist.
- **Never interrupt** a story in progress to finish a different one. If they have moved on, follow them.
- If asked twice across sessions and they don't take it up, stop asking. That silence may itself be an answer, and some endings are painful. Set the thread's `status` to `retired` with a reason.
- This must not compete with the significant-person recall anchor (`_pick_recall_anchor`). If both exist, the person anchor goes first. The two can often be the same thing anyway.

## 10. Evaluation

These extend the regression set after TC-11. The first four can run against fixtures in Phase A, before any schema change.

| ID | Case | Pass |
|---|---|---|
| **TC-12** | One incident told across 4 turns with an unrelated detour in turn 2 | Exactly one Thread/Story, detour atom not included |
| **TC-13** | Two incidents interleaved in one session | Two separate Threads, no cross-contamination |
| **TC-14** | Fidelity: compose from transcripts with a deliberately thin turn | Zero unsupported sentences in the final body (rubric + sentence check) |
| **TC-15** | Setting + Event only, no Turn | Stays `thread`, missing element = `turn` |
| **TC-16** | Session 1 gives setting+event, session 3 gives turn+resolution | Thread promoted in session 3, body uses turns from both |
| **TC-17** | Code-mixed transcript (Tamil-English) | Original-language quote preserved verbatim; native terms kept in body |
| **TC-18** | Storyteller hedges a date ("'62 or '63") | Hedge preserved in body |
| **TC-19** *(Phase C)* | Unfinished-story question asked twice and declined | Not asked a third time |
| **TC-20** | Stories in `pending` and `kept_private` | Never returned by any `/family/*` endpoint or export; no story message sent |
| **TC-21** | Storyteller corrects a name during read-back | New version uses the correction, goes back to `pending`, family still sees the previous approved version (or nothing) |
| **TC-22** | Storyteller says "don't tell my children" during read-back | `kept_private`, no message, not on dashboard |

The rubric for first-person quality (a reviewer checks: does this sound like the person? is anything said that they didn't say?) needs a human reviewer in Phase A. That is Krish, on his own self-trial data and with consent on pilot data.

**Metrics** (replacing "20+ story atoms per user per month" in PRD §11.1):

- Complete Stories per user per month. The target is set after Phase A from real data; don't guess it now.
- Thread → Story conversion rate, and median sessions to completion.
- Fidelity: share of drafts needing revision; share failing outright.
- Family engagement per Story vs per atom (dashboard views).

## 11. Rollout

| Phase | Scope | Changes to production? |
|---|---|---|
| **A. Offline validation** | Run steps 1–3 as a script over existing pilot transcripts (export + consent), review output by hand, tune prompts, write TC-12 to TC-18 fixtures | None |
| **B. Build** | `stories` schema + migration, consolidation pass in `close_and_process_session`, **read-back and approval in session** (§5.4), deletion/export, approved-only family API, dashboard Story list/detail in life order, story-ready message | Yes. Read-back is a conversation change, so full TC-01 to TC-22 run. Self-trial before families |
| **C. Conversation** | Unfinished-stories block in Layer 3 + guardrails + TC-19 | Yes. Prompt change, so full TC-01 to TC-19 run |
| *Separate* | Inbound voice-note storage with consent, for real audio clips | Own proposal (consent + residency + cost) |

**Phase A is the decision gate.** If real pilot transcripts produce few Stories even with good consolidation, the problem is partly in the conversation, and Phase C moves ahead of B.

## 12. Risks

- **Words in their mouth.** Covered by §5.1, §5.3, the source trail and §5.4. This is the risk that matters most. A family that catches one invented detail will stop trusting all of it.
- **Translation flattens voice.** An English first-person body from a Tamil telling will lose texture. The labelling (§5.2) and original-language quotes reduce this but do not remove it.
- **Over-merging.** Two different trips to the same village merged into one. TC-13 covers this. The `retired` status and a merge/split admin tool are the way back.
- **Sensitive content.** People tell Katha things in conversation they may not want written up for their children. A Story makes those things more visible than a fragment did. Approval first (§5.4), including the `kept_private` outcome, is how this is handled: nothing is shared until the storyteller says so.
- **Cost and latency.** 1–3 extra Sonnet calls per session, post-session only. Measure in Phase A.
- **Approval friction.** Stories may pile up as `pending` if read-backs are skipped, and the family sees fewer of them, later. The daily quote card covers the gap. Track pending age as a metric. If it grows, the read-back design needs work, not the approval rule.

## 13. Decisions and remaining questions

### Decided (2026-10-11, Krish)

| # | Question | Decision |
|---|---|---|
| D1 | Minimum arc for a Story | **Setting + Event + Turn.** Resolution preferred, not required. Revisit with Phase A data |
| D2 | Card cadence | **Keep the daily quote card, and add a story-ready message.** One message per day at most |
| D3 | Show Threads to the family | **No.** Hidden until we have seen real Threads |
| D4 | Approval before the family sees a Story | **Yes, the storyteller approves first** (§5.4) |
| D5 | Family corrections | **No.** The Story is the parent's and remains so (§7.5) |

### Still open (raised by D4)

- **Q-A. No clear answer to a read-back.** How many times do we offer before we stop asking? Suggested default: offer in up to 3 sessions, then stop offering and leave it `pending`. A pending Story is never shown to the family by default, and the storyteller can still bring it up themselves.
- **Q-B. Reading it back in their language.** The body is English for MVP (§5.2), but the read-back must be in their language, so it means translating back. Do we accept that the family reads an English version of what the parent approved in Tamil? Or does approval need the two versions to be checked against each other? Suggested: accept it for MVP, and label it on the dashboard.
- **Q-C. Long Stories.** A long Story read in full may tire the listener. Do we read it in parts across sessions, or read a shortened version (which would mean approving something different from what the family sees)? Suggested: read it in full and keep Stories short by design.
