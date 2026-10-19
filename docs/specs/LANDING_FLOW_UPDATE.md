# Landing page: update to the current product flow

**Status:** Draft for Krish, 2026-10-19. §2 has four decisions to settle before Claude Code starts. Copy below is draft and must be marked `<!-- COPY: draft -->` as in the original brief.
**Owner:** Krish · **For:** Claude Code
**Branch:** `feature/landing-flow-update`, one PR. Never commit to `main`; see `.claude/rules/git.md`.
**Files:** `coming-soon/index.html`, `coming-soon/main.js`, `coming-soon/styles.css`, `coming-soon/art/*.svg`, then `node coming-soon/build.mjs`. Also update `docs/LANDING_BRIEF.md` so the brief matches the page (§8).
**Background:** `docs/LANDING_BRIEF.md` (the original brief; everything not changed here still applies), `docs/specs/CONVERSATION_CARDS.md`, `docs/specs/COST_TO_SERVE.md` §9 (C5: the parent initiates), `frontend/app/privacy/page.tsx`.

---

## 1. Why

The page (merged from `feature/landing-story-page`) describes the flow as it stood on 15 Oct. Five things have changed since, and two lines on the page were never true:

| # | The page says | What is true now | Source |
|---|---|---|---|
| 1 | "Best time to talk: 10:30 am, daily" and "10:30 the next morning, her phone buzzes": Katha messages first | **She starts every conversation.** Katha does not message her first (Meta rejects it outside the 24-hour window) | COST_TO_SERVE C5; `SCHEDULED_OPENER_ENABLED=false` |
| 2 | "A quote card reaches your WhatsApp a few minutes after she finishes" | **Up to two "Did you know?" cards a week**, sent on WhatsApp and email a few hours before the child usually calls, **only after she hears each one and says yes** | CONVERSATION_CARDS §3, §6, §8 |
| 3 | "Katha writes a short story… She reads it first" | First-person Stories are **not built yet**. What she approves today is a card. She doesn't read it; **Katha reads it to her**, in her language | first-person-stories.md (Phase B pending); CONVERSATION_CARDS §6 |
| 4 | Nothing about the child's side after the card | The child can **reply with a question**, which Katha asks her next time, crediting the child | CONVERSATION_CARDS §10 |
| 5 | Nothing about the end of a conversation | Katha **tells her what it kept** at the end of each conversation | CONVERSATION_CARDS §5.3 |
| 6 | "in their own voice" (title, hero) and a bookend where "her voice plays" years later | **Her voice is not kept.** Voice notes are transcribed and the audio discarded; the privacy page says so | `privacy/page.tsx`; CONVERSATION_CARDS §2 "Out" |
| 7 | "Nothing reaches the family until she says yes" | True for cards. **Not true for the dashboard:** it shows every non-private story summary and quote, unapproved. The privacy page says summaries are shared via the dashboard | `api/routes/family.py` `list_stories`; privacy page "How it's used" |

Rows 6 and 7 are honesty problems, not just stale copy. Row 7 needs a product decision (§2, D1).

## 2. Decisions for Krish

| # | Decision | Proposed |
|---|---|---|
| D1 | The dashboard shows unapproved story summaries, which contradicts "nothing reaches the family until she says yes" | **Product fix: the dashboard shows only what she approved** (sent cards, and later approved Stories). Story summaries are hidden from the family. A small backend + dashboard change, specced separately. Until it ships, the page uses the weaker, true line in §4 beat 9 (variant B) |
| D2 | Her voice | **Change the copy to "her words"** now. Keeping her recorded voice (with her consent) is a separate product decision, which would also change the privacy page. If you decide to build it, restore the voice bookend then |
| D3 | When this ships | **When the conversation-cards PR and the C5 change (cost-to-serve) are both live in production.** The page is a pilot-recruitment page, so it describes what a family signing up now will get |
| D4 | Human review of cards | **Disclose it.** During the pilot you read every card before Katha reads it to her. That is a person reading her stories, so the page (and the privacy page) should say so plainly. Proposed line in §4 beat 9 |

Optional:
- **D5:** add one optional form field, "When do you usually call your parent?" It tells you whether the call ritual exists, and you need it at onboarding anyway. Proposed: yes.

## 3. What stays the same

- Act I beats 1–3 (the hero line, "You call on Sundays", "What fades") and their art and motion.
- Act III beat 6 ("She talks. Katha remembers."), its mock and its art.
- Act V's eight chapters and shelf (but see D1 for its sub-line).
- The form endpoint, the fields (apart from D5), the button and the note under it.
- The colour arc, the thread, the three styles, the character sheet, accessibility and performance rules.

## 4. Changes, beat by beat

Beat numbers follow `LANDING_BRIEF.md` §4. Every new or changed line gets `<!-- COPY: draft -->`. Every name, quote and card gets the *Illustrative* label.

### Act I

**Hero sub-line** (and `<title>`, `og:title`):
- Was: *Katha keeps your parent's stories, in their own voice.*
- Now: *Katha keeps your parent's stories, in their own words.*

**Beat 4, Katha introduced:**
- Was: *A warm voice on WhatsApp that talks with your parent most days, in their language — and keeps every story they tell.*
- Now: *A warm voice on WhatsApp that listens whenever your parent feels like talking, in their language — and keeps every story they tell.*

### Act II — The beginning

**Beat 3, setup.**
- Text now: *Her name, her language, when you usually call her, and a few hints to start from, like* Amma grew up in Madurai.
- Setup mock fields, in order: *Her name: Amma* · *Her language: Tamil* · *You usually call her: Sundays, 11 am* · *A few hints: Amma grew up in Madurai / Lived in Bombay in the 70s*. Remove "Best time to talk".
- Mock button: *Send her the link* (was "Ask her if she'd like to try").

**Beat 4, She decides.** Keep the three-language "yes" art. Text now:
> *You send her a link. When she taps it, Katha introduces itself — honestly, as an AI — and asks if she'd like to talk, in her language.*
> *She says yes in her own words.*
> *If she doesn't, Katha stays silent.*

**Beat 5, the first conversation.**
- Was: *10:30 the next morning, her phone buzzes.* / *It's a voice, not an app.*
- Now: *The next morning, she presses the mic and starts talking.* / *It's a voice, not an app. She talks when she feels like it, and Katha always answers.*
- **Phone mock: reverse the order.** Her voice note first (`2:41 · 10:30`), then Katha's reply (`0:24 · 10:33`), then "listening…". Katha's first bubble must not come before hers.

### Act III — The days

**Beat 6:** unchanged, plus one line after the existing text:
> *And before she goes, Katha tells her what it has kept safe.*

**Beat 7 (quote card): remove from Act III.** The card now appears only after she has approved it, in Act IV (beat 10). The card-flies-to-the-child motion moves with it.

### Act IV — Her story, her say

**Beat 8, rewritten: from her words to a card.**
- Heading: *Something worth sharing takes shape.*
- Text: *From what she's told Katha, it picks one thing you might not know, in her words, translated into English.*
- Motion: keep the fragments (*"the river house…"*, *"seven of us children"*, *"Appa's cycle bell"*…). They now gather into a **card**, not a story page.
- The card (illustrative, drawn to match the real card layout in `memory_cards/generator.py` / CONVERSATION_CARDS §7.5):
  - Header: *Did you know?*
  - Hook: *Did you know Amma could hear Appa's cycle bell long before she saw him?*
  - Excerpt: *"From the mango tree by the gate, you could hear Appa's cycle bell long before you saw him."*
  - Box, headed *On your next call*: *Ask her where Appa was cycling home from.*
  - Footer: *Shared with Amma's permission · translated from Tamil*
- Remove the first-person story page ("The river house… As told to Katha"). Stories aren't built yet.

**Beat 9, full-screen moment.** Two variants; D1 picks which ships.

*Variant A (once the dashboard shows only approved content):*
> *Katha reads it to her first, in her language.*
> *Nothing reaches you until she says yes.*
> *Your family can't change a word.*
> *It's her story. It stays hers.*

*Variant B (until then):*
> *Katha reads it to her first, in her language.*
> *No card is sent to you until she says yes.*
> *Your family can't change a word.*
> *It's her story. It stays hers.*

- "And no one edits it" becomes "Your family can't change a word". During the pilot, the operator can edit a draft before she hears it, so "no one edits it" isn't true.
- **D4 line**, small, under the moment: *During the pilot, a person on our team checks each card before Katha reads it to her.*
- Art: keep her glasses. Add a small voice-note bubble playing beside them, to show it is read *to* her.

**Beat 10, new: the card reaches you.**
- Heading: *Then it reaches you.*
- Text: *On WhatsApp and by email, a few hours before you usually call. Up to twice a week.*
- Motion: the card lifts from her side of the thread and lands in the child's phone (the former beat 7 motion). Show an email envelope as a second, smaller landing.

**Beat 11, new: the call.**
- Heading: *You call. You ask.*
- Text: *Or reply with a question of your own. Katha will ask her next time, and tell her it came from you.*
- Small line: *If she'd like to share her answer, it comes back to you the same way — once she's said yes.*
- Art: the child at the city-flat window, phone at the ear (paper-cut), the thread running into the phone.
- **Do not** promise an answer, a time frame, or that Katha will "get" the answer.

### Act V — The archive

**Beat 10 (now 12), chapters.** Keep the shelf and labels.
- Variant A sub-line: *Everything she agrees to share is kept for your family, chapter by chapter of her life.*
- Variant B: no sub-line.

**Beat 11 (now 13), bookend.**
- Was: the grandchild presses play and her voice plays; *Their voice. Their words. Yours to keep.*
- Now: the grandchild reads a card on a phone (or a printed page) with headphones around the neck, not on. Line: *Her words. Her stories. Yours to keep.*
- Keep the silence-then-line timing.

### Act VI — Invitation

**Trust strip.** Keep the four statements. Add two, so six in all (3 × 2 on desktop, 2 columns on mobile):
- *She hears every card before you do.*
- *She can keep anything just between her and Katha.*

**Form (D5).** Add an optional field after "Where you live, relative to them": label *When do you usually call your parent? (optional)*, placeholder *e.g. Sundays around 11*.

### Meta and assets

- `<title>` and `og:title`: *Katha · Your parent's stories, in their own words*
- `description`: *Katha listens to your parent on WhatsApp, in their own language, and shares the stories they choose with you. We're inviting a small number of families to try it with us.*
- Check whether `og.png` contains the words "in their own voice". If it does, re-render it from the hero art with the new line. Keep `og:image:alt` matching.
- **"Hear Katha" sample:** if Krish supplies `audio/katha-sample.mp3`, it must be in the voice new families hear (Gemini, the default persona voice per `media/tts.py`), not Bulbul. The button stays hidden until the file exists.

## 5. Honesty rules: additions to LANDING_BRIEF.md §6

Add these to the brief's non-negotiables, and check the page against all of them:

1. Never say Katha calls, rings, buzzes, reminds or messages her first.
2. Never say her voice is kept, played or heard later. Use "her words".
3. Card frequency is **"up to twice a week"**. Never "every day" or "after every conversation".
4. Never say the card is in her language. It is English, labelled as translated. Only the read-back is in her language.
5. Never promise the child an answer to their question.
6. Don't say "no one edits it". Say the family can't change it.
7. "Nothing reaches you until she says yes" only once D1 is live. Before that, "No card is sent to you until she says yes".
8. Don't describe first-person Stories or a book as something families get now.

## 6. Out of scope, but found while checking

These sit outside the landing page. Listed so they aren't lost:

- **Onboarding still asks for "session time"** (`frontend/app/family/onboarding/page.tsx`), though Katha no longer messages first, and it doesn't ask the card fields (child's name, what they call her, call slots). The conversation-cards build sets those with an operator script.
- **The privacy page doesn't mention human review of cards** (D4). It should.
- **The parent's welcome message says "I would record our conversations"** (`core/parent_consent.py`), but the audio isn't kept. "I would keep what you tell me" is closer. Changing it bumps `PARENT_CONSENT_VERSION`.
- **D1 itself** (dashboard shows approved content only) needs its own small spec.

## 7. Done means

- [ ] Every row in §1 is fixed on the page, and every rule in §5 holds (search the page for "voice", "buzz", "10:30", "daily", "story" and "edits").
- [ ] Beat 9 ships as the variant D1 picks.
- [ ] New beats 10 and 11 are built in the agreed styles. The card-to-phone motion runs after the approval moment, not before.
- [ ] The phone mock in beat 5 shows her message first.
- [ ] `node coming-soon/build.mjs --check` passes.
- [ ] Playwright screenshots at 390×844 and 1440×900 for Acts II, IV and V, plus a reduced-motion run, attached to the PR.
- [ ] Lighthouse mobile still Performance ≥ 85, Accessibility ≥ 95.
- [ ] The form still submits to Formspree (one real test). The D5 field arrives in the submission.
- [ ] `docs/LANDING_BRIEF.md` updated to match: §4 beats, §5 form, §6 rules, §9 item 3 marked done.
- [ ] PR description says which D1 variant shipped, and that the PR must not merge before the D3 gate.

## 8. Order of work

1. Copy and meta changes (§4 text, §4 Meta), and swap the beat 5 mock order. These are the cheapest and fix the honesty problems.
2. Act IV restructure: beat 8 card, beat 9 variant, move the card motion to beat 10.
3. New art: beat 11 call scene, beat 13 bookend, beat 9 voice bubble.
4. Trust strip and form field.
5. `LANDING_BRIEF.md` update, build, screenshots, Lighthouse.
