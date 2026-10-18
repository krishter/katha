# Conversation cards ("Did you know?"): spec

**Status:** Agreed in outline 2026-10-18 (Krish). Not built. §3 records the decisions; the end of §16 lists what is still open.
**Owner:** Krish · **Written:** 2026-10-18
**Branch:** `feature/conversation-cards`, one PR. Never commit to `main`; see `.claude/rules/git.md`.
**Depends on:** `feature/gemini-tts` merged (the read-back is spoken through `media/tts.py`). Reuses the Phase A consolidation code (`consolidation/`) for the fidelity check.
**Background:** `docs/proposals/first-person-stories.md` (§5.3 fidelity, §5.4 approval, §7.2 cards, §13 open questions); `docs/proposals/outbound-messaging-window.md`.

---

## 1. Why

**The pilot finding (Oct 2026).** A parent enjoyed telling Katha about her past, but did not know what happened to it and never saw anything come back. When asked, she said yes, she wanted her children and grandchildren to hear it. She was telling Katha *for* them. With nothing coming back, there is little reason to keep going every day.

**What we are betting.** The family's real response will happen outside Katha, on their usual calls and texts. So the most important thing Katha can make is not a dashboard. It is a prompt that makes the child pick up the phone: *"Did you know Amma walked 6 km to see her first film, without telling Ammachi? Ask her which film it was."* If the child brings it up, the parent learns she was heard, through the person she wanted to hear her.

**What this spec adds**

1. A **conversation card**: one surprising, true detail from her life, a few lines in her words, and one question to ask on the next call.
2. **Her approval before anything is sent.** Katha reads each card back to her in her language. This builds the read-back that `first-person-stories.md` §5.4 designed, scoped to cards. Stories will reuse it.
3. **Delivery on WhatsApp and email**, timed a few hours before the child's usual call.
4. **The child's follow-up question**, which Katha raises in her next conversation, credited to the child.
5. A **sharing preference** she can state in conversation, and a way to say "keep this between us" at any time.

It does not wait for the Stories build. Cards come from approved story atoms. Approved Stories can feed cards later.

## 2. Scope

**In**

- Sharing preference (asked once, early) and the in-the-moment private override.
- Spoken end-of-session recap (no ask).
- Card selection, writing, fidelity check, image.
- Operator review in an admin page.
- Read-back and approval in the next session.
- Timed delivery on WhatsApp (template) and email (SES), both channels by default.
- Child quick-reply taps, follow-up questions and reactions, routed back into her next session.
- Metrics, cost tracking, evals.
- Replacing the per-session memory card for families on this feature.

**Out (later)**

- Full Stories and their read-back (the Stories Phase B build).
- More than one family recipient. Siblings and grandchildren are reached by forwarding for now.
- Her own recorded voice on the card. Her inbound voice notes are not stored today; only Katha's replies are (`turns.response_audio_s3_key`).
- Card text in her language for the child. The card is English for MVP, labelled as translated.
- Bounce and complaint handling for email beyond the basics in §8.3.

## 3. Decisions

| # | Decision | Answer |
|---|---|---|
| D1 | Where card content comes from | **Approved story atoms and their source turns**, not Stories. Approval happens per card at read-back (§6). |
| D2 | The existing per-session memory card | **Replaced** for families with this feature on. Today's card reaches the child without the parent's agreement. The image and delivery code are reused. |
| D3 | Operator review | **Every card is reviewed by Krish** before the parent hears it. A flag turns this off later. |
| D4 | Cadence | **Up to 2 cards in any rolling 7 days**, each sent before a call. |
| D5 | Name | **"Conversation cards"** internally. The child sees "Did you know?" |
| D6 | Approval timing | **At the next session, on the finished card text.** Not at the end of the session where the material was told, because the card is written afterwards and she would be agreeing to a topic, not to the words. |
| D7 | Channels | **WhatsApp and email, both, every card.** Email is the copy that always arrives, even if Meta rejects the WhatsApp send or the child has a US number (§8.1). The child can turn email off. |
| D8 | Card language | **English, labelled "in Amma's words, translated from Tamil".** Original-language quotes stay in their script with an English gloss. The read-back is in her language (accepts Stories Q-B). |
| D9 | Offers with no clear answer | **Up to 3 sessions, then the card is dropped** (status `expired`). It never goes to the child by default (answers Stories Q-A for cards). |

## 4. Non-negotiables

1. **Nothing reaches the child without her yes** to that exact card, given after hearing it read.
2. **Every sentence on a card is supported by what she said.** No invented detail, feeling, date or motive (Stories §5.1 rules 2–4 apply). Katha's own questions are never quoted.
3. **Private turns never feed a card**, now or later.
4. **Questions from the child are credited to the child,** by name. Katha never presents them as its own curiosity.
5. **Answers never go straight back to the child.** Anything she says in reply becomes material for a later card, which needs her approval like any other.
6. **The family cannot correct her.** A correction from the child is not passed on (Stories §7.5).
7. **At most one "ask" per session:** either a read-back or a relayed child question. Never while she is in the middle of a story.

## 5. Parent side

### 5.1 Sharing preference

First asked in session 2 (session 1 is for trust), at a natural pause, through a Layer 4 instruction. No card is drafted before she has said yes, so the first card can come after session 2 at the earliest.

> *"When you tell me these stories, would you like me to share some of them with Priya? I would always read them to you first."*

Her answer is classified (§9.3) into:

| Answer | `user_profiles.share_preference` | Effect |
|---|---|---|
| Yes | `share_with_approval` | Cards are drafted |
| No | `never` | No cards are drafted. The operator is told and talks to the family |
| Unclear, or she changes the subject | `unsure` | Asked once more, in session 4 or later. Still unclear after that: treated as `never` |

She can change it at any time by saying so ("don't send anything to my son", "you can share my stories"). The per-turn extraction pass picks this up (§5.2) and updates the profile. The change is logged in `consent_records` with principal `parent`.

### 5.2 "Keep this between us"

- **Dialogue (Layer 2 rule):** if she asks for something to stay private, Katha confirms in one short sentence, in her language: *"Of course. This stays between us."* It does not ask why.
- **Extraction:** the per-turn extraction JSON gains `share_restriction: none | this_turn | topic`. `topic` carries a short label ("my brother's illness"). The turn is marked `turns.is_private = true`. For `topic`, earlier turns in the session that match the label are marked too.
- **Effect:** private turns, and atoms whose only source is a private turn, are never candidates for a card. A card already in flight that draws on a newly private turn is withdrawn (status `withdrawn`).
- **Topics persist.** Private topic labels are stored on the profile (`private_topics`) and given to the card selection step, which must not pick an atom on that topic.

### 5.3 End-of-session recap

The closing exchange (`goal_met`, Layer 4) gains one instruction: name, in a sentence, what she told Katha today, and say it is kept safe. *"Today you told me about the walk to the talkies, and about Ammachi's jasmine. I've kept them safe."*

There is no question and no extra voice note, so there is no extra TTS cost. It closes her loop every session, even in weeks with no card.

## 6. Read-back and approval

### 6.1 When it is offered

A card in status `awaiting_parent` sets a Layer 4 instruction in her next session. Layer 4 is per-turn, so the cached Layers 1–3 are untouched (COST_TO_SERVE C3).

- **If her opening message is a greeting or small talk,** Katha's first reply ends by offering: *"Before we start, I've written something from what you told me on Tuesday, to send to Priya. Shall I read it to you?"*
- **If she opens by telling a story,** the offer moves to the closing exchange instead: *"Before you go…"*
- **Never mid-story.** The dialogue model decides which case applies. The instruction says so in plain words.

The dialogue model writes only the offer. **It never reads or paraphrases the card.**

### 6.2 How it is read

Her reply to the offer is classified (§9.3):

- **Yes:** the system sends one voice note through `media/tts.py`, in her pinned voice: a fixed framing line, the card's `readback_text`, and the question *"Shall I send this to Priya?"* The text is stored and was visible to the operator. It is not generated live.
- **Not now, or she carries on with something else:** no read. The offer counts towards D9's three.

### 6.3 What her answer does

Her reply after the read-back is classified into one of:

| She says | Card status | What happens |
|---|---|---|
| Yes / that's right / send it | `approved` | Scheduled (§8). Layer 4 tells the model, which confirms: *"I'll send it to Priya."* |
| A correction ("it was my uncle, not my father") | `correction` | The correction turn is added as a source. The card is rewritten and fidelity-checked, goes back to the operator, and is read back once more next session. A second correction sends it to the operator to resolve by hand |
| Don't send it / keep it | `kept_private` | Never sent. Source turns are not marked private (she may be fine with a different card from them). Katha confirms |
| Not now / unclear | stays `awaiting_parent` | Offered again (D9) |

**Conservative by design.** Only an explicit affirmative is `approved`, the same rule as `core/parent_consent.py`. "Hmm", "okay okay" while carrying on, or a laugh are `unclear`.

## 7. The card

### 7.1 When a card is drafted

After each session, in `_produce_session_outputs`, in place of `_generate_and_deliver_memory_card` when `CONVERSATION_CARDS_ENABLED` is on. A draft is made only when all of these hold:

- `share_preference = share_with_approval`.
- No other card for this family is in `draft`, `operator_review`, `awaiting_parent` or `correction`. One card in the pipeline at a time.
- Sending another card would not break D4 (counting cards `scheduled` or `sent` in the last 7 days).
- There is at least one candidate atom (below).

**Candidates:** atoms created since the last card's `source_cutoff`, with a non-empty `verbatim_quote`, not from private turns, and not on a private topic. Drawing from several sessions gives the selection step more to choose from, which is the point of a twice-a-week cadence.

### 7.2 Selection (what makes a good hook)

One LLM call (`claude-sonnet-5-5`) gets the candidates' summaries and source turn transcripts, the private topics, and the titles of the last 10 cards. It returns one atom group, or `none`. Rules in the prompt:

- **Prefer what the child probably doesn't know:** life before the child was born; the parent as a young or mischievous person; firsts; risky decisions; people the child never met.
- **Prefer an answer to a question the child asked** (§10). The card then opens *"You asked Amma which film it was…"*, which closes the child's loop.
- **Avoid:** health, money, property, family conflict, grief told in pain, anything she framed as hard to talk about. These can be beautiful stories, but they are hers to raise with her children, not a teaser's.
- **Not a repeat** of a recent card's subject.
- **`none` is a good answer.** No card beats a weak card.

### 7.3 Writing

A second call writes the card from the **raw turn transcripts** (Stories §5.1 rule 1), not the atom summaries.

| Field | Rule |
|---|---|
| `title` | Her words where possible ("The walk to the talkies"). Used in the email subject and dashboard |
| `hook` | One sentence, ≤ 30 words, starting "Did you know…". Third person, using what the child calls her (`parent_title`). One concrete detail. Holds back the ending or the why |
| `excerpt` | 1–3 sentences, ≤ 60 words, first person, cut and joined from what she said. May include one original-language quote with `original_quote_gloss` |
| `call_prompt` | One open question, ≤ 15 words, that she will enjoy answering at length. Never a quiz, never yes/no |
| `held_back_note` | For the operator only: what the card leaves out and why |
| `readback_text` | Hook, excerpt and call prompt, translated into her `preferred_language`, original-language quote kept as said |

### 7.4 Fidelity check

The consolidation fidelity step (`consolidation/pipeline.py`, `build_fidelity_prompt`) checks `hook` and `excerpt` sentence by sentence against the source transcripts: `supported`, `connective` or `unsupported`.

- Any `unsupported` sentence: one rewrite, then re-check (the existing `_MAX_FIDELITY_PASSES = 2`).
- Still unsupported: the card is dropped (`failed_fidelity`) and its atoms go back into the pool.
- The result is stored (`fidelity_json`, with source turn ids per sentence) for audit.

### 7.5 Image

A new layout in `memory_cards/generator.py`, sharing its fonts and per-script font fallback:

- 1080 × 1350 (4:5), the existing palette.
- "Did you know?" header; the hook large; the excerpt in quotes, with the original-script line and its gloss; the call prompt in a box headed "On your next call".
- Footer: "Shared with {parent_title}'s permission · translated from {language} · katha.life".

### 7.6 Operator review

A new page, `/admin/cards/view`, with the same token auth as `/admin/metrics/view` (`require_admin_token_browser`):

- Lists cards in `operator_review`, each with the image, every field, the source transcripts beside it, the fidelity result and `held_back_note`.
- Actions: **approve**, **edit** (any text field; the edited card is fidelity-checked again), **reject** (with a reason, which is kept for prompt tuning).
- On approve the card moves to `awaiting_parent`.
- An email goes to a new `OPERATOR_EMAIL` setting when a card is waiting. Target: reviewed within 24 hours.
- The page also lists pending child questions that screening held (§10.2).

## 8. Delivery

### 8.1 WhatsApp

**Template.** A new template, `katha_conversation_card_v1`. It has an image header and three quick-reply buttons. Meta rules to respect: a body may not start or end with a variable, and vague bodies are classed as Marketing. Proposed body:

> A memory {{2}} agreed to share with you, from her conversations with Katha.
> On your next call, you could ask her: {{3}}
> Reply here to send her a question of your own.

| Variable / button | Value |
|---|---|
| `{{1}}` | Image (presigned key, as for memory cards, via `TWILIO_TEMPLATE_MEDIA_HOST`) |
| `{{2}}` | `parent_title` ("Amma") |
| `{{3}}` | `call_prompt` |
| Button 1 | "I didn't know this!" |
| Button 2 | "I knew this" |
| Button 3 | "Ask her something" |

**Category: request Utility, plan for Marketing.** The honest case for Utility: the child signed up for exactly this, and each card is specific and non-promotional. Meta decides, and it may say Marketing. It matters in two ways:

- **Reach.** Meta has paused Marketing templates to US (+1) numbers since April 2025, still in place as of August 2026 with no end date. Utility still delivers. A Marketing card would never reach an NRI child on a US number. Email (§8.3) is how those families still get the card.
- **Cost.** For Indian numbers `core/pricing.py` has Meta at ₹0.115 (Utility) vs ₹0.8631 (Marketing) per template, plus Twilio's $0.005.

The category Meta assigns goes into `META_TEMPLATE_CATEGORIES`. If it is Marketing, the scheduler skips WhatsApp for +1 numbers (it would be charged and never arrive) and sends email only.

**Button taps.** A tap is an inbound message from the family number, so it opens the 24-hour window:

- "I didn't know this!" / "I knew this" → recorded on the card (`child_reaction`). Reply: *"Thank you. I'll let Amma know you enjoyed it."* (only for "didn't know"; "knew" gets *"Lovely. She'll be glad it stuck."*). A "didn't know" tap is relayed to her as a reaction (§10.3).
- "Ask her something" → *"What would you like to ask Amma? Type it or send a voice note."* The next message is the question (§10).

### 8.2 Timing

- **Call slots.** Onboarding asks the child when they usually call ("Sundays around 11, sometimes Wednesday evenings"), stored as `family_accounts.call_slots` (day of week + time) and `family_accounts.timezone`. NRI children are in their own timezone.
- **Send time:** 3 hours before the next call slot that is at least 6 hours after approval, in the child's timezone.
- **No slot within 4 days, or no slots on file:** send at 18:00 child time on the day after approval.
- **One card per slot.** A second approved card takes the next slot.
- **Cadence (D4):** never more than 2 sends in any rolling 7 days.
- The existing per-minute scheduler (`scheduler/session_initiator.py`) gains a job that sends cards whose `scheduled_for` has passed.

### 8.3 Email

Sent at the same scheduled time as the WhatsApp card, to `family_accounts.email` (the address the child logs in with), unless `card_email_enabled` is false.

- **Subject:** "A memory {parent_title} agreed to share: {title}". Specific, not teasing.
- **Body (HTML and plain text):**
  - The card image inline (CID attachment; the image is private and presigned URLs expire).
  - The hook, excerpt and call prompt again as text, for screen readers and image-blocking clients.
  - Two buttons: **"I didn't know this!"** and **"Ask {parent_title} something"**. Each is a signed link with a 30-day, single-card token. They go to a small page at `katha.life/family/card/{id}` that records the tap, or takes a typed question (§10). No login needed.
  - Footer: why they're getting this; a link to turn card emails off (also sent as a `List-Unsubscribe` header, one-click).
- **Transport:** a new `core/email.py` with `send_email_ses` moved out of `core/auth.py` and a new `send_raw_email_ses` (MIME with inline image). SES ap-south-1, as today.
- **Status honesty:** for the pilot we record `email_message_id` and `email_status = accepted`. "Accepted by SES" is not "delivered", the same lesson as memory cards. Bounce and complaint events (SES → SNS → an endpoint) are a follow-up (§16, Q3). Opens are not tracked (privacy, and Apple Mail Privacy Protection makes them meaningless). Button clicks are tracked.
- **Before sending to real families:** confirm SES production access (out of sandbox), and Easy DKIM, SPF and DMARC on `katha.life`.

## 9. Conversation changes

### 9.1 Prompt changes

| Where | Change |
|---|---|
| Layer 2 | Private-request rule (§5.2). Child-question relay rules: credit by name, in her language, accept a "no" warmly and move on, never press |
| Layer 4 | Sharing-preference ask (session 2/4, §5.1); read-back offer (§6.1); child question or reaction relay (§10.3); end-of-session recap (§5.3); the outcome of her last answer, so the model can confirm it |
| Extraction | `share_restriction` and `topic_label` (§5.2); `share_preference_change` |

Layer 4 already carries per-session instructions such as the recall anchor, so this adds no cache cost.

### 9.2 One ask per session

Each session can carry at most one of: the sharing-preference ask, a read-back, a relayed child question. Priority:

1. The sharing-preference ask, when due.
2. Whichever of read-back and child question has waited longer.

A reaction (§10.3) is not an ask and can ride along with anything.

### 9.3 Classifiers

One small module, `core/parent_answers.py`, on `claude-haiku-5-5`. It labels her reply to an offer, a read-back, or the sharing question. It gets the question asked and her transcript (any language, code-mixed) and returns one label plus the span it relied on. Labels:

- `offer`: `yes | not_now | other`
- `readback`: `approve | correction | keep_private | not_now | unclear`
- `share`: `yes | no | unclear`

It follows `parent_consent.py`'s rule: when in doubt, it is not a yes.

## 10. Child questions and reactions

### 10.1 Routing

In `webhook.py`, inbound numbers are resolved in this order:

1. **A parent's `whatsapp_number`:** today's path, unchanged.
2. **A `family_whatsapp_number`:** the new family branch (button payloads, questions).
3. **Neither:** today's unknown-number path.

**Parent wins on a clash.** Self-trial (`docs/SELF_TRIAL.md` Stage B) therefore needs a second number for the child role.

Family voice notes are transcribed with the existing STT. Email questions arrive through the card page (§8.3).

### 10.2 Screening

Each question goes through one `claude-haiku-5-5` call:

| Label | Examples | What happens | Reply to the child (free-form, inside the window) |
|---|---|---|---|
| `ok` | "Which film was it?" "What was Ammachi like?" | Queued | *"Thank you. I'll ask Amma when we next talk. If she's happy to share her answer, it will come to you as a card."* |
| `sensitive` | Property, money, inheritance, health, old family conflicts | Held for the operator, who releases or drops it | Same as `ok`. The child isn't told it was held |
| `correction` | "It was Chennai, not Kumbakonam" | Not relayed | *"Katha keeps Amma's stories in her own words, so we don't pass on corrections, but it could be a lovely thing to ask her about on your call."* |
| `reaction` | "I never knew this!" "❤️" | Relayed as a reaction (§10.3) | *"I'll let Amma know."* |
| `other` | Support questions, "stop" | Not relayed. "Stop" turns off cards for that channel | A short helpful reply, or the opt-out confirmation |

**Limits:** at most 3 questions queued per family. A fourth gets *"Amma has a few of your questions waiting already. I'll ask those first."* Questions not asked within 21 days expire.

### 10.3 Raising it with her

Through Layer 4, at a natural pause, at most one per session (§9.2):

- **Question:** *"Priya read about your walk to the talkies. She wanted to know which film it was."* Credited, in her language, then she answers or doesn't. If she declines, Katha accepts warmly and moves on. Nothing is reported back to the child.
- **Reaction:** *"Priya heard about the talkies. She said she never knew."* No question attached.

The question is marked `asked`, with the session id. Her answer is ordinary conversation. It becomes atoms, and the selection step prefers it for the next card (§7.2).

## 11. Data model

One migration.

**`user_profiles`:** add `share_preference` (`not_asked | share_with_approval | unsure | never`, default `not_asked`), `share_preference_asks` (int), `private_topics` (text[]).

**`family_accounts`:** add `child_name`, `parent_title` (what the child calls her: "Amma", "Maa"), `timezone`, `call_slots` (JSONB `[{dow, time}]`), `card_email_enabled` (bool, default true).

**`turns`:** add `is_private` (bool, default false), `private_topic` (text, nullable).

**`conversation_cards`** (new):

| Column | Notes |
|---|---|
| `id`, `user_id`, `created_at`, `updated_at` | |
| `status` | `draft, operator_review, rejected, awaiting_parent, correction, approved, kept_private, expired, withdrawn, failed_fidelity, scheduled, sent` |
| `source_atom_ids`, `source_turn_ids` (uuid[]), `source_cutoff` (timestamptz) | |
| `answers_question_id` (nullable) | |
| `title`, `hook`, `excerpt`, `original_quote`, `original_quote_gloss`, `call_prompt`, `held_back_note` | |
| `readback_text`, `readback_language` | |
| `fidelity_json` | |
| `operator_action`, `operator_edited` (bool), `operator_reason`, `operator_reviewed_at` | |
| `readback_offers` (int), `correction_round` (int) | |
| `parent_answer`, `parent_answer_turn_id`, `parent_answered_at` | |
| `scheduled_for` | |
| `image_s3_key` | |
| `wa_message_sid`, `wa_delivery_status`, `wa_error_code`, `wa_delivered_at`, `wa_skipped_reason` | |
| `email_message_id`, `email_status`, `email_sent_at` | |
| `child_reaction` (`didnt_know | knew`), `child_reaction_at`, `child_reaction_channel` | |

**`family_questions`** (new):

| Column | Notes |
|---|---|
| `id`, `user_id`, `card_id` (nullable), `created_at` | |
| `channel` | `whatsapp | email` |
| `kind` | `question | reaction` |
| `raw_text`, `transcript` | |
| `screening` | `ok | sensitive | correction | reaction | other` |
| `status` | `pending, held, released, dropped, asked, expired, not_relayed` |
| `asked_in_session_id`, `asked_at` | |

**Dashboard, deletion and export.** `GET /family/cards` returns sent conversation cards (and older memory cards). `DELETE /user/{user_id}` (`admin.py`) deletes both tables' rows and card images. `GET /family/export` includes cards (approved and sent only) and the family's own questions. Cards that are `kept_private` are deleted with everything else, and are never in the family export.

## 12. Tests and evals

**Unit (targeted, `.claude/rules/testing.md`)**

- Candidate selection excludes private turns, private topics and atoms already used. Respects the one-in-pipeline and 2-in-7-days rules.
- Card state machine: every transition in §6.3 and §7. No path reaches `scheduled` without `approved`.
- Webhook routing order (§10.1), including the parent-wins clash.
- Scheduling across timezones, slot rules, the no-slot fallback, and the +1 skip when the template is Marketing.
- Email MIME: inline image, plain-text part, `List-Unsubscribe`, signed-link expiry.
- Template variable mapping matches §8.1.
- Deletion cascade and export contents.

**Evals (new, beside TC-01–TC-11)**

| Case | Checks | Target |
|---|---|---|
| TC-12 | Sharing ask in session 2: asked once, gently; a "no" is accepted without persuasion | Rubric ≥ 75% |
| TC-13 | "Don't tell my son this": one-sentence confirmation; extraction sets `share_restriction` | Objective 100% on the flag |
| TC-14 | Read-back offer: made after a greeting; not made when she opens with a story; made at closing instead | Objective ≥ 80% |
| TC-15 | `parent_answers` on a fixture of ~40 replies (Tamil, Hindi, English, code-mixed) | **No false `approve`** (precision 100%); overall ≥ 85% |
| TC-16 | Relayed question: credits the child by name, in her language, one question, accepts a decline | Rubric ≥ 75% |
| TC-17 | Card writing on 10 real pilot transcripts: zero `unsupported` after fidelity; length limits; open call prompt | Objective 100% on fidelity |
| TC-18 | Screening fixture: sensitive questions caught | Sensitive recall ≥ 95% |

Layers 2 and 4 change, so the full regression set (TC-01–TC-11) runs too.

## 13. Metrics (amend `ADMIN_METRICS.md` §6.5)

Replace the memory-card section with:

- **Pipeline:** cards drafted; `none` rate from selection; fidelity failures; operator approve / edit / reject rates; median hours in operator review.
- **Parent:** sharing preference split; read-back offers per card; outcome split (`approved`, `correction`, `kept_private`, `expired`); median days from draft to sent.
- **Delivery:** WhatsApp delivered rate, split by template category and country code (+91 / +1 / other); WhatsApp skipped (+1 on Marketing); email accepted; email turned off.
- **Child:** "didn't know" / "knew" taps per card; question rate per card; questions by screening label; questions asked to her.
- **Did the call happen:** sessions where she brings up a child's call about a card, unprompted. Tagged by hand on the admin cards page during the pilot. It is the metric that matters most and the hardest to automate.
- **Per-family table:** last card sent, cards sent in 30 days, pending card status.

## 14. Cost

Estimates per card, before measurement. They assume Sonnet 5.5 at $2 / $10 per MTok, Haiku 5.5 at $0.10 / $0.50, ₹95.06 per USD, and Gemini TTS at ~₹1.14/min (to 31 Dec 2026).

| Item | Estimate |
|---|---|
| Selection + writing (Sonnet 5.5, ~10k in / ~1k out) | ~₹2.9 |
| Fidelity check (Sonnet 5.5, ~4k in / ~1k out) | ~₹1.7 |
| Read-back voice note (~25 s Gemini TTS) | ~₹0.5 |
| Classifiers (Haiku 5.5) | < ₹0.1 |
| WhatsApp, Indian number, Utility (Twilio ₹0.48 + Meta ₹0.115) | ~₹0.6 |
| …the same if Meta classes it Marketing | ~₹1.3 |
| Email (SES) | ~₹0.01 |
| **Per card** | **~₹5.7 (Utility) to ~₹6.5 (Marketing)** |

At 2 cards a week that is roughly ₹50–55 per family per month. It replaces the per-session memory card's template send. Each child reply adds one inbound Twilio fee (~₹0.48). Every call goes through the usage ledger (`core/usage.py`) under new operations `card_select`, `card_write`, `card_fidelity`, `card_readback_tts`, `parent_answer_classify`, `question_screen`, `card_email`. Measured numbers replace this table after the pilot.

## 15. Rollout

**Step 0: verify before building the delivery code**

- [ ] Submit `katha_conversation_card_v1` and record the category Meta gives. Check the body passes Meta's variable-placement rules.
- [ ] If a US number is available, send a test to it.
- [ ] Check the existing memory-card template's category in `META_TEMPLATE_CATEGORIES`, as a precedent.
- [ ] SES: production access, Easy DKIM, SPF and DMARC on `katha.life`.

**Step 1: build behind `CONVERSATION_CARDS_ENABLED`** (default off). Order of work in the PR: migration → classifiers and extraction fields → selection, writing and fidelity → image → admin page → read-back in the session → WhatsApp and email delivery → family routing and questions → metrics.

**Step 2: self-trial.** Krish as parent on one number, as child on a second number and email, through at least two full cycles. That means draft → review → read-back (one approval, one correction, one keep-private) → delivery on both channels → a question → relay.

**Step 3: pilot family #1.**

- Tell the child what's changing: no more daily quote card; up to two "Did you know?" cards a week, before their call, on WhatsApp and email.
- Collect `child_name`, `parent_title`, timezone and call slots with an operator script.
- Turn the flag on for that family.

**Step 4: new pilot families.** Onboarding asks for the new fields. `PILOT_RUNBOOK.md` gains the call-slot question and a line in the child briefing about what cards are and that the parent approves each one.

## 16. Risks

| Risk | Mitigation |
|---|---|
| Meta classes the card Marketing: +1 children get nothing on WhatsApp, and Meta's fee on Indian sends is ~7.5× higher (about 2.3× including Twilio's fee) | Email on every card; +1 skip so we don't pay for messages that never arrive; category visible in metrics |
| Too few cards (approval, cadence and `none` combined), so the child hears less than with today's daily card | Track draft-to-sent and the `none` rate. The recap keeps her loop closed regardless. Revisit cadence after 3 weeks |
| Read-back feels like a chore to her | One ask per session; three offers then drop; offer only at pauses; watch `not_now` rates |
| A hook misrepresents her, or reads as clickbait | Fidelity check, operator review, her approval: three gates |
| Child asks something painful | Screening, operator hold, Katha accepts a "no" without pressing |
| She is embarrassed when the child raises something | Only approved cards are sent; private turns and topics are never used |
| The translated read-back and the English card differ | Accepted for MVP (D8); labelled on the card; operator sees both |
| Operator review becomes a bottleneck | 24-hour target; email alert; the flag that turns review off once reject rates are low |
| Email goes to spam or Promotions | DKIM, SPF, DMARC; plain, specific subject; low volume |
| Number clash in self-trial | Parent wins; use a second number |

**Still open**

- **Q1.** Should a child's voice-note question be relayed as text only, or could Katha play the child's voice to her? Playing it would be powerful, but needs the child's consent and storage of their audio. Proposed: text only for this pilot.
- **Q2.** When `share_preference` becomes `never`, what does the child see? Proposed: the operator handles it by talking to the family during the pilot. Nothing automatic.
- **Q3.** SES bounce and complaint handling (SNS → endpoint → `email_status`). Proposed: a follow-up PR before the pilot grows beyond hand-recruited families.

## 17. Acceptance criteria

- [ ] No code path sends a card to the family unless the card is `approved` by the parent's classified answer to its read-back.
- [ ] Private turns and private topics never appear in any card's sources.
- [ ] Every sent card has a fidelity result with no `unsupported` sentence, and an operator action.
- [ ] Cards arrive on WhatsApp (where the category allows) and email at the scheduled time, with no more than 2 in any 7 days.
- [ ] A child's button tap, WhatsApp reply or email question is recorded, screened, acknowledged, and (when `ok` or released) raised in a later session, credited by name.
- [ ] With the flag off, behaviour matches today's, including the memory card.
- [ ] TC-12 to TC-18 meet their targets; TC-01 to TC-11 still pass.
- [ ] Deletion removes both new tables and card images; export includes sent cards.
- [ ] Step 0 checks recorded in this spec, including the category Meta assigned.
- [ ] `ADMIN_METRICS.md`, `PILOT_RUNBOOK.md`, `TECH_DESIGN.md`, `CLAUDE.md` (Critical Constraints: card approval, template category) and the privacy page updated.
