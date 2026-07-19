# Proposal: Detecting & Resurfacing "Unforgettable People"

**Status:** Draft for review — not yet implemented
**Author:** Krishnaraj CK
**Related:** `backend/prompts/system_prompt.py`, `backend/prompts/domains.py`, `docs/TECH_DESIGN.md` §2.1, §2.4, §2.5

---

## 1. The observation

Every elderly storyteller carries a small number of people who shaped them disproportionately — a teacher, a shopkeeper, an uncle, a friend who died young. These aren't necessarily family (already covered by the Family & Ancestors domain) and they don't map to any single life domain — they can surface while talking about childhood, career, or a historical event, and the emotional weight of *how* they're described (repetition, a change in tone, a long pause implied in the transcript) is often the signal, not just the fact that a name was said.

Right now, Katha has no mechanism to notice this and come back to it. That's the gap this proposal closes.

## 2. What exists today, and why it falls short

Katha already has a question **per domain** that gestures at influence:

| Domain | Question | File |
|---|---|---|
| Childhood & Home | "Who were the people you remember most from your childhood?" | `domains.py:23` |
| Family & Ancestors | "Who in your family had the biggest influence on you, and why?" | `domains.py:39` |
| Career & Work Life | "Tell me about a colleague who made a real impression on you." | `domains.py:72` |
| Wisdom & Life Lessons | "Is there a person whose wisdom shaped you most?" | `domains.py:159` |

These are useful prompts, but they're **domain-locked, one-shot, and passive**. If someone significant comes up unprompted while discussing, say, the Historical Events domain, there's no question there to catch it, and nothing carries it forward to a later session.

The extraction layer (Layer 5, `system_prompt.py:141-150`) captures a flat `named_entities` object. It doesn't distinguish "the shopkeeper my father bought sugar from" from "the teacher who changed the direction of my life" — both would land in the same undifferentiated bucket, with no weight attached.

Layer 3 (`system_prompt.py:82-112`) does have an `open_threads` mechanism designed exactly for carrying things forward across sessions — and `PriorContext.facts` already stores structured people-data per TECH_DESIGN §2.5's fact-store format (`{person_name, relationship, first_mentioned}`). But nothing populates either of these specifically for emotionally significant people. TC-03 in the eval set (TECH_DESIGN §3.3) tests that Katha recalls a person ("sister Kamala") proactively — proving the recall *plumbing* works — but it's testing generic fact recall, not detecting or prioritizing significance.

So the pieces are there. They're just not wired for this specific pattern.

## 3. Proposed mechanism

### 3.1 Extraction (Layer 5) — add a `significant_people` field

Alongside the existing `named_entities`, add a dedicated field so significance is tagged at the point of extraction, not inferred later:

```json
{
  "story_atoms": [],
  "named_entities": {},
  "significant_people": [
    {
      "name": "Mr. Iyer",
      "relationship": "school teacher",
      "why_significant": "Encouraged the user to pursue teaching despite family pressure to join the family business",
      "signal": "user's tone shifted; described him unprompted across two separate answers"
    }
  ],
  "themes": [],
  "energy_signal": "high|medium|low",
  "gaps_remaining": [],
  "session_end_suggested": false
}
```

`signal` is a short free-text field capturing *why* the model flagged this person — repetition, unprompted volunteering, described with unusual detail, explicit language like "changed my life." This gives you an auditable reason rather than a black-box tag, and lets you tune precision later if it over-fires.

### 3.2 Therapeutic protocol (Layer 2) — new principle

Add a sixth principle alongside the existing five (`system_prompt.py:57-79`):

> **6. Follow unforgettable people, wherever they appear.** If the user describes someone — in any domain — with unusual warmth, repetition, or emotional weight, do not treat it as incidental to the current topic. Gently go deeper in the moment ("What was it about him that stayed with you?"), and flag them for a future session if the conversation moves on before it's fully explored.

This is what turns the domain-locked questions in section 2 from "the only chance to ask" into "one of several chances to ask" — the model is now watching for the *pattern*, not just answering a scripted prompt at the right moment.

### 3.3 Life context (Layer 3) — a dedicated surface

Currently `PriorContext` (`system_prompt.py:37-40`) has `facts`, `recent_stories`, `open_threads`. Add a fourth field, `significant_people: list[dict]`, populated from the extraction step above. Give it its own block in Layer 3 rather than burying it inside the generic facts dump:

```
People who have mattered deeply to {name}, mentioned in past sessions:
  - Mr. Iyer (school teacher) — encouraged him toward teaching. Not yet fully explored.
```

This mirrors the existing "explicit callback instruction" pattern already described in TECH_DESIGN §2.5 ("Reference [specific prior content] early in today's session") — same idea, just given a dedicated, higher-priority slot instead of competing with every other fact for the model's attention.

### 3.4 Closing the loop across sessions

A person should stop being resurfaced once they've been genuinely explored (not just re-mentioned). Simplest approach: once a `significant_people` entry accumulates a story atom with `completeness_score >= 3` (per TECH_DESIGN §2.4's existing scoring) that's about *them specifically*, mark it resolved and stop injecting it into Layer 3. This reuses the completeness scoring you already have rather than inventing a new one.

## 4. Evaluation

Add one test case to the TC-01–TC-10 regression set (TECH_DESIGN §3.3 format):

**TC-11: Unforgettable Person Detection & Resurfacing**
Setup: In Session 2, user says: "There was a teacher, Mr. Iyer — I still think about what he told me, some days more than others." No direct follow-up occurs before the domain moves on.
Input (Session 6, unrelated domain): user is discussing career.
Expected: Katha proactively asks about Mr. Iyer at an appropriate point, distinct from a generic fact callback (i.e., asks *why he mattered*, not just "how is Mr. Iyer?").
Pass: `significant_people` populated in Session 2 extraction with non-empty `why_significant`; Session 6 response references Mr. Iyer with a question aimed at unexplored emotional significance.

This complements rather than duplicates TC-03, which only tests that a fact is recalled — TC-11 tests that *significance* is recognized and actively pursued.

## 5. Open questions / risks

- **Over-tagging risk.** If the model tags every named person as "significant," this just becomes a noisier `named_entities`. The `signal` field (3.1) is meant to keep this honest and inspectable — worth checking real transcripts before assuming precision will be fine.
- **Annoyance risk.** Resurfacing someone across sessions could feel repetitive rather than attentive if timed badly. Suggest capping resurfacing to once per unresolved person per session, and only when it fits naturally (not forced at the top of a session).
- **Scope.** This is a Layer 2/3/5 prompt change plus one new `PriorContext` field — no new infra, no schema migration beyond adding a JSON field to the existing extraction output and fact store. Should be a small, self-contained change relative to the rest of the system.

## 6. Suggested next step

Implement as a single feature branch (`feature/unforgettable-people`) touching `system_prompt.py`, `PriorContext`, and the extraction schema, plus TC-11 in the eval set — per `.claude/rules/testing.md`, TC-11 should be run alongside the existing TC-01–TC-10 regression set before merging, not just on its own.
