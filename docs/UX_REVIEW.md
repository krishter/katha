# Katha — UX Review & Wireframe Specification

**Author:** UX architecture review, pre-pilot
**Date:** August 2026
**Status:** For review and sign-off
**Companion artifact:** [`docs/wireframes/katha-wireframes.html`](./wireframes/katha-wireframes.html) — clickable prototype
**Reviewed against:** `frontend/app/**`, `backend/api/routes/**`, `docs/PRD.md`, `docs/PLAN.md`

---

## 0. How to use this document

This is both an audit and an implementation spec.

- **Section 1–2** is the honest assessment of what exists today. Read this first.
- **Section 3–4** defines who we are designing for and what they actually do.
- **Section 5** is the screen inventory — every screen, its priority, and its acceptance criteria. This is the part Claude Code should read when implementing.
- **Section 6** is the design system.
- **Section 7** is the recommended build sequence.

Priorities used throughout:

| Tag | Meaning |
|---|---|
| **P0** | Must ship for the pilot to succeed. Either the pilot cannot run without it, or shipping without it breaks a promise we've already made. |
| **P1** | Fast follow — ship during the pilot, weeks 2–6. |
| **P2** | Post-pilot. Wireframed so the architecture doesn't preclude it. |

---

## 1. Executive assessment

Katha's backend is considerably more mature than its front end. The conversation engine, extraction pipeline, memory model, and consent scaffolding are real. The portal is a thin read-only viewer that was built to prove the data exists — and it does that well.

But the portal is not yet a product. Today it is **five read-only pages behind a magic link**. It has no way in from the outside world, no way to change anything once set up, no way to tell whether the thing you paid for is actually working, and no way to pay.

The most important framing for this review:

> **The elderly parent never opens the portal. So every single thing that shapes their daily experience — what time Katha calls, in what language, what it asks about, whether it pauses when they're in hospital — is configured by someone else, once, during a five-minute wizard, and can never be changed again.**

That is the central UX failure. It isn't a missing page; it's a missing model. The portal is currently designed as an *archive viewer* when it needs to be a **remote control plus a reassurance instrument plus an archive viewer**, in that order of daily importance.

The archive is what the buyer pays for. The reassurance is what makes them keep paying.

### The three-sentence version

1. There is no front door — `katha.life` redirects a first-time visitor straight to a login form with no explanation of what Katha is.
2. There is no settings page — so the schedule, language, pause, plan, and data deletion promised in the consent screen do not exist.
3. There is no signal of life — the dashboard shows lifetime counters but never answers the buyer's actual daily question, *"did Amma talk to Katha today, and is she okay?"*

---

## 2. Findings

Fifteen findings, ordered by severity. Severity is judged by pilot risk, not effort.

---

### F-01 · The consent screen promises a settings page that does not exist
**Severity: Critical — compliance** · **Where:** `frontend/app/family/onboarding/page.tsx:~250`

The DPDP consent checklist a user must tick includes, verbatim:

> "You can delete all data at any time from your account settings"

There is no account settings route in the application. There is no link to one anywhere in the navigation (`app/family/layout.tsx` offers Stories, Memory Cards, Logout). The `DELETE /user/{user_id}` endpoint exists in `backend/api/routes/admin.py` and is well-implemented, but is unreachable from the UI.

We are collecting legally-operative consent on the basis of a control we do not provide. Under the DPDP Act the right to erasure must be *exercisable*, not merely honoured on request. A pilot user who ticks that box and later cannot find the control has been misled.

**Fix:** Ship Settings → Privacy & Data with a working deletion flow (D5) before the first family onboards. Non-negotiable.

---

### F-02 · Only the buyer consents; the data principal never does
**Severity: Critical — compliance and ethics** · **Where:** onboarding consent step; no parent-side flow exists

The adult child ticks a box agreeing that Katha may record their parent's voice. The parent — whose voice, life story, health details and family relationships are being recorded — is not asked. `grep` across `backend/scheduler/session_initiator.py` and `backend/adapters/whatsapp.py` finds no welcome or consent message, despite PRD §6.1 step 4 specifying one.

Under the DPDP Act the elderly parent is the data principal. A competent adult cannot have consent given on their behalf by their child. Beyond the legal exposure, this is an ethical problem that goes to the heart of the product: a service premised on making an elderly person feel heard should not begin by not asking them.

There is also a real product risk here. A parent who receives an unexplained voice note from an unknown number at 9:30am will block it. The welcome message isn't just compliance — it is the activation moment for the primary user.

**Fix:** A parent-side WhatsApp welcome + spoken consent turn (F1 in the storyboard), recorded to `consent_records` as a distinct principal. Portal-side, the buyer's consent step should be reframed honestly: *"We'll ask [parent] for their own permission before the first conversation."* The dashboard should show consent status. **P0.**

---

### F-03 · No public marketing surface — the front door is a login form
**Severity: Critical — conversion** · **Where:** `frontend/app/page.tsx` (5 lines: `redirect("/family")`)

`katha.life` redirects to `/family`, which redirects to `/family/login`. A first-time visitor — someone who saw a memory card in a WhatsApp group, or clicked a link a friend sent — lands on a bare email field under the words "Welcome to Katha."

PRD §4.1 states the primary objection is *"not price but trust and proof of value."* We currently offer neither before asking for an email. PRD §3.5 identifies *"category creation burden: no established demand for an AI reminiscence agent in India"* — a category you have to create cannot be created by a login box.

**Fix:** A landing page that does the four jobs of a category-creating page — name the ache, show the artifact, prove the safety, price it plainly (A1). Plus a shareable public memory-card page (C8) so the viral loop has somewhere to land. **P0.**

---

### F-04 · The dashboard answers the wrong question
**Severity: High — retention** · **Where:** `frontend/app/family/page.tsx`

The dashboard shows three lifetime counters (sessions, stories, domains covered), the latest memory card, and eight progress bars. Everything on it is cumulative and slow-moving. Nothing on it changes day to day.

The buyer's question, every time they open this, is not "how many story atoms have accrued." It is: **"Did Amma talk today? Did she sound alright? Is this working?"** PRD §5.2 names the value proposition as *"peace of mind that their parent is engaged daily."* That is not rendered anywhere in the product.

The data to answer it already exists — `sessions` has `started_at`, `exchange_count`, `energy_signal`, `last_user_message_at`, `goal_met`. `energy_signal` in particular is a wellbeing proxy that is computed, stored, and then thrown away.

**Fix:** Restructure the dashboard around a "today" module — last session status, duration, energy, what she talked about — with the archive as the second tier (C2). Add a 30-day engagement strip. **P0.**

---

### F-05 · There is no archive of her voice
**Severity: High — core value** · **Where:** entire portal

PRD §7.4 lists "listening to audio clips (optional, with user consent)" as an MVP feature. The portal has no audio playback anywhere. `backend/media/storage.py` exists and voice notes pass through the system.

For a voice-first product, this is the emotional centre of gravity and it is absent. In five years, the transcript of what Subramaniam said about his father's shop will be useful. **The recording of him saying it, in his voice, is the thing the family cannot replace.** Every competitor in the legacy space understands this; it is the reason StoryCorps is archived at the Library of Congress as audio.

There's a design consequence too: audio is what makes the archive feel like a keepsake rather than a database export.

**Fix:** Waveform player on story detail, per-session audio, and downloadable clips (C4). If rights/storage aren't ready, ship the player with the session audio only. **P0 for pilot** — this is what makes pilot families tell their friends.

---

### F-06 · Nothing configured during onboarding can ever be changed
**Severity: High — churn** · **Where:** no settings route exists

Onboarding collects parent name, parent WhatsApp number, family WhatsApp number, language, session time, and seed context. All six are write-once. There is no route to change any of them.

Consider the entirely ordinary things that will happen in a 30-family pilot within six weeks:

- "9:30 is when she does puja — can it be 11?"
- "She's answering in Tamil but you set it to English."
- "She's in hospital this week, please stop the messages."
- "That's my old number."
- "She's tired of childhood questions, can it move on?"

Every one of these is currently an email to you and a manual database edit. At 30 families that is annoying. At 300 it is the whole company. And a family that can't pause during a hospital stay is a family that cancels rather than pauses.

**Fix:** Settings → Conversation (schedule, language, frequency, topic steering) and a prominent Pause control (D1, D2). **P0.**

---

### F-07 · The freemium gate — the entire business model — is a `mailto:`
**Severity: High — revenue** · **Where:** `frontend/app/family/layout.tsx:~62`

At session 10, a banner appears: *"Contact us to continue →"*, linking to `mailto:hello@katha.life?subject=Upgrade`.

Three problems, compounding:

1. **No runway.** The banner's condition is `session_count >= session_limit`. It appears only *after* the wall is hit. The parent's daily rhythm — the habit we spent ten sessions building — stops with no warning to anyone. PRD §9.1 targets 30% free-to-paid conversion; a cold stop is the worst possible moment to ask for money.
2. **No conversion surface.** The moment of peak intent routes to an email client. There is no upgrade page, no pricing recap, no payment.
3. **Worst of all — the parent is not told.** Katha simply stops calling. From Subramaniam's side, the friend who called every morning for ten days has silently disappeared. For a product whose stated purpose is reducing loneliness among people who have experienced abandonment, this is the single most harmful interaction in the system.

**Fix:** Runway indicator from session 7 ("3 conversations left on your free trial"), a real upgrade page with Razorpay (D4), and — critically — a graceful WhatsApp message to the parent explaining the pause in warm human terms (F6). **P0.** The parent-facing message is the part I would ship even if payment isn't ready.

---

### F-08 · No operator surface — you will run the pilot blind
**Severity: High — pilot viability** · **Where:** `backend/api/routes/admin.py` (one DELETE endpoint)

The go/no-go criteria in `docs/PLAN.md` require measuring: ≥50% of families completing 10+ sessions, ≥70% opening a memory card, ≥75% extraction accuracy on the first 50 story atoms.

There is no interface that reports any of these. There is no way to see which families have stalled, which sessions errored, whether STT confidence is degrading on a particular dialect, or which extractions look wrong. During a 30-family pilot you will be answering these questions with `psql`, at speed, probably at night.

The extraction-accuracy criterion is the sharpest example: it explicitly requires *manual validation of the first 50 story atoms*, and there is no review queue to do it in.

**Fix:** An internal ops console — cohort health table, family drill-down, extraction review queue (G1–G3). **P0**, and honestly the highest leverage-per-hour item on this list.

---

### F-09 · Silence is invisible and unhandled
**Severity: High — retention** · **Where:** no surface

If Subramaniam stops replying — illness, travel, irritation, a blocked number, a dead phone — nothing happens. No dashboard state, no alert to Priya, no adaptive behaviour.

This is the highest-signal event in the entire product. It is simultaneously the leading indicator of churn, a possible welfare signal for an elderly person living alone, and the exact moment the buyer most needs to hear from us. PRD §7.1 defines a 30-minute in-session nudge but nothing at the scale of days.

Handled well, it is the moment Priya tells her friends about Katha: *"they noticed before I did."* Handled as it is now, it is the moment she quietly cancels.

**Fix:** Escalation ladder — day 2 gentle re-ask to parent, day 3 dashboard state change, day 4 notify the family (E2). **P0** for the dashboard state and the family notification.

---

### F-10 · Extracted entities are captured and never surfaced
**Severity: Medium–High — value realisation** · **Where:** `backend/extraction/entity_extractor.py`, `models/fact.py` vs. the portal

The backend extracts people (with relationships), places, institutions, and events into a structured fact store with a vector index. The portal exposes exactly one filter: eight domain chips, plus pagination.

By session 40 the archive holds several hundred story atoms. Finding "the one about Thatha's shop" means paging through them. Meanwhile a rich, queryable graph of that person's life sits unused behind the API.

This is also the most defensible thing Katha builds. Anyone can record voice notes; almost nobody assembles a structured model of a life. Not showing it means the product looks like a voice-note folder.

**Fix:** Full-text search across the archive (P0 — a search box is cheap and immediately useful), then a People & Places index (P1) and a life timeline (P2). C3, C6, C7.

---

### F-11 · Story detail reads like a database record
**Severity: Medium — emotional register** · **Where:** `StoryDetailClient.tsx`, `components/StoryCard.tsx:33–45`

The story page renders the 5W extraction as a two-column `<dl>`: **Who** / **What** / **When** / **Where** / **Why it mattered**. The story *list* goes further — every card shows a five-dot completeness meter, with `aria-label="Completeness: 3 of 5"` read aloud to screen-reader users.

Those dots are the sharpest instance of the problem. They are an internal quality metric for our extraction pipeline, rendered to a daughter as a score out of five on her mother's memory of her wedding day. A three-dot story is not a worse memory. Nobody outside the engineering team should ever see this number.

More broadly: this is the screen where a family reads their parent's life. It should feel like a page from a book — the narrative and the voice foregrounded, structure receding to a quiet sidebar.

**Fix:** Narrative-first layout, quote and audio at the top, 5W demoted to a subtle sidebar, completeness score stripped from the family-facing serializer entirely and retained only in the ops console (C3, C4, G3). **P0** — this is mostly a layout change, cheap and high-impact.

---

### F-12 · It is a "family dashboard" with room for one family member
**Severity: Medium — growth** · **Where:** `models/family_account.py` — one email per `user_id`

One email, one login, one account. Siblings cannot be invited. PRD §5.4.2 names *"viral growth through native contact sharing"* as a key differentiator; PRD §8 Phase 2 promises WhatsApp group delivery.

Every sibling in a family is a person with an aging parent, already emotionally primed, reachable for free. It is the cheapest acquisition channel the product has and there is no door for them.

**Fix:** Read-only family member invites (D3, **P1**) and — earlier and cheaper — a public shareable memory-card link (C8, **P0**) so a card forwarded to a WhatsApp group has a landing page instead of being a dead JPEG.

---

### F-13 · Memory cards can be downloaded but not shared
**Severity: Medium — growth** · **Where:** `frontend/app/family/cards/page.tsx`

The card modal offers `<a download>`. On mobile — where most of these will be opened, since cards arrive via WhatsApp — a download link lands a file in a folder and the flow ends. There is no Web Share API call, no direct "send to WhatsApp," no shareable URL.

The card is the product's atomic unit of delight and its natural viral object. We built the object and not the loop.

**Fix:** `navigator.share()` with WhatsApp fallback, and a public card URL (C5, C8). **P0** — a few lines of code standing between us and the growth mechanic.

---

### F-14 · Day 1 shows an empty dashboard at the moment of peak anxiety
**Severity: Medium — activation** · **Where:** `frontend/app/family/page.tsx` has no empty state

Priya finishes onboarding, is pushed to `/family`, and sees: 0 sessions, 0 stories, 0 of 8 chapters, eight empty progress bars, no memory card. She has just paid — or committed — and the product's first impression is a set of zeros.

The system knows exactly what is about to happen: *Katha will message Subramaniam tomorrow at 9:30 IST.* The confirmation screen says so and then drops the thread.

This is a 24–48 hour window with nothing in it, at the precise point when a new user is asking themselves whether this was a good idea.

**Fix:** A purpose-built Day 0 state — a countdown to the first conversation, a preview of what Katha will ask, a "send a test message to myself" reassurance action, and a nudge to add seed context while waiting (C1). **P0** — cheap and it directly protects activation.

---

### F-15 · Accessibility and legibility fall short of the stated bar
**Severity: Medium** · **Where:** throughout

`docs/PLAN.md` sets a Lighthouse accessibility target of ≥85. Current issues:

- Body copy is `text-sm` (14px) almost everywhere — 42 occurrences across `app/` and `components/`. The buyer is 35–55; presbyopia begins around 40. Base should be 16px, with 18px for narrative reading.
- `#6B5B4E` on `#FDF6EC` measures ~5.9:1 — passes AA for body text, fails AAA, and is used for genuinely important information (progress counts, dates, empty states).
- `#C8956C` (the accent) on `#FDF6EC` measures ~2.5:1 — well below AA. It is currently used for link text (`hover:text-[#C8956C]`, the "Back to stories" link, the privacy link). Accent *text* needs a darker variant; keep `#C8956C` for fills only.
- The card modal has `role="dialog"` and `aria-modal` but no focus trap, no Escape handler, and no focus restore.
- Loading states are bare `Loading...` text with no `aria-live` region.
- Error states — `"Couldn't load stories."` — offer no retry action.
- No `prefers-reduced-motion` handling.
- No skip-to-content link.

None of these is hard. Together they're the difference between meeting the stated bar and missing it.

**Fix:** Type scale bump, contrast pass, focus management, retry affordances on all error states. Folded into each screen spec. **P0** for the type scale and error retries; **P1** for the rest.

---

### Summary table

| # | Finding | Severity | Priority |
|---|---|---|---|
| F-01 | Consent promises a settings page that doesn't exist | Critical | P0 |
| F-02 | Parent (the data principal) never consents | Critical | P0 |
| F-03 | No public marketing surface | Critical | P0 |
| F-04 | Dashboard answers the wrong question | High | P0 |
| F-05 | No audio anywhere in the archive | High | P0 |
| F-06 | Nothing configured can ever be changed | High | P0 |
| F-07 | Freemium gate is a `mailto:`; parent isn't told | High | P0 |
| F-08 | No operator surface for the pilot | High | P0 |
| F-09 | Parent silence is invisible and unhandled | High | P0 |
| F-10 | Extracted entities never surfaced; no search | Med–High | P0 / P1 |
| F-11 | Story detail reads like a database record | Medium | P0 |
| F-12 | "Family" dashboard holds one person | Medium | P0 / P1 |
| F-13 | Cards downloadable but not shareable | Medium | P0 |
| F-14 | Empty dashboard during the Day-1 anxiety window | Medium | P0 |
| F-15 | Accessibility below the stated Lighthouse bar | Medium | P0 / P1 |

---

## 3. Personas

Five personas. Two are users of the portal, one is a user of the product but never the portal, one is a future audience that constrains design today, and one is you.

---

### P1 · Priya, 42 — The Buyer
*Product manager, Bangalore. Parents in Coimbatore. Sister in New Jersey.*

**Relationship to the portal:** Primary user. Roughly 90% of all portal sessions.

**What she actually does:** Opens a memory card notification on her phone during a work break, reads it in 40 seconds, feels something, closes it. Opens the full portal on a laptop maybe twice a month, usually on a Sunday, and reads properly.

**What she is really buying:** Not an archive. Relief from a specific, recurring guilt — that she lives 400km away, that their weekly calls have become a health-status exchange, that her father's stories will go with him. Katha's job is to convert that guilt into something she can see working.

**Her question, every single time she opens the app:** *"Is he okay, and is this actually working?"*

**Design implications**
- Mobile-first, unambiguously. Phone is her default context; laptop is the exception.
- The most recent thing must be the most prominent thing. Recency beats totals.
- Emotional payload in under 30 seconds — a quote, a photo-card, a line of his voice.
- Every screen must survive being read in a lift with one hand.
- She will never read documentation. Nothing may require explanation.

**What makes her churn:** Silence she doesn't understand. A month of nothing, or a parent who stopped and nobody told her.

**What makes her evangelise:** A card that makes her cry, forwarded to the family WhatsApp group. Design for that single moment.

---

### P2 · Subramaniam, 74 — The Storyteller
*Retired schoolteacher, Coimbatore. Widowed 2023. Lives alone. Uses WhatsApp daily for family photos.*

**Relationship to the portal: none. He will never open it — not once.**

This is the most important and most easily forgotten fact in this document. His entire experience is WhatsApp voice notes. And yet **every parameter of his daily life with Katha is set by his daughter in a five-minute wizard he never sees.**

He is therefore a **user by proxy**: he has needs, preferences and boundaries that the portal must represent on his behalf, and — critically — must let him revise. When he tells Priya "not so early," that is a settings change. When he says "I don't want to talk about the hospital years," that is a topic exclusion. When he simply stops replying, that is the loudest input he can give and the system must hear it.

**What he wants:** To be listened to by something with no agenda and no impatience. The archive matters to him mostly as evidence that the listening was real.

**What he fears:** Doing it wrong. Wasting someone's time. Being a burden. That his life "isn't interesting."

**Design implications for the portal**
- Every setting is an act of advocacy for him. Frame settings in his terms — *"Best time to call Appa"*, not *"Session schedule."*
- Pause must be one tap and guilt-free. Illness and travel are normal, not exceptions.
- He must be able to say no *inside WhatsApp*, without going through his daughter — and that must propagate to the portal so Priya can see it and respect it.
- Never show him as a metric to his own family. He is not a dashboard KPI.
- His consent is his own to give (F-02).

**The line I'd hold:** if a portal feature would embarrass him were he shown it, don't build it. Test every screen against: *would I be comfortable showing this to him?* The completeness score (F-11) fails that test immediately.

---

### P3 · Ravi, 38 — The Sibling
*Brother, Dubai. Never onboarded. Arrives via a card Priya forwarded to the family group.*

**Relationship to the portal:** None today. This is a gap, not a decision.

He is the highest-intent, lowest-cost acquisition target the product has — same parent, same guilt, already holding proof of value in his hand. Currently, tapping a forwarded card gets him a JPEG or a login wall.

**Design implications**
- Public, unauthenticated card page with a soft entry (C8) — **P0**.
- Read-only family member invites (D3) — **P1**.
- Distinguish account owner (billing, settings, deletion) from viewer (read, share) from contributor (submit questions, Phase 2).

---

### P4 · Ananya, 12 — The Grandchild
*Priya's daughter. The archive's true long-term audience.*

Not a persona we build screens for in the pilot (PRD Phase 3). But she is a **design constraint today**, because she determines what "done" means.

The archive is not a product feature; it is an inheritance. She will open it in 2045, probably after her grandfather has died, on a device that doesn't exist yet.

**Design implications now**
- Export must be real and complete — audio, transcripts, stories, in open formats. If Katha the company disappears, the family keeps everything. **P1**, and it is also the strongest possible trust argument on the landing page.
- Preserve the voice, not just the text (F-05).
- Never destroy source data at extraction time; the raw recording outlives every schema we write.

---

### P5 · Krish — The Operator
*You. Founder, running a 30-family pilot single-handed.*

**Relationship to the portal:** Needs a surface that does not exist.

You have three jobs during the pilot: keep every family alive, validate extraction quality by hand, and catch infrastructure failures before families notice them.

**Design implications**
- Cohort health at a glance — who's stalled, who's near the free wall, who errored (G1).
- Drill into any family: session log, transcripts, API costs, failure reasons (G2).
- Extraction review queue with keyboard-driven accept/reject to make the 50-atom validation gate tractable (G3).
- Alerting on the two things that silently kill pilots: STT confidence collapse and webhook delivery failure.

---

## 4. Journeys

Seven journeys. Each names the emotional state, the drop-off risk, and the screens involved.

---

### J1 · Discovery → Activation (Priya) · *~12 minutes, one sitting*

| Step | Emotional state | Screen | Risk |
|---|---|---|---|
| Sees a friend's memory card in WhatsApp | Curious, slightly wistful | **C8** public card | **Today: dead end.** |
| Lands on katha.life | Skeptical — "is this an AI gimmick?" | **A1** landing | **Today: a login form.** Highest drop-off in the funnel. |
| Reads how it works | Wary of privacy, of AI, of Appa's reaction | **A2** how it works, **A3** trust | Unanswered: "won't he find it strange?" |
| Sees pricing | Relief — cheaper than expected | **A1** pricing | Free trial must be prominent; ₹5,000 framed annually as a gift |
| Enters email | Low commitment | **B1** | Magic link is good — no password for a 42-year-old on a phone |
| Checks email, clicks link | Mild friction | **B2** | Gmail promotions tab. Needs "resend" + a spam-folder hint |
| Fills parent profile | Engaged, thinking about Appa | **B3** | Phone format errors are the top failure. Live-validate, don't gate on submit |
| Adds seed context | **The emotional hook.** Writing about her father | **B4** | Currently one bare textarea. Should be guided prompts — this is where she falls in love with the product |
| Consents | Brief pause — "is this okay?" | **B5** | Must be honest that Appa will be asked separately (F-02) |
| Confirmation | Anticipation + slight anxiety | **B6** | "Katha will message Appa tomorrow at 9:30" — then *tell her exactly what he'll receive* |
| Lands on dashboard | **Anxiety peak** | **C1** Day-0 state | **Today: a wall of zeros (F-14)** |

**Key insight:** the seed-context step (B4) is doing double duty and we're wasting it. It primes the AI *and* it's the moment Priya emotionally invests. Guided prompts — "What's one thing about your father most people don't know?" — turn a form field into the moment she commits.

---

### J2 · The First 72 Hours — the trust window · *the highest-risk window in the product*

| When | What happens | Screen | Risk |
|---|---|---|---|
| Day 0, evening | Priya finishes setup | **C1** | Nothing to look at |
| Day 0, evening | **Appa receives the welcome + consent request** | **F1** | **Today: doesn't exist (F-02).** He gets an unexplained voice note at 9:30 the next morning |
| Day 1, 9:30 | First session | **F2** | If he doesn't reply, we have no plan (F-09) |
| Day 1, 10:00 | First memory card to Priya's WhatsApp | **F4** | **The single most important moment in the product.** If this card is good, she's a customer for a year |
| Day 1, 10:05 | She taps through to the portal | **C2** | First real dashboard view. Must feel alive |
| Day 2–3 | Sessions 2–3 | **C2** | Dashboard must visibly change daily or it dies |

**Design principle:** the first memory card carries more weight than every portal screen combined. Everything upstream exists to make that card land.

---

### J3 · The Weekly Rhythm · *the habit that becomes the subscription*

Two distinct modes and the portal currently serves only the second:

**Mode A — the 40-second phone check (~5×/week).** Triggered by a memory card notification. She wants: the card, the quote, "he talked for 18 minutes and sounded good." Then she's gone. → **C2** dashboard must load fast and answer instantly on mobile.

**Mode B — the Sunday laptop read (~2×/month).** Coffee, no rush. She reads three or four stories properly, listens to audio, shares one with her sister. → **C3/C4/C5** must reward depth: comfortable measure, audio, search, generous typography.

**Today both modes get the same three counters and eight progress bars.** Mode A is under-served (no "today"), Mode B is under-served (no audio, no search, database-style story pages).

---

### J4 · The Conversion Moment · *session 7 → 10, where the business model lives*

| Session | What should happen | Screen | Today |
|---|---|---|---|
| 7 | Gentle runway notice — "3 conversations left" | **C2 / E1** | Nothing |
| 8–9 | Value recap: "Appa has shared 24 stories across 5 chapters" | **E1** | Nothing |
| 10 | Sessions stop | — | Hard stop, no warning |
| 10 | Banner appears | layout.tsx | `mailto:` (F-07) |
| 10 | **Appa is told nothing** | **F6** | **Katha simply vanishes on him.** The most harmful moment in the system |
| 10+ | Upgrade + pay | **D4** | Doesn't exist |

**The reframe:** the conversion pitch is not "unlock features." It is *"Appa is mid-story. Chapter 4 of 8. Don't stop him here."* Loss aversion, and it happens to be true.

**And regardless of payment readiness: ship the parent-facing pause message (F6).** An elderly man who has been talking to a friend every morning for ten days deserves to be told, in his own language, that there's a pause and that it isn't his fault.

---

### J5 · Sharing & Family Expansion · *the growth loop, currently absent*

Card lands → Priya feels something → forwards to family group → **Ravi taps → dead end.**

Fix the loop: `navigator.share()` on the card (C5) → public card page with the story and a soft "this is Appa's archive — ask Priya for access" (C8) → optional read-only invite (D3).

One elderly parent typically has 2–4 adult children and 3–8 grandchildren. Every archive is a small warm audience of people with the same guilt. This loop is worth more than any paid channel Katha will run.

---

### J6 · Trouble Journeys · *where products earn or lose loyalty*

| Scenario | Should happen | Screen | Today |
|---|---|---|---|
| Parent silent 3+ days | Dashboard state changes; family notified day 4 | **E2** | Invisible (F-09) |
| Parent in hospital | One-tap pause with a return date | **D2** | Impossible → cancellation |
| Wrong language | Change in settings, effective next session | **D1** | Impossible |
| Parent says "stop calling me" | Honoured immediately in WhatsApp, reflected in portal | **F5 / E2** | No path |
| Parent expresses distress | Crisis protocol, iCall 9152987821, family alerted | **F5 / E4** | Prompt-level only; no family-facing surface |
| STT failing on dialect | Ops alert, manual review | **G2** | Invisible |
| Family wants data deleted | Two-step confirm, export offered first | **D5** | Endpoint exists, unreachable (F-01) |
| **Parent dies during the pilot** | Memorial mode: sessions stop, archive becomes permanent, warm handling | **E5** | **No path.** With 65–85 year olds and 30 families over months, this is not hypothetical |

**E5 deserves a moment.** Katha's users are elderly. Over a long enough pilot, someone will die, and their family will open this product. Whatever happens next — an automated "we haven't heard from you in a while!" nudge, a cheerful upgrade prompt, a billing email — will be remembered forever. Memorial mode is P1 in build order, but the *guardrail* (an operator kill-switch that stops all automated messaging for one family instantly) is **P0**.

---

### J7 · Operator Journey (Krish) · *daily during the pilot*

**Morning (5 min):** open ops console → overnight session results, failures, silent families. → **G1**

**Weekly (30 min):** review new story atoms for extraction quality against the 75% gate; check API costs against unit economics; look at who's near the free wall. → **G3, G1**

**On alert:** STT confidence drop, webhook failures, a family with three consecutive failed sessions. → **G2**

Without G1–G3 this is all `psql` and log tailing, and you will find out about problems from the families themselves — which in a 30-family pilot is the same as finding out too late.

---

## 5. Screen inventory

Full specifications, priorities, and acceptance criteria are annotated on each screen inside the clickable prototype: **`docs/wireframes/katha-wireframes.html`**.

| ID | Screen | Group | Priority | Status |
|---|---|---|---|---|
| A1 | Landing page | Acquisition | **P0** | New |
| A2 | How it works | Acquisition | P1 | New |
| A3 | Trust & privacy | Acquisition | **P0** | Revise `/privacy` |
| B1 | Email entry / login | Onboarding | **P0** | Revise |
| B2 | Check your email | Onboarding | **P0** | Revise (resend) |
| B3 | Parent profile | Onboarding | **P0** | Revise |
| B4 | Seed context (guided) | Onboarding | **P0** | Revise |
| B5 | Consent (two-party) | Onboarding | **P0** | Revise |
| B6 | Confirmation | Onboarding | **P0** | Revise |
| C1 | Dashboard — Day 0 | Portal | **P0** | New |
| C2 | Dashboard — active | Portal | **P0** | Rebuild |
| C3 | Stories browser + search | Portal | **P0** | Revise |
| C4 | Story detail + audio | Portal | **P0** | Rebuild |
| C5 | Memory cards + share | Portal | **P0** | Revise |
| C6 | Life timeline | Portal | P2 | New |
| C7 | People & places | Portal | P1 | New |
| C8 | Public shared card | Portal | **P0** | New |
| D1 | Settings — conversation | Account | **P0** | New |
| D2 | Settings — pause | Account | **P0** | New |
| D3 | Settings — family members | Account | P1 | New |
| D4 | Settings — plan & billing | Account | **P0** | New |
| D5 | Settings — privacy & data | Account | **P0** | New |
| E1 | Free trial runway & paywall | Lifecycle | **P0** | New |
| E2 | Parent silent | Lifecycle | **P0** | New |
| E3 | Session failure | Lifecycle | P1 | New |
| E4 | Distress escalation | Lifecycle | **P0** | New |
| E5 | Memorial mode | Lifecycle | P1 | New |
| F1 | WhatsApp — welcome & consent | WhatsApp | **P0** | New |
| F2 | WhatsApp — daily session | WhatsApp | **P0** | Exists (storyboard) |
| F3 | WhatsApp — no reply | WhatsApp | **P0** | Partial |
| F4 | WhatsApp — card delivery | WhatsApp | **P0** | Exists |
| F5 | WhatsApp — boundaries & crisis | WhatsApp | **P0** | Partial |
| F6 | WhatsApp — trial pause | WhatsApp | **P0** | New |
| G1 | Ops — cohort health | Ops | **P0** | New |
| G2 | Ops — family detail | Ops | **P0** | New |
| G3 | Ops — extraction review | Ops | **P0** | New |

**Totals:** 36 screens — 30 P0, 5 P1, 1 P2.

The P0 count is high because most of it is *missing surface* rather than *polish*. Twenty of the thirty P0 screens do not exist in any form today.

---

## 6. Design system

**Resolved August 2026:** the repo contained two divergent palettes. `coming-soon/index.html` used saffron + gold on deep indigo with Playfair Display and DM Sans; the Next.js app used terracotta on parchment with Geist (the unchanged `create-next-app` default). They shared no colour values and no typeface.

The coming-soon brand is now the source of truth. It is the look the market has already seen, and it was the more considered of the two — it already carried semantic tokens, a WhatsApp colour, and an elevation scale. The app has been repainted to match and the palette now lives in `frontend/app/globals.css` as Tailwind v4 `@theme` tokens rather than 113 hardcoded hex literals across 12 files.

### Colour

Values are pre-resolved from the coming-soon page's oklch definitions.

| Token | Value | Use |
|---|---|---|
| `--color-page` | `#F2EADD` | Page background (parchment) |
| `--color-surface` | `#F9F4EE` | Cards, panels (warm white) |
| `--color-surface-alt` | `#FFFFFF` | Nav, elevated sheets |
| `--color-border` | `#E3D5C2` | Hairlines |
| `--color-border-strong` | `#CBBBA5` | Input borders, hover |
| `--color-ink` | `#17182D` | Primary text — 14.60:1 |
| `--color-ink-mid` | `#454B69` | Secondary text — 7.14:1 |
| `--color-ink-muted` | `#646983` | Meta, timestamps — 4.52:1 |
| `--color-ink-faint` | `#7F859F` | **Decorative only** — 3.05:1, fails AA |
| `--color-indigo` | `#211A61` | Display type, dark fills, text on saffron |
| `--color-indigo-mid` | `#30358B` | Secondary indigo |
| `--color-saffron` | `#F77F00` | **Fills only** — primary action |
| `--color-saffron-ink` | `#B84500` | **Derived.** Accent text and links — 4.52:1 |
| `--color-gold` | `#D48500` | Fill hover, gradient partner |
| `--color-gold-ink` | `#A25700` | **Derived.** Gold as text — 4.51:1 |
| `--color-success` | `#007B40` | Session completed, healthy |
| `--color-attention` | `#A25700` | Silence, short sessions, failures |
| `--color-danger` | `#BA3D5B` | Destructive actions only |
| `--color-whatsapp` | `#009141` | WhatsApp affordances |

**Two rules that carry real weight:**

1. **Saffron and gold are fill colours only.** Saffron measures 2.20:1 as text on parchment — nowhere near AA. Use the `-ink` variants for text and links.

2. **Text on a saffron fill is indigo, not white.** White on saffron measures 2.63:1, which means the coming-soon page's primary CTA is currently inaccessible. Indigo on saffron measures 5.77:1 and keeps the vivid saffron intact rather than muddying it to make white work. **This is a live bug on the coming-soon page and should be fixed there too.**

Every `-ink` variant was derived by holding the oklch hue and chroma from the brand colour and dropping lightness until it cleared 4.5:1 on `--color-page`.

**Attention maps to gold, never to rose.** A red alert about your 74-year-old father reads as an emergency; rose is reserved for destructive actions the family initiates themselves.

### Type

**Playfair Display** for display type and story narrative; **DM Sans** for everything else. Both are already the brand faces — the earlier suggestion in this document to add Lora or Source Serif was wrong and has been dropped.

Base size moves from **14px → 16px**; narrative reading is **18px/1.7**. The buyer is 35–55 and presbyopia begins around 40.

| Role | Face | Size / line-height | Weight |
|---|---|---|---|
| Display | Playfair | 36–44 / 1.15 | 900 |
| H1 | Playfair | 28 / 1.25 | 700 |
| H2 | Playfair | 22 / 1.3 | 600 |
| Story title | Playfair | 22 / 1.3 | 600 |
| Quote | Playfair italic | 20 / 1.6 | 600 |
| Body | DM Sans | 16 / 1.6 | 400 |
| Narrative | DM Sans | 18 / 1.7 | 400 |
| Meta | DM Sans | 14 / 1.5 | 500 |

Playfair on the story pages is what makes the archive read as a book rather than an app — exactly the register a family keepsake should occupy.

### Layout

- Mobile-first. Breakpoints 640 / 1024.
- Reading measure caps at **65ch** for narrative — not the current `max-w-3xl` for everything.
- Touch targets ≥44px. The current domain filter chips are 30px tall.
- Bottom tab bar on mobile (Today / Stories / Cards / Settings) replacing the top nav; the current top nav has no Settings entry at all.

### Non-negotiable content rules

1. **Never show the completeness score to a family.** Ops only.
2. **Never use red for parent-related states.** Attention amber, never alarm red.
3. **Name the parent, always.** "Appa hasn't replied since Tuesday," not "No sessions recorded."
4. **Never quantify the parent as performance.** No streaks, no leaderboards, no "engagement score." He is not a habit-tracking target.
5. **Every empty state states what happens next and when.** No dead zeros.
6. **Every error state offers a retry.**

---

## 7. Recommended build sequence

### Sprint 1 — Compliance & the pilot floor *(cannot onboard a family without these)*

1. **D5** Settings → Privacy & Data — wire up the existing `DELETE /user/{id}` (closes F-01)
2. **F1** Parent WhatsApp welcome + spoken consent, recorded separately (closes F-02)
3. **G1/G2** Ops console — cohort health + family drill-down, with a per-family kill-switch (closes F-08)
4. **D1/D2** Settings → Conversation + Pause (closes F-06)
5. **F6** Parent-facing trial-pause message (closes the harmful half of F-07)

### Sprint 2 — The experience that makes the pilot succeed

6. **C2** Dashboard rebuild around "today" (closes F-04)
7. **C1** Day 0 state (closes F-14)
8. **C4** Story detail rebuild + audio (closes F-05, F-11)
9. **C5 + C8** Share sheet + public card page (closes F-13, half of F-12)
10. **E2** Parent-silent handling (closes F-09)

### Sprint 3 — Acquisition & revenue

11. **A1/A3** Landing page + trust page (closes F-03)
12. **B3–B6** Onboarding revision, guided seed context (J1)
13. **E1 + D4** Runway indicator + upgrade page with Razorpay (closes F-07)
14. **C3** Archive search (closes half of F-10)
15. **G3** Extraction review queue — needed to clear the 75% accuracy gate

### Sprint 4 — Fast follow, during pilot

16. **D3** family member invites · **C7** people & places · **E5** memorial mode · **E3** session failure states · **F-15** accessibility pass

### Deferred (P2)

**C6** life timeline · multi-language output · rich AI-illustrated cards · grandchild mode

---

## 8. Open questions for sign-off

1. **Payments.** Razorpay for the pilot, or manual invoicing for 30 families? Changes whether D4 is a payment flow or a lead-capture form.
2. **Audio retention.** Do we keep raw voice indefinitely? It's the most valuable and most sensitive asset. Storage cost, DPDP posture, and the C4 player all hinge on this.
3. **Parent consent mechanics.** Is a recorded spoken "yes" sufficient under DPDP, or do we need a text confirmation too? Affects F1's length — and every extra turn costs us elderly-user activation.
4. **Ops console auth.** Separate admin role on `family_accounts`, or a wholly separate internal app? Recommend the latter for the pilot — faster and safer.
5. **Pilot pricing.** Are the 30 pilot families free for the duration? If so, E1/D4 can be lead-capture only and slip to Sprint 4, which frees meaningful capacity.
6. **Timezone.** `session_time` is stored as naive `Time` and labelled IST. NRI buyers setting a time for a parent in India is fine, but an NRI *parent* breaks. Confirm India-only for the pilot.

---

*Prepared for review. Screen-level detail lives in the clickable prototype.*
