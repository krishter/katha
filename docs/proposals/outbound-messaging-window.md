# The daily session open does not reach the parent

**Status:** Open — needs a product decision, not just a code change
**Found:** 2026-10-04, while diagnosing three reported production faults
**Related:** `SPRINT_1_PLAN.md` S3.0 (error 63049, first contact)

---

## The finding

Of 436 WhatsApp messages sent from production between 1 and 4 October:

| Status | Error | Count |
|---|---|---|
| undelivered | **63016** | 419 |
| delivered | — | 11 |
| undelivered | 63021 | 4 |
| received (inbound) | — | 2 |

**63016 is "free-form message outside the 24-hour window — use a template."**

The eleven delivered messages all fall within minutes of an inbound message
from the parent. Everything Katha sent on its own initiative — including the
daily 09:30 session-opening voice note, on every single day — was rejected.

The parent is not receiving the thing the product is built to send.

## Why this is the premise, not a detail

The PRD names the outbound model as *"the single most important engagement
design decision"* (§5.4) and the primary mitigation for RISK V1, the P0
engagement risk: Katha calls the user, so the user does not have to remember
anything. §7.1 specifies a voice note at a pre-set time, and a gentle text if
there is no reply within 30 minutes.

Neither currently reaches an elderly user who has not messaged first. The
product as delivered is inbound-only: it works beautifully once the parent
says something, and is silent otherwise. That inverts the design for exactly
the population it was chosen for — the PRD's persona is someone with
technology anxiety who will not open an app unprompted.

## Three separate reasons it cannot work today

**1. There is no session-open template.** `TWILIO_TEMPLATE_SESSION_OPEN` and
`TWILIO_TEMPLATE_FOLLOWUP` in the production `backend/.env` are the literal
placeholder text `<HX...>`, never substituted. The only Katha templates that
exist on the Twilio account are `katha_welcome_v1` and
`katha_welcome_followup_v1`; everything else listed is a Twilio stock sample.
(Same class as the `JWT_SECRET` placeholder caught in S4.0 — a config value
that looks set because the line is present.)

**2. The code does not use one anyway.** `scheduler/session_initiator.py`
calls `whatsapp.send_voice_note(...)` for the daily open and
`whatsapp.send_text(...)` for the nudge. Both are free-form. `send_text`
accepts `template_sid`, so the seam exists; nothing passes through it.

**3. The one approved template is categorised MARKETING.**
`katha_welcome_v1` is `status: approved`, `category: MARKETING`. That
category is why S3.0 hit error 63049 on first contact: Meta throttles
Marketing to recipients who have never replied. A daily Marketing template
to the same unengaged recipient invites the same treatment, and at worst
damages the sender's quality rating.

## Update, 2026-10-04 — on-demand sessions change the stakes

Shipped since this was written: an inbound voice note with no active session
now **opens one** (`_open_session_on_demand`). Previously the scheduled
session was abandoned after four hours, so for twenty hours a day a parent
who reached out was told "your session isn't scheduled yet" — turned away at
the moment she asked to talk, having never been told a window existed,
because the 09:30 opener was rejected with 63016.

That makes the product work **today, without resolving any of this**. Her
own message opens the 24-hour window; everything after it is free-form and
delivers. The eleven messages that did reach her were all of exactly this
shape.

What it does not do is restore the outbound premise. Katha still cannot
start the conversation — she has to think of it. The PRD's bet is that an
elderly user will not, and that bet is the reason the outbound model exists.

So the decision below is no longer *"is the product usable?"* but *"is it
the product we designed?"* That is a weaker urgency and a better question to
take time over. Option B in particular is no longer a capitulation — it is
roughly what now ships, and the honest choice is between formalising it and
paying for A or C.

## Options

### A — A Utility-category template for the daily open

Register a session-open template and send it instead of the free-form voice
note. The parent's reply opens the window, and the real voice conversation
happens inside it as it does now.

The question this turns on is **category**. Utility is for messages tied to
something the user requested or transacted; Marketing covers promotion and
re-engagement. A daily "shall we talk about your life today?" is, on an
honest reading, closer to re-engagement — and `allow_category_change: True`
is set on the existing template, so Meta can reclassify unilaterally.

Do not assume Utility will be granted or will stick. Ask Twilio, in writing,
how they would categorise a daily conversational opener for an opted-in user,
before building on the answer.

**Cost:** template approval turnaround; every session now opens with a
templated message rather than Katha's voice, which is a real loss of warmth
on the very first thing the parent hears each day.

### B — Accept that the parent initiates

Drop the outbound premise. Katha goes quiet until the parent messages; the
family dashboard nudges the adult child to prompt their parent.

Honest, zero platform risk, and it contradicts the PRD's central engagement
claim. It also moves the burden onto the elderly user, which §4.2 says is the
thing most likely to lose them.

**Worth stating plainly:** this is the product that exists today, by
accident. Choosing it deliberately is at least coherent.

### C — Template to open, voice to converse *(recommended to evaluate first)*

A minimal approved template once a day — short, plain, no content — whose
only job is to give the parent something to tap. Her reply opens the window;
Katha's actual voice note and the whole session follow inside it, free-form.

This keeps the outbound initiation the PRD depends on, limits template
exposure to one message a day, and keeps the conversation itself in Katha's
voice. It still depends on the parent replying to something, so it is a
weaker promise than "Katha calls you" — but it is a promise the platform
permits.

**Open question:** whether a quick-reply button counts as engagement for
Meta's throttling, which would make the daily open reliable rather than
best-effort. `katha_welcome_v1` is already `twilio/quick-reply`, so there may
be data in the account's delivery history to answer this empirically.

## What to verify before committing to any of these

1. **Written answer from Twilio** on the category a daily conversational
   opener receives, and whether `allow_category_change` can be disabled.
2. **Whether the 24-hour window is per-number or per-conversation** for this
   sender — it determines whether one daily template can carry a whole
   session or only the first turn.
3. **The sender's current quality rating.** 419 rejected sends in 72 hours
   (the follow-up loop fixed alongside this) may already have hurt it. Check
   before adding more outbound volume.
4. **What the pilot family is told.** If delivery is best-effort, the
   onboarding copy should not promise a daily call at a chosen time.

## Note on sequencing

The follow-up loop that produced 419 of those rejections is fixed separately
(`sessions.followup_sent_at`). That fix reduces the volume but does not
change the outcome: one nudge a day outside the window is still one rejected
message a day. Only this decision changes that.
