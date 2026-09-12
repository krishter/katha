# Katha — Pilot Runbook (per family)

**One run = one family.** Repeat for each of the 20–30 pilot families in `docs/PRD.md` §12.1.
Written against **what ships today** (post-Sprint 1). Steps marked 🔧 are manual because the
Sprint 2 workstream that automates them has not landed; each names its replacement.

**Three lanes throughout:**

| Lane | Who | What they are actually doing |
|---|---|---|
| **PO** | Krish (product owner / operator) | Recruiting, setup, watching, intervening |
| **Child** | Adult child, 35–55, buyer | Sets up, seeds context, receives cards, tells you the truth |
| **Parent** | Elderly parent, 65+, data principal | Consents, talks, keeps talking |

The parent never touches a browser. Everything she sees arrives on WhatsApp.

---

## Phase 0 · T-3 days — Select the family (PO only)

1. **Screen against PRD §12.1 criteria.** Parent 65+, uses WhatsApp daily *unassisted*,
   adult child in a different city/country, family has voiced concern about loneliness or
   about losing the stories. Reject politely rather than stretching a criterion — one
   unqualified family costs more than the slot is worth.
2. **Confirm the language.** Which language will she actually speak, including code-mixing.
   This sets `profile.preferred_language` and every message she gets.
3. **Run the rehearsal.** `python backend/scripts/pilot_rehearsal.py` against a clean
   database — 13 steps, 75 assertions, exit 0. Re-run it before *every* family, not once.
   It is the only thing standing between you and a repeat of the webhook `ImportError`.
4. **Check sender health.** `python backend/scripts/verify_whatsapp_sender.py` — real send,
   real number. Template approvals expire and quality ratings drop silently.
5. 🔧 **Decide the free-session posture.** `FREE_SESSION_LIMIT = 10` in `core/freemium.py`
   stops the scheduler at session 11. Either raise it for pilot families or diarise the
   cliff now (see Day 10). *Replaced by: T3.*

**Gate:** rehearsal green + sender verified. Do not proceed on a red rehearsal.

---

## Phase 1 · Day -1 — Setup with the adult child (PO + Child, ~30 min call)

Do this on a call, not by email. Family #1–#5 are hand-held; you are testing the flow as
much as they are.

**PO:**

1. Set expectations out loud: this is a pilot, things will break, you will call weekly, and
   they can stop any time. Give them your WhatsApp number.
2. Send her to **`katha.life/family/onboarding`** and stay on the call while she does it.
   The wizard runs the whole flow — email → magic link → parent profile + seed context →
   consent → done. You are there to watch where she hesitates, not to drive.
3. The consent screen already states that Katha asks the parent separately. Say it out loud
   anyway; it is the sentence that earns the family's trust.
4. The done screen carries the `wa.me` link and the three-step handover. Read it with her
   rather than re-explaining it — if it doesn't stand on its own here, it won't at family #12.

**Child:**

1. Works through the wizard herself: email, magic link from her inbox, then parent's name,
   WhatsApp number, the family number that receives cards, language, session time.
2. **Seeds context in the free-text field.** This is the highest-value step in the whole
   setup — it primes Layer 3 *and* it is where she emotionally commits. Push past one-liners:
   "What's one thing about your father most people don't know?" beats "Dad was a
   schoolteacher." Aim for names, places, decades.
3. Ticks consent as account owner, having read that the parent will be asked separately.
4. **Phones the parent first**, then sends the `wa.me` link while still on the call and stays
   on while she taps it. The done screen says exactly this; your job is to make sure she
   actually does it rather than forwarding the link cold.
5. Adds one line in her own words with the link — "Amma, I set this up for you, please say
   hello to Katha." Not a forwarded blurb.

**Parent:** nothing yet. She should have heard about it from her child, not from us.

**PO closes the call by saying what happens next, in order:** she taps the link tomorrow →
Katha introduces itself and asks her permission → first conversation the morning after →
a memory card in the child's WhatsApp that same day.

---

## Phase 2 · Day 0 — Welcome and parent consent (Parent)

**Parent:**

1. Taps the `wa.me` link, sends anything ("hi" is enough).
2. Receives the welcome — voice note + text, in her language, stating plainly that Katha
   is an AI and that her child set this up.
3. Gives or declines consent in her own words.

**System guarantee (asserted by the rehearsal, steps 5–6):** no session opens before a
`ConsentRecord` with `principal="parent"` and a populated `evidence_ref` exists. A declined
or absent consent means Katha stays silent. This is the DPDP position and the ethical one.

**PO — same day:**

- 🔧 Confirm the consent row exists and the welcome went out. *Replaced by: T1 family detail.*
- **If she has not tapped the link by evening:** do not send anything from Katha. Ask the
  child to phone her. A second unexplained message from an unknown number is how you get
  blocked.
- **If she declines:** record it, thank her through the child, close the account. A decline
  is a valid pilot outcome and worth an exit note — ask the child why.

**Child:** watches the dashboard show consent status. Warn her it is mostly zeros until
tomorrow (F-14 is unfixed; the day-0 empty state is a known anxiety point).

---

## Phase 3 · Day 1 — First session (all three lanes)

**Parent:** receives Katha's opening voice note at the agreed time, replies with a voice
note, has a 15–25 minute conversation over 4–6 exchanges.

**Child:** receives the memory card on WhatsApp a few minutes after the session, opens it,
ideally forwards it to the family group. That forward is your cheapest acquisition channel
and your clearest proof-of-value signal — note whether it happens unprompted.

**PO — within 2 hours of the session, manually:** 🔧

- Turn count and whether the session completed, or which `FailureStage` was hit.
- STT sanity: did Sarvam actually transcribe her, or did it return mush? Elderly speech in a
  regional language is where this breaks first.
- Story atoms extracted > 0, and the memory card generated *and delivered*.
- Read the transcript end to end. Once per family, on day 1. You are looking for tone, not
  metrics — did it feel like a conversation or an intake form?

*Replaced by: T1 cohort table + family detail.*

**If Day 1 fails badly:** fix it and re-run the same day, or tell the child honestly and
reschedule. Do not let a broken first session sit — the first 72 hours are the trust window
and the parent's model of "what this is" forms here.

---

## Phase 4 · Days 2–7 — The trust window

**PO — daily, ~5 minutes:** 🔧

| Check | Where | Act if |
|---|---|---|
| Session completed yesterday | `sessions` | No session, or 0 turns |
| Crisis detections | `crisis_events` rows since yesterday | **Any row.** Read it the same day |
| Turn failures | logs / `FailureStage` | Two failures in a row for one family |
| Cards delivered and opened | cards + child engagement | No open by day 3 |

**`crisis_events` is written by the orchestrator and read by nothing.** Until T1.3 lands,
*you* are the reader. For a product serving isolated elderly people, this is the one check
that cannot be skipped for a day. Escalation is a phone call to the child, plus iCall India
(9152987821) referenced in the protocol — not a dashboard note.

**Silence handling is manual until T2:** 🔧

- **2 days no reply** → warm re-ask to the parent (inside the 24h window if open, otherwise
  the approved follow-up template).
- **3 days** → tell the child, framed as concern, not as churn.
- **4 days** → phone call. For someone living alone, silence is a welfare signal before it
  is a product metric.
- Any inbound message resets the ladder.

**Child — Day 7 check-in call (PO runs it, 15 min).** Ask, in this order:
what did your parent say about it unprompted · what confused either of you · what did you
expect that didn't happen · would you pay ₹5,000/year for this today, and if not, what's
missing. Write the answers down verbatim; don't summarise into a theme yet.

**Parent — Day 7:** don't interview her directly this early. Ask the child what she has
said. A survey from the operator breaks the fiction that Katha is her conversation.

---

## Phase 5 · Days 8–12 — Two scheduled cliffs

**Session ~5 — the Layer 3 thinning.** `retrieve_relevant` returns `top_k=5` by recency, so
once atoms span more than five domains the earliest domain drops out of the prompt entirely.
🔧 Read the assembled Layer 3 block at session 5 and check `childhood` is still in it. If it
has vanished, that is T5's trigger firing — do T5 before family #2.

**Session 10 — the free limit. ⚠️ The most harmful interaction in the system today.**
At session 11 `is_session_allowed` returns false, the scheduler skips the family, and Katha
simply stops calling. The parent gets no explanation. For a product about loneliness, a
friend who vanishes without a word is worse than never having called.

Until T3 ships, **do one of these before session 10, not after:**

- Raise `FREE_SESSION_LIMIT` for pilot families (pilots aren't a payment test), **or**
- 🔧 Send the parent a warm message yourself, in her language, saying conversations are
  pausing and that it is nothing she did.

Diarise the date at onboarding: daily cadence puts it ~day 10–11.

---

## Phase 6 · Days 13–30 — Steady state

**PO — weekly, per family:**

- Sessions completed this week vs scheduled; note the trend, not the number.
- Domain spread — is Katha circling one chapter?
- 🔧 Validate 10 story atoms against their source transcript, correct/incorrect. Across the
  cohort this accumulates toward the **50-atom manual validation** the go/no-go gate needs.
  *Replaced by: T6 review queue.*
- One line in the family's notes: what changed this week, in plain language.

**Child:** dashboard visit at least once; forwards at least one card. If she has not opened
the dashboard in two weeks, that is a finding about the dashboard (F-04: it answers the
wrong question), not about her.

**Parent:** unchanged daily rhythm. Any schedule, language or number change goes through you
until T4 lands. 🔧 A hospital stay or travel means **pause**, not cancel — 🔧 stop the
scheduler for that family by hand rather than losing them.

**Day 30 — exit interview (PO + Child, 30 min).** Same four questions as Day 7 plus:
would you renew · would you set this up for your in-laws · what would you tell a friend it
is · what should we delete. Ask the child to put the parent on the phone for five minutes if
she is willing — one open question ("what do you think of your morning calls?") and then
listen.

---

## Stop conditions — end a family's run early

- Parent declines or withdraws consent → delete via `DELETE /admin/user/{id}`, then verify
  by table and bucket inspection, **not** by the endpoint's return value.
- Crisis event indicating real distress → human contact first, product second.
- Repeated failed sessions with no fix in sight → pause, apologise, keep the relationship.
- Child asks to stop → stop that day. Then ask why, once, gently.

🔧 There is no per-family kill switch until T1.4; stopping a family today is a database
edit. Know the query before you need it.

---

## What each family contributes to the go/no-go gate

Tracked per family, decided across the cohort (`docs/PLAN.md` §MVP Go/No-Go):

| Gate | This family's contribution |
|---|---|
| ≥50% complete 10+ sessions | Did she reach session 10 |
| ≥70% of children open a card | Did the child open at least one |
| ≥75% extraction accuracy | Their share of the 50 validated atoms |
| No critical WhatsApp/STT failures | Failure log for the month |
| Evals ≥80% TC-01–TC-10 | Re-run after any prompt change made for this family |

---

## Pre-flight, before family #N

- [ ] `pilot_rehearsal.py` green on a clean database
- [ ] `verify_whatsapp_sender.py` green
- [ ] Previous family's day-1 findings actually fixed or explicitly deferred
- [ ] Free-session posture decided for this family
- [ ] Day-10 cliff diarised
- [ ] Language confirmed with the child, not assumed
- [ ] DPDP processor disclosure in the privacy copy *(open item, triggered at family #2)*

---

*Owner: Krishnaraj CK · Companion to `docs/PLAN.md`, `docs/SPRINT_2_PLAN.md`, `docs/UX_REVIEW.md`*
*Revise after family #1's Day 30 — the runbook is a hypothesis too.*
