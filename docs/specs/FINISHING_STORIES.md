# Help her finish the stories she starts: plan

**Status:** Decided 2026-11-08: D1–D4 take the suggested answers. Steps 1 and 2 done; step 3 (Krish's listening review) next.
**Owner:** Krish · **Written:** 2026-11-08
**Branch:** `feature/finishing-stories`, one PR. Never commit to `main`; see `.claude/rules/git.md`.
**Touches:** `backend/prompts/system_prompt.py` (Layer 2 principle 8, the extraction prompt's `open_threads`), `backend/evals/run_eval.py`, `backend/tests/test_system_prompt.py`, `docs/TECH_DESIGN.md` §2.3 and §3.3.
**Background:** `docs/proposals/first-person-stories.md` §4 (the arc test), §9 (Phase C) and §11 (Phase A is the decision gate).

---

## 1. Why

Phase A of the first-person stories proposal ran on 2026-11-07, over one pilot parent's 11 conversations (Malayalam, with consent). The consolidation pass found **14 incidents and only 3 complete stories**. Of the 9 open threads in the report, **8 had no turn** (the moment that mattered) and 7 no resolution. She had said where and when, and what happened. Nobody asked her what happened next.

Phase A was set up to answer exactly this. §11: if real transcripts produce few Stories even with good consolidation, the problem is partly in the conversation, and the conversation change comes before the Phase B build. It does.

The cause is in our own prompt. Layer 2 principle 8 tells Katha that when an answer leaves something out, it should ask for the missing **who, what, when, where or why**, by name ("What was her name?", "Roughly what year was that?"). Nothing asks **what happened then** or **how it ended**. So Katha does what it is told: a name, then a year, then a smell, and the story never reaches its turn. TC-08 (follow-ups target missing W's) and the 5W `completeness_score` both pull the same way.

Five W's describe a fact. An arc describes an account. The family is waiting for accounts.

## 2. What changes

Two parts were planned, both prompt-only; only 2.1 is built (see 2.2). No schema, no new LLM call.

### 2.1 In the moment: ask for the arc while she is telling it (Layer 2)

Principle 8 is rewritten to tell two kinds of answer apart:

- **An incident**: something happened once ("the day the water came into the house", "when my father didn't come home"). Follow the arc. If she has given the setting and what happened but not the turn, ask for it: "What happened then?", "What did you do?", "What did your mother say?". Once there is a turn, ask how it ended or what it meant to her: "How did it all end?", "Did things change after that?". Ask for a name or a year only when the story cannot go on without it.
- **A portrait or a description**: a person, a place, a routine ("there was a man who made the best sweets", "our kitchen"). Keep today's behaviour: ask for one missing W at a time, a different one each turn.

The draft wording, for review:

> 8. Help a story reach its end. When the user is telling an incident, something that happened once, follow it forward: if you have heard where it was and what happened but not the moment that mattered, ask what happened then, or what they did; once you have that, ask how it ended or what it meant to them. Ask for a name or a year only when the story can't go on without it. When they are describing a person, a place or a routine instead, ask for one missing piece of who, what, when, where or why by name, a different one each turn. Either way, check your own last question in the conversation above, and never ask for the same thing again in different words.

Unchanged around it: one question per reply (Layer 5), never re-asking a question passed over, principle 4 (mood first), the grief handling of TC-10, the crisis protocol, set-aside topics, and the card read-back and sharing offers.

### 2.2 Across sessions: name the missing part in the open thread (extraction)

**Dropped 2026-11-08 (Krish).** The baseline showed the extractor already does this: all six TC-29 samples left an open thread naming the missing turn. Only 2.1 changes; TC-29 stays, as the check that it keeps doing so.

When she moves on before an incident ends, the arc is lost unless the next session knows what is missing. Today each atom's `open_threads` are free-text hints ("the name of her school"). Layer 3 already shows up to 12 of them, and principle 7 already tells Katha to raise them when today's topic gives an opening.

The extraction prompt gains one rule: for an atom that describes an incident left without its turn or ending, one of its `open_threads` names the missing part as a question about that incident, e.g. "What happened after the water came into the house?". This uses the existing path (atom → `_extract_open_threads` → Layer 3 → principle 7) and needs no new storage.

### 2.2a Acknowledge, don't retell (Layer 5; added 2026-11-09)

Krish's Malayalam listening review: replies opened by retelling the parent's last answer (44–74% of the words before the question were hers), and "ഞാൻ മനസ്സിൽ കാണുന്നുണ്ട്" ("I can picture it") opened every Malayalam TC-27 reply. The cause was the October line "Often the best start is the detail they just gave you". Layer 5 now says: don't retell; respond to how it must have felt or what it shows about someone, or go straight to the question; the examples show the kind, not the words; never the same acknowledgement twice in a session. The first examples Krish chose (a night, a father) were copied word for word wherever the story fitted them, so they were swapped for "അത് വലിയൊരു ധൈര്യമായിരുന്നു." ("that took real courage"), "അവർക്ക് നിങ്ങളെ അത്ര ഇഷ്ടമായിരുന്നു, അല്ലേ." ("they must have loved you very much") and "എന്നിട്ട്?" ("and then?"). TC-30 checks a five-turn session for a repeated acknowledgement.

### 2.2b Don't close in the middle of a story (Layer 4 and `session_manager`; added 2026-11-09)

TC-30 found the biggest single cause of unfinished stories. One incident told over several turns makes an atom a turn, so the domain's atom goal (3 for childhood) was met halfway, Layer 4 told the next reply to close, and `should_end_session` closed the session one turn later whatever Katha said. Katha put the ending off: "tomorrow I'd love to hear what happened when he finally reached you", and anything she said after counted as a second conversation. `main` did the same.

Now, with the goal met, Layer 4 says not to close while they are in the middle of telling something, and to close on the reply after it ends. The extraction reports that close as `session_end_suggested`, which ends the session; `goal_met` alone ends it only at `GOAL_MET_EXCHANGE_CEILING` (10 exchanges; the design target is 6–8). `ended_reason` stays `goal_met`. TC-31 checks it.

### 2.3 Not in this change: the "unfinished stories" block and its ask-limit

Proposal §9 wants a separate Layer 3 block of at most two unfinished stories, raised at most once a session, and dropped after two declined asks. Counting asks per incident needs the incident stored, which is the `stories` table of Phase B (a Thread is a `stories` row with `status = thread`). It ships with Phase B. Until then, the existing rule that Katha never re-asks a question already passed over in the conversation holds within a session, and an open thread stays one hint among twelve.

## 3. Guardrails

- **Mood and grief first.** If the incident is a loss, principle 4 and the TC-10 handling win: acknowledge it, offer to stay or move on, never press for "how did it end". A death has no "resolution" to extract.
- **Never interrupt one story to finish another.** If she has moved on, follow her. Only an open thread from an earlier session may bring an old incident back, and only at a natural opening (principle 7).
- **The remembered-person anchor goes first** (`_pick_recall_anchor`). If both exist, the person is raised first, and the two are often the same story.
- **Set-aside topics stay set aside** (PR #61). An open thread about a set-aside person or topic is never raised.
- **Not an interview.** Still one question per reply, and two to four sentences.

## 4. Evaluation

Baseline first, as in `OFF_TOPIC_SCOPE.md`: write the new cases, run them on the current prompt, and confirm they fail before changing it. If the baseline already passes, stop and look again at the Phase A transcripts.

TC-19 to TC-25 are taken, so the new cases start at TC-26. Each runs in English and Malayalam, three samples each (the TC-23 pattern).

| ID | Case | Pass |
|---|---|---|
| **TC-26** | An incident told with setting and event, no turn ("We were living by the river. One night the water came into the house.") | The next reply asks what happened then / what they did, not a name, year or smell |
| **TC-27** | The same incident, now with a turn ("My father carried my grandmother to the temple steps.") | The next reply asks how it ended or what it meant, or moves on if the story is clearly closed; it does not ask what happened next again |
| **TC-28** | A loss told as an incident ("My elder brother went to Madras for work. One evening a telegram came: there had been an accident.") | TC-10's rubric: validates, does not ask how it ended or what happened next, offers to stay or move on |
| **TC-29** | Extraction on TC-26's exchange | The atom's `open_threads` include one naming the missing turn of that incident |
| **TC-08** *(existing)* | A portrait: "There was a man in our neighbourhood who used to make the best sweets." | Must still pass: a portrait still gets W follow-ups |

The judgement in TC-26 to TC-28 is a reading of Katha's reply. Score them first with keyword lists in both languages, as TC-05 and TC-10 are, and show every failing reply verbatim. Krish decides any change to a criterion (`reference_eval_harness`); the open eval-audit item to validate keyword scorers against his labels applies here too.

**Baseline (2026-11-08, current prompt, 3 samples × en/ml, ₹9):** TC-26 **0/6**, TC-27 **0/6**, TC-28 6/6, TC-29 3/6 by the scorer. Every TC-26 reply asked what she saw, heard or felt, or who was there; every TC-27 reply asked a name, a place or a feeling. The three TC-29 fails do name the missing part ("How the family responded that night and where they went"), in words the scorer does not list, so the extractor already does what 2.2 asks for: 6/6 by hand.

Then the full regression: TC-01 to TC-11 at three samples, plus TC-19 to TC-25. Targets as always: 80%+ objective, 75%+ rubric.

**After (2026-11-08, ₹9 per run).** The first rewrite of principle 8 gave TC-26 2/6 and TC-27 3/6: Katha zoomed into the moment ("What do you remember about that moment, when you first saw the water?", "How did that night begin?"). Principle 8 now adds "not what that moment looked, sounded or felt like, and not how it began". With three scorer widenings Krish approved (en "what you did"; ml "കടന്നുപോയി", "കഴിച്ചുകൂട്ടി"; "വയസ്സുകാരി", a ten-year-old, no longer counts as asking her age), the result is TC-26 **5/6**, TC-27 **5/6**, TC-28 6/6, TC-29 6/6. The fails: TC-27 "Once you all reached the temple steps, what did you do there?" (asks for the next step again) and a TC-26 Malayalam reply that asks for the turn as a statement, with no question mark. Note that TC-27's end list includes "what happened", so "Once you reached the temple steps, what happened next?" passes; read strictly it would be 3/6.

**Regression on the new prompt:** objective 71/72, rubric 37/48 (77%). Two cases were checked against `main` (ec359c5), because they could plausibly move:

- TC-08 (a portrait, D4): 8/9 on the branch, 8/9 on `main`.
- TC-20 (bridge rubric): 9/18 by the scorer on the branch, 14/18 on `main`. Read by hand (is the memory question about money?), both are 9/18. The scorer passes "what did the house you grew up in look like?" on "working years" and fails "a small hoard of coins… where did you keep it?". The tax replies jump to the house or the school on both. An existing weakness, not this change.

**After 2.2a and 2.2b (2026-11-10, TC-26 to TC-31 ₹15, regression about ₹40).** TC-26 en 0/3, ml 3/3; TC-27 6/6; TC-28 5/6 (the fail validates and offers to pause, in words the list lacks: "ഇത് പറയാൻ എളുപ്പമല്ല…"); TC-29 6/6; TC-30 ml 3/3, en 0/3 ("must have been", "must have felt" twice in a session; `main` 2/6, its Malayalam repeating "കേൾക്കാൻ എനിക്ക് വലിയ ആഗ്രഹമുണ്ട്"); TC-31 **6/6** (goal met at turn 3 every time, no close before turn 5). Regression: objective 68/72, rubric 40/48 (83%); TC-09 3/3. The fails are known ones: an empty extraction on TC-06, TC-08's one-W follow-up, the two-question slip on TC-19 and TC-20, and TC-20's bridge scorer. English TC-26 still zooms into the moment ("Do you remember where you were in the house when you first noticed the water?"), 3 of the last 6 by hand.

## 5. Did it work

The eval cases show the prompt does what it says. Whether she now finishes more stories only shows up in real conversations. After about two weeks of sessions, rerun the Phase A script (`scripts/consolidate_stories.py`, offline against a restore, read-only) over the **new sessions only**, for the same parent, and compare:

| Measure | Phase A baseline (2026-11-07) | Target |
|---|---|---|
| Incidents that reach a complete arc | 3 of 14 (21%) | **D1** |
| Open threads missing the turn | 8 of 9 | Falling |
| Complete stories per conversation with transcripts | 3 / 11 (0.27) | Rising |

One parent is a small sample, and the next two weeks may simply be different topics. Treat it as a direction, not a verdict, and repeat it as more families join.

A side effect to expect: atoms of incidents may score lower on 5W `completeness_score`, since Katha asks fewer names and years. That score stays an extraction-quality signal; the arc is the bar for what the family sees (proposal §4).

## 6. Cost

No new calls. Principle 8 grows by about 400 characters inside Layer 2, which is static and sits in the cached prefix of the dialogue prompt; the extraction rule adds about 250 characters to an uncached Haiku prompt. Both are rounding error next to a turn's cost.

## 7. Rollout

1. Write TC-26 to TC-29 and the unit tests for the prompt text. Run the baseline on the current prompt.
2. Change principle 8 and the extraction rule. Run TC-26 to TC-29 and the full regression.
3. Krish reviews the failing replies, and at least five passing Malayalam replies, read aloud (does "what happened then?" sound natural, not abrupt?).
4. Merge, deploy. Nothing changes for the family; the parent hears a different follow-up question.
5. After two weeks, §5.

## 8. Decisions for Krish

Decided 2026-11-08 (Krish): all four as suggested.

| # | Question | Decided |
|---|---|---|
| D1 | The target for incidents reaching a complete arc | 40%, about double the baseline. Revisit after the first rerun |
| D2 | Ship 2.2 (open threads naming the missing part) now, or wait for Phase B's stored threads and ask-limit | Now. It reuses the existing path, and the risk of an old incident being raised too often is capped by the 12-thread list and the no-repeat rule |
| D3 | When she clearly ends an incident ("and that was that"), should Katha still ask what it meant to her? | No. Move on, or ask one reflective question only if she sounds open to it |
| D4 | Should TC-08's pass criteria change? | No. TC-08 is a portrait; it keeps its 5W targets, and becomes the check that 2.1 did not break portraits |
