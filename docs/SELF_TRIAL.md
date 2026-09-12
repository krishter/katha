# Katha — Self-Trial (before family #1)

**Purpose:** you run the whole product on yourself, in both roles, before a real family
ever touches it. Not a QA pass — `pilot_rehearsal.py` already asserts correctness. This is
about *how it feels*: the waiting, the tone, the moment a card arrives, the moment the
conversation stops.

**Two stages.** Stage A is a laptop dry run — an hour, no cost, nothing real sent, and the
only place to make destructive mistakes cheaply. Stage B is you as an actual user, **on
production** — `app.katha.life`, `api.katha.life`, the real sender number — for ~12 days.

**Stage B has a prerequisite:** none of that is deployed yet. Work
`docs/DEPLOYMENT.md` first; Stage B is the thing that proves the deployment, and you should
be the first real user of the exact URLs family #1 will use. Stage A stays local because a
dry run's job is to be free and disposable, not because local is where this belongs.

---

## Ground rules

1. **Separate the roles physically.** Child lane on the laptop. Parent lane on the phone,
   in WhatsApp only. Never read logs or the database while wearing the parent hat.
2. **No mid-run fixes.** Log what you find, keep going. A run you keep patching tells you
   nothing about what a family would have experienced.
3. **Answer as the parent would, not as the builder.** Voice notes, not typing. Ramble.
   Don't rescue a bad question with a helpful answer.
4. **Journal daily, same five lines** (template at the bottom). Two minutes, before you
   look at any data.

---

## Stage A · Laptop dry run (~90 minutes, stub adapter, ₹0)

Everything here uses `WHATSAPP_ADAPTER=stub` — nothing reaches a phone.

**1. Bring it up clean.**
```
docker-compose up -d db
make migrate
```
Confirm `.env` has `WHATSAPP_ADAPTER=stub`, `SES_MOCK=true`, `ENVIRONMENT=development`.

**2. Watch the whole lifecycle run itself.**
```
cd backend && python scripts/pilot_rehearsal.py
```
13 steps, 75 assertions. Read the output slowly — it is the shape of everything a family
goes through, from account creation to verified deletion. This is your map.

**3. Now do the child's setup in the browser**, as Priya would — not through the API.
`localhost:3000/family/onboarding`, start to finish: email, then the magic link (with
`SES_MOCK=true` it is printed in the backend logs instead of emailed), then the parent
profile and seed context, then consent, then the done screen with the `wa.me` link.

Two things to watch in yourself: how long the seed-context field takes you to fill
*honestly* — that is the step a real buyer abandons if the prompt is weak — and whether the
done screen's three steps are clear enough that you'd follow them without me explaining
them. Drop to the underlying `POST /onboarding/{start,profile,consent}` only if you want to
inspect what the wizard sent.

**4. Read the welcome text your parent-self will receive.** `core.parent_consent.build_welcome_text`.
Say it out loud. Would a 72-year-old who has never spoken to an AI understand it?

**5. Simulate the parent's first inbound** — `POST /webhook/whatsapp` with Twilio's form
fields (`From`, `Body`). Confirm the welcome + AI disclosure comes back and that
**no session opens yet**.

**6. Probe the consent edges here, not in Stage B.** Send `"hmm"` and then `"acha"` — both
are deliberately *not* consent. Watch it ask again, then reach `HALTED` after two asks.
Then, on a fresh account, send `"ok"` and confirm a `ConsentRecord` with `principal="parent"`
and a populated `evidence_ref`. This is the compliance spine of the product; see it work.

**7. Run two sessions a day apart** (the rehearsal script does this) and read the assembled
**Layer 3** block for session 2. Does it carry facts, people and open threads from session 1?
This is the single best predictor of whether Katha feels like it remembers you.

**8. Look at the dashboard as the child.** `localhost:3000/family`. Land on it the way Priya
does — right after setup, with zeros everywhere (F-14). Sit with that for a minute.

**9. Delete yourself.** `DELETE /admin/user/{id}`, then verify by table and bucket inspection
rather than the endpoint's return value.

**Stage A output:** a list of things that surprised you. Fix the cheap ones now — this is the
last point where fixing is free.

---

## Stage B · Real self-run on WhatsApp (~12 days)

You are now the parent. Someone else, or your other number, is the child.

### Setup (day before)

- **Production is live and proven.** `docs/DEPLOYMENT.md` worked end to end, including
  `pilot_rehearsal.py` run against the production stack and a magic link that actually
  arrived in your inbox. Everything below assumes `app.katha.life` and `api.katha.life`.
- **Numbers.** Three distinct roles: Katha's sender (`TWILIO_WHATSAPP_NUMBER`), the parent's
  number (**your personal WhatsApp**), the family number that receives cards. Use a *second*
  number for the family lane if you can — spouse, second SIM — so you experience the card
  arriving somewhere you weren't already looking. Same number for both works, but collapses
  two distinct moments into one chat.
- **Real keys, real everything:** `ENVIRONMENT=production`, `WHATSAPP_ADAPTER=twilio`,
  Sarvam, Anthropic, S3 `ap-south-1`, SES out of the sandbox, templates approved and their
  SIDs in `.env`.
- **Webhook on the real hostname.** Twilio points at `https://api.katha.life/webhook/whatsapp`
  and `PUBLIC_BASE_URL` matches it exactly. Verify with
  `python scripts/verify_whatsapp_sender.py` before anything else — a mismatch 403s every
  inbound message and looks, from the phone, like Katha ignoring you.
- **Single process only.** The APScheduler is in-process; a second worker means the parent
  gets duplicate opening voice notes. Confirm one "Scheduler started" line in the logs.
- **Pick a language you'll actually speak,** including how you really mix English into it.
  If the pilot will be Malayalam or Tamil, do not run this in English — STT on code-mixed
  elderly speech is exactly what you're testing.
- **Pick a real time** — one you will genuinely be free at, daily. Testing at a convenient
  time hides the friction that kills real habits.

### Day 0 — Consent

Do the setup as the child on the laptop, at **`app.katha.life/family/onboarding`** — the
real URL, the real magic-link email, on the phone-and-laptop split a real buyer has. Then put the laptop away, pick up your phone, and
**wait until you actually want to tap the link.** That gap is real: Katha cannot open the
conversation (Meta 63049), so nothing at all happens until the parent taps.

Tap, say "Namaste", listen to the welcome as a voice note, give consent by voice.

*Notice:* how long the welcome took to arrive · whether the AI disclosure landed before the
ask · whether the voice sounds like someone you'd talk to again.

### Days 1–4 — The trust window

Take the session at the scheduled time, on your phone, by voice, before looking at anything.

*Notice each day:*
- **Latency.** Seconds between your voice note and Katha's reply. Above ~15s an elderly user
  assumes it's broken and sends "hello?" — watch for your own urge to do that.
- **STT.** Did it hear your names, places, dialect words? Log every mangled proper noun.
- **The questions.** Did the follow-up come from what you just said, or from a script?
- **Length.** Does 4–6 exchanges really run 15–25 minutes, or is it over in 6?
- **The card.** Check the family number. Would you forward it to a family group unprompted?
  If not, say why in one line — that card is the entire acquisition strategy.

**Day 3 — deliberately go silent.** Skip the session, ignore the follow-up. Nothing will
happen, because the silence ladder (T2) isn't built. Feel the absence: that is precisely
what family #1's parent gets when she has a bad week.

### Days 5–8 — Memory

**At session ~5, check Layer 3.** Once your atoms span more than five domains, `top_k=5`
recency retrieval drops the earliest domain out of the prompt. Ask Katha, in conversation,
about something you told it on day 1. If it has forgotten, that's T5's trigger firing on you
instead of on a customer.

Also notice whether the conversation is circling one chapter, and whether Katha ever
resurfaces something you said days ago unprompted. That resurfacing is the moment users
describe as "it actually remembers me."

### Days 9–12 — The cliff

`FREE_SESSION_LIMIT = 10`. **Let it fire.** Do not raise it for yourself.

At session 11 the scheduler skips you and Katha simply stops calling. No message, no
explanation. Wait two days without touching the code.

This is the most valuable hour of the whole self-trial: you will know, first-hand, exactly
what the harmful half of F-07 does to someone who had begun to look forward to the call.
Then go implement T3.

### Teardown

Delete your own data through the real endpoint and verify it by table and bucket inspection.
You should experience the deletion path as a user once — including how it feels to lose the
stories.

---

## Daily journal — five lines, before you look at any data

```
Day N ·
1. Did I want to take the call today? (y/n, one clause why)
2. Best moment:
3. Worst moment:
4. Anything that made me feel managed rather than heard:
5. What would my father/mother have done differently here:
```

Line 5 is the important one. You are a 40-something product manager simulating a 70-year-old;
the gap between you and the real user is the main limitation of this exercise, and writing
it down each day is how you stay honest about it.

---

## What this costs, roughly

Sarvam STT ≈ ₹1/min and TTS ≈ ₹0.50/min (`CLAUDE.md`). A 20-minute session with ~10 minutes
of your speech and ~3 minutes of Katha's runs ≈ ₹12. Twelve sessions ≈ **₹150 or so in
Sarvam**, plus Claude tokens and Twilio conversation charges. The cost of this exercise is
your time, not your money.

---

## Optional Stage C — one real elder, one session

Before family #1, sit beside someone actually 65+ — with their permission and their own
consent turn — for a single session, and say nothing while they use it. Watch their hands.
Watch how long they hold the record button. Watch what they do when they mishear.

Thirty minutes here will overturn more assumptions than your twelve days did. Your own run
teaches you the product; this teaches you the user.

---

## Gate — what must be true before you onboard family #1

- [ ] `docs/DEPLOYMENT.md` complete: production reachable, rehearsal green against it
- [ ] Stage A run end to end, including the consent-edge probes and verified deletion
- [ ] 10+ real sessions on your own number, in the pilot's language
- [ ] Median reply latency known and written down
- [ ] STT failure list logged (names, places, dialect words)
- [ ] Layer 3 checked at session 5 with your real atom spread
- [ ] Session-10 cliff experienced, and T3 either shipped or the manual workaround diarised
- [ ] Every Stage A/B finding either fixed or explicitly recorded as shipping unfixed

---

*Owner: Krishnaraj CK · Companion to `docs/PILOT_RUNBOOK.md`*
