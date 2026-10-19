# Conversation cards: Step 0 checklist

**For:** `docs/specs/CONVERSATION_CARDS.md` §15 Step 0. It must be done before the self-trial sends a real card.
**Who:** Krish. Every item is an action in an external console (Twilio, Meta, AWS, DNS). None of it is done by code.
**When an item is done:** record the result in the spec's §15 checklist, and the template's category in `META_TEMPLATE_CATEGORIES`.

The code is written so that skipping this fails safe:
- With no template set, cards go by email only.
- With the category unknown, no WhatsApp card is sent to a +1 number.
- With `SES_MOCK=true`, no email leaves the server.

---

## 1. The WhatsApp template: `katha_conversation_card_v1`

### 1.1 What to create

Create it in the Twilio Console under **Messaging → Content Template Builder → Create new**:

| Field | Value |
|---|---|
| Friendly name | `katha_conversation_card_v1` |
| Language | English |
| Content type | **`twilio/card`**: a media header, a body and quick-reply buttons. Check in the builder that it offers a media URL together with up to three quick replies for WhatsApp. If it doesn't, use `twilio/media` with quick replies. |
| Category to request | **Utility** (see §1.4) |

**Media URL:** `https://<TWILIO_TEMPLATE_MEDIA_HOST>/{{1}}`
- Use the same host the memory-card template uses, the value of `TWILIO_TEMPLATE_MEDIA_HOST`. Twilio allows a variable in a media URL only after a fixed host.
- `{{1}}` is the presigned key plus query string. The adapter fills it in (`adapters/whatsapp.py`, `_template_media_suffix`).

**Body.** Copy this exactly:

```
A memory {{2}} agreed to share with you, from talking with Katha.
On your next call, you could ask: {{3}}
Reply here to send a question of your own.
```

**Buttons.** Three quick replies. The ID is what the code matches on (`cards/family_inbox.py`, `BUTTONS`), so copy the IDs exactly:

| # | Button text (max 20 characters) | ID / payload |
|---|---|---|
| 1 | `I didn't know this!` | `didnt_know` |
| 2 | `I knew this` | `knew` |
| 3 | `Send a question` | `ask` |

### 1.2 Two changes from the spec, and why

1. **"from her conversations" is now "from talking with Katha".** A template's text is fixed and can't follow the parent's pronouns, and some parents are fathers. The card itself, and every reply Katha sends the child, use the parent's title ("Amma", "Appa") and no fixed pronoun.
2. **Button 3 is "Send a question", not "Ask her something".** Same reason. When tapped, Katha replies "What would you like to ask Amma?", using the title.

If you'd rather keep the spec's wording for an all-mothers pilot, change only the text. The IDs stay the same, and the code needs no change.

### 1.3 Sample values, for Meta's review

| Variable | Sample |
|---|---|
| `{{1}}` | `cards/conv/3f2a9c1e-6b1d-4c4e-9f0e-1a2b3c4d5e6f-9eabb2c97d1f.png?X-Amz-Algorithm=AWS4-HMAC-SHA256&X-Amz-Expires=900` |
| `{{2}}` | `Amma` |
| `{{3}}` | `What do you remember about that first film?` |

The body follows Meta's variable rules:
- it doesn't start or end with a variable;
- no two variables are adjacent;
- the fixed text is much longer than the variables.

### 1.4 Asking for Utility

Paste this into the submission's notes or use case, if the form has a field for it:

> Sent only to the adult child who set up Katha for their parent, and only after the parent has heard this specific memory read back and agreed to share it with them. Each message is a single personal memory the recipient subscribed to receive, with one suggested question for their next call. There is no promotion, offer, or call to buy anything; recipients can reply STOP to end these messages.

Meta decides the category, and it may say Marketing. Either way, record what it assigns:

```
META_TEMPLATE_CATEGORIES={"HX<memory card sid>": "...", "HX<this template's sid>": "utility"}
```

**What the category changes in the code** (`cards/deliver.py`):
- **Utility:** cards go on WhatsApp to every number.
- **Marketing, or unknown:** cards to +1 numbers go by email only, because Meta has paused Marketing templates to US numbers. Indian numbers are unaffected.
- **Cost (§14):** Meta charges ₹0.115 per Utility template and ₹0.8631 per Marketing template to an Indian number, plus Twilio's $0.005.

### 1.5 After approval

- [ ] Set `TWILIO_TEMPLATE_CONVERSATION_CARD=HX…` in the server's `backend/.env`. Production refuses to boot if it's set without `TWILIO_TEMPLATE_MEDIA_HOST`.
- [ ] Add its category to `META_TEMPLATE_CATEGORIES`.
- [ ] Check the existing memory-card template's category in the same map, as a precedent (§15).
- [ ] If a US number is available, send one test card to it and record whether it arrived.
- [ ] Record the category and the date in the spec's §15 Step 0.

---

## 2. Email: SES out of the sandbox, and katha.life authenticated

Cards go from `SES_FROM_EMAIL` (default `noreply@katha.life`) through SES in **ap-south-1**. While SES is in its sandbox, mail reaches only verified addresses.

### 2.1 Production access

- [ ] In the AWS console, go to **SES (ap-south-1) → Account dashboard → Request production access**.
  - Mail type: **Transactional**.
  - Website: `https://katha.life`.
  - Use case: *"Each email is a single memory an elderly parent agreed to share with their adult child, who signed up for it. Under 10 emails a week per family, a few dozen families in the pilot. One-click unsubscribe (RFC 8058) in every message; bounces and complaints are monitored."*
  - Expected volume: under 500 a month.
- [ ] Confirm that the account's **sending quota** has gone above 200 a day. The sandbox limit is 200.

### 2.2 The domain identity, with Easy DKIM

- [ ] Go to **SES → Identities → Create identity → Domain → `katha.life`** and choose Easy DKIM, RSA 2048.
- [ ] Add the **three DKIM CNAME records** SES shows at katha.life's DNS host. They look like `xxxx._domainkey.katha.life → xxxx.dkim.amazonses.com`.
- [ ] Wait for the identity to show **Verified** and DKIM **Successful**.

DKIM matters here for two reasons:
- It is what lets Gmail and Yahoo honour `List-Unsubscribe-Post`. Without it, one-click unsubscribe is ignored.
- It keeps card emails out of spam.

### 2.3 A custom MAIL FROM domain (for SPF alignment)

- [ ] Go to **SES → the katha.life identity → Custom MAIL FROM domain → `mail.katha.life`**. Set *Behavior on MX failure* to "Use default MAIL FROM".
- [ ] Add the two records SES shows:
  - **MX** `mail.katha.life` → `10 feedback-smtp.ap-south-1.amazonses.com`
  - **TXT** `mail.katha.life` → `v=spf1 include:amazonses.com ~all`
- [ ] Don't change an existing SPF record on `katha.life` itself. With a custom MAIL FROM, SPF is checked on `mail.katha.life`.

### 2.4 DMARC

- [ ] Add **TXT** `_dmarc.katha.life` → `v=DMARC1; p=none; rua=mailto:dmarc@katha.life; adkim=r; aspf=r`
  - Start at `p=none` and watch the reports for two weeks. If everything passes, move to `p=quarantine`.
  - Point `rua` at a mailbox that exists.
- [ ] If a DMARC record already exists, edit it rather than adding a second one. Two records invalidate both.

### 2.5 Check it works

- [ ] With `SES_MOCK=false` on the server, run a test send to your own Gmail and to an Outlook address. Use a self-trial card (§15 Step 2), not a real family's.
- [ ] In Gmail, use **Show original** to check that **SPF: PASS, DKIM: PASS, DMARC: PASS**, and that the email shows an **Unsubscribe** link next to the sender.
- [ ] Check that the card image shows inline, without a "download images" prompt, and that both buttons open the `/c/…` page on `api.katha.life`.
- [ ] Check which tab it lands in (Primary or Promotions). Promotions is a known risk (§16); record what happened.

### 2.6 Not in this checklist

- **Bounce and complaint handling** (SES → SNS → an endpoint → `email_status`) is Q3 in the spec. It's a follow-up PR before the pilot grows past hand-recruited families. Until then, check the SES console's reputation page weekly.

---

## 3. Settings for the self-trial (§15 Step 2)

Set these in the server's `backend/.env`, then redeploy:

```
CONVERSATION_CARDS_ENABLED=true
CARDS_OPERATOR_REVIEW=true
OPERATOR_EMAIL=<your address>
TWILIO_TEMPLATE_CONVERSATION_CARD=HX…        # after §1; empty = email only
LLM_CARD_MODEL=claude-sonnet-5-5
LLM_CARD_EFFORT=medium
LLM_CLASSIFIER_MODEL=claude-haiku-5-5
```

Then turn cards on for your own self-trial family only. Use a second WhatsApp number for the child role, because the parent's number wins a clash (§10.1):

```
cd backend
python -m scripts.cards --user-id <your self-trial user_id> --enable \
  --child-name <name> --parent-title <what the child calls you> \
  --timezone Asia/Kolkata --slot "sun 11:00" --apply
```
