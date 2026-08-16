# Katha eval harness

Executable form of the regression set in [`docs/TECH_DESIGN.md`](../../docs/TECH_DESIGN.md) §3.3 — TC-01 through TC-11.

`.claude/rules/testing.md` requires this to be run on every prompt change, with targets of **80%+ on objective cases** and **75%+ on rubric cases**.

## Running it

Needs a reachable Postgres and a real `ANTHROPIC_API_KEY` (from `backend/.env`). It makes live, billed API calls — that is the point; see below.

```bash
docker compose up -d db
cd backend
DATABASE_URL="postgresql+asyncpg://katha:katha@localhost:5432/katha" \
  ./.venv/bin/python evals/run_eval.py           # all 11 cases
DATABASE_URL="..." ./.venv/bin/python evals/run_eval.py tc03 tc11   # selected
```

Results land in `evals/results/<timestamp>.json`, timestamped and never overwritten. Partial runs get a `-partial-` suffix, because a partial re-run once clobbered a full run's results file and the file on disk then silently disagreed with the run it appeared to describe.

## Why live calls, not mocks

Five P0-class defects in this codebase were invisible to a fully green mocked test suite, because every mock supplied a correctly-shaped payload the model does not actually produce:

- `story_atoms` had no per-item schema, so the LLM returned strings; extraction crashed and was swallowed.
- `entity_extractor` called `json.loads` on a reply Claude wraps in a ```json fence, so **the structured fact store was empty for every user since day one**.
- `check_post_turn` demanded a `<response>` wrapper the model omits on roughly half of short low-engagement turns, discarding good replies.
- Sessions closed the instant `goal_met` flipped, making a closing-exchange instruction dead code.
- `mark_resolved` fired in the same extraction pass that flagged a person, so nobody was ever resurfaced.

Each was found by running the real path. None could have been found by a mock.

## The one rule for this file

**Every criterion must correspond to something §3.3 actually states.**

This is not pedantry. The harness once failed TC-04 on a keyword list of temporal words (`after`, `years later`, `looking back`) when §3.3 asks only that a response "introduces new angle" — so a model asking about the house, the street, and the father's role scored zero for choosing spatial angles over temporal ones. It failed TC-05 on a `≤60 words` cap that appears nowhere in the spec, while the criterion §3.3 *does* state — `session_end_suggested` within 3 exchanges — passed.

Both produced good conversations and a failing grade. An eval that fails on invented criteria cannot gate anything, because you cannot tell a real regression from a scoring artifact.

So: when a case fails, first check the criterion against §3.3. If they disagree, the harness is wrong. When adding or changing a criterion, quote the §3.3 line it implements in a comment beside it.

The corollary — **do not loosen a criterion to make a run pass.** Change it only to match the spec, and when you do, re-run before and after so you can see which cases the measurement change moved.

## Known limitations

- **STT and TTS are bypassed.** Transcripts are fed directly. This touches nothing in the memory or extraction pipeline, but it means TC-06 measures extraction on code-mixed *text*, not Sarvam's transcription of code-mixed *audio* — its pass is narrower than it appears, and the real gap there is a Sarvam accuracy matter.
- **Filler sessions insert `StoryAtom` rows directly** to advance the domain sequence so a later session lands on the domain a case needs. Anything actually under judgement goes through the real prompt-building code and a real API call.
- **Rubric cases are LLM-sampled and vary run to run.** TC-04 has been observed FAIL / PASS / FAIL across three samples with byte-identical Layer 3 content. Before reporting a rubric change as a regression, re-run the case and check whether the input to the model actually changed.
