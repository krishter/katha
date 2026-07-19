# Katha — Active Implementation Spec

**Phase:** 7 — Onboarding + Business Layer  
**Status:** Ready for implementation  
**References:** docs/PLAN.md Phase 7, docs/PRD.md Sections 8 and 12

---

## Goal

A new family can discover Katha, sign up, set up their elderly parent, give DPDP consent, and have their first session automatically scheduled — all without any manual intervention from the Katha team.

This phase also closes the two remaining compliance and business requirements before pilot launch: the freemium gate (10 free sessions per family) and the data deletion endpoint (DPDP Act mandate).

After Phase 7, the product is pilot-ready.

---

## Out of Scope

- Payment processing (freemium gate shows "Contact us" for upgrade — no Stripe integration in MVP)
- Multi-user family accounts (one adult child per family for pilot)
- Email marketing or drip campaigns
- In-app notifications beyond the upgrade banner

---

## New Dependencies

No new packages. Phase 6 already introduced `python-jose`, `boto3`, and SES. Phase 4 introduced APScheduler and Twilio. All reused here.

---

## DB Migrations Required

```sql
-- DPDP consent log — never fully deleted (anonymized on user deletion, not hard-deleted)
CREATE TABLE consent_records (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id VARCHAR NOT NULL,
    email_hash VARCHAR NOT NULL,          -- SHA-256 of email address (retained after deletion)
    consent_version VARCHAR NOT NULL,     -- e.g. "1.0" — bump when policy changes
    consented_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    ip_address VARCHAR,                   -- For audit trail
    user_agent VARCHAR                    -- For audit trail
);

-- Track freemium status
ALTER TABLE family_accounts ADD COLUMN plan VARCHAR NOT NULL DEFAULT 'free';
ALTER TABLE family_accounts ADD COLUMN upgraded_at TIMESTAMPTZ;

-- Track onboarding completion
ALTER TABLE family_accounts ADD COLUMN onboarding_complete BOOLEAN NOT NULL DEFAULT FALSE;
```

---

## DPDP Compliance Checklist

These must all be verifiable before any real users are onboarded. Include them as test assertions.

- [ ] Consent recorded with timestamp, policy version, IP, and user agent
- [ ] `DELETE /user/{user_id}` removes: story_atoms, memory_cards (DB + S3), facts, sessions, user_profile, magic_link_tokens, family_account
- [ ] Consent records are anonymized (email replaced with SHA-256 hash), never hard-deleted
- [ ] Raw user voice notes are never persisted — audio bytes are processed in memory and discarded
- [ ] Sarvam API: confirm in data processing agreement that audio is not retained server-side
- [ ] OpenAI API: confirm embeddings API does not train on input data (opt-out is default for API usage)
- [ ] All S3 storage confirmed in `ap-south-1` (Mumbai)
- [ ] Privacy policy v1.0 live at `katha.life/privacy` before first real user (static HTML page — not part of this spec, but must exist)

---

## Files to Create / Modify

```
backend/
├── api/routes/
│   ├── onboarding.py           # POST /onboarding/start, /onboarding/profile,
│   │                           # /onboarding/consent (NEW)
│   └── admin.py                # DELETE /user/{user_id} (NEW)
├── core/
│   └── freemium.py             # is_session_allowed(), send_upgrade_prompt() (NEW)
├── models/
│   └── consent_record.py       # SQLAlchemy model (NEW)
├── core/
│   └── session_manager.py      # UPDATE — freemium check in start_session
├── scheduler/
│   └── session_initiator.py    # UPDATE — freemium check before initiating

frontend/
├── app/
│   ├── page.tsx                # Landing page / entry point — redirect to login or onboarding (UPDATE/NEW)
│   ├── family/
│   │   ├── layout.tsx          # UPDATE — add upgrade banner if at session limit
│   │   ├── page.tsx            # UPDATE — redirect to /onboarding if not complete
│   │   └── onboarding/
│   │       └── page.tsx        # 5-step onboarding wizard (NEW)
│   └── privacy/
│       └── page.tsx            # Static privacy policy page (NEW)

tests/
├── test_onboarding.py
├── test_freemium.py
└── test_data_deletion.py
```

---

## Implementation Steps

### Step 1 — `backend/models/consent_record.py` + Alembic migration

```python
class ConsentRecord(Base):
    __tablename__ = "consent_records"
    id: UUID
    user_id: str
    email_hash: str        # SHA-256 of original email
    consent_version: str
    consented_at: datetime
    ip_address: str | None
    user_agent: str | None
```

Run `make migrate` for all schema changes in this phase.

---

### Step 2 — `backend/api/routes/onboarding.py`

Three endpoints that correspond to the three data-capture stages of the wizard. The frontend calls them in sequence.

```python
@router.post("/onboarding/start")
async def onboarding_start(email: str = Form(...), db = Depends(get_db)):
    """
    Step 1: Email registration.
    1. If family_account already exists for this email AND onboarding_complete=True:
       return {"status": "existing", "message": "Account already set up. Check your email for a login link."}
       and send a magic link (reuse Phase 6 auth.send_magic_link)
    2. If family_account exists but onboarding_complete=False:
       return {"status": "incomplete"} — frontend resumes wizard
    3. If new email:
       Create family_account(email, onboarding_complete=False, plan='free')
       Send magic link email
       Return {"status": "new"}
    All cases return 200. Cookie is set when user clicks magic link (Phase 6 flow).
    This endpoint requires no auth — it's the entry point.
    """

@router.post("/onboarding/profile")
async def onboarding_profile(
    parent_name: str = Form(...),
    whatsapp_number: str = Form(...),      # E.164 format, e.g. +919876543210
    preferred_language: str = Form(...),   # BCP-47, e.g. ta-IN
    session_time: str = Form(...),         # HH:MM in 24h IST, e.g. "09:30"
    onboarding_context: str = Form(...),   # Free text seed facts, up to 1000 chars
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    Steps 2 + 3 combined: parent profile + seed context.
    1. Validate whatsapp_number is E.164 format
    2. Validate session_time is a valid HH:MM string
    3. Upsert user_profile for current_user['user_id']:
       name, whatsapp_number, preferred_language, scheduled_time, onboarding_context
    4. Return {"status": "ok"}
    """

@router.post("/onboarding/consent")
async def onboarding_consent(
    request: Request,
    consent_given: bool = Form(...),
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    Step 4: DPDP consent.
    1. If consent_given is False: return 400 — cannot proceed without consent
    2. Record ConsentRecord:
       user_id = current_user['user_id']
       email_hash = sha256(current_user['email'])
       consent_version = "1.0"
       ip_address = request.client.host
       user_agent = request.headers.get('user-agent')
    3. Mark family_account.onboarding_complete = True
    4. Schedule first session:
       The user_profile.scheduled_time is already set.
       APScheduler will pick it up automatically on the next minute tick.
       Log: "First session scheduled for user {user_id} at {scheduled_time} IST"
    5. Return {"status": "complete", "parent_name": user_profile.name, "session_time": scheduled_time}
    """
```

**Test (`test_onboarding.py`):**
- Assert `/onboarding/start` with new email creates family_account and sends magic link
- Assert `/onboarding/start` with existing complete account returns status="existing"
- Assert `/onboarding/profile` with invalid phone number format returns 422
- Assert `/onboarding/consent` with `consent_given=False` returns 400
- Assert `/onboarding/consent` with `consent_given=True` creates ConsentRecord and sets `onboarding_complete=True`
- Assert ConsentRecord stores email_hash (SHA-256), not the raw email

---

### Step 3 — `backend/core/freemium.py`

```python
FREE_SESSION_LIMIT = 10

async def is_session_allowed(user_id: str, db) -> bool:
    """
    Count completed sessions for this user from the sessions table.
    Return True if count < FREE_SESSION_LIMIT OR family_account.plan != 'free'.
    """

async def send_upgrade_prompt(user_id: str, db) -> None:
    """
    Called when is_session_allowed returns False.
    1. Look up family_account email for this user_id
    2. Send email via SES:
       Subject: "{parent_name} has completed 10 conversations with Katha"
       Body: "Subramaniam has shared 10 wonderful sessions with Katha.
              To continue preserving their stories, upgrade to Katha Premium.
              Reply to this email or visit katha.life/upgrade to continue."
    3. Log the upgrade prompt (avoid sending duplicate emails — check if prompt was sent in last 7 days)
    """

async def get_session_count(user_id: str, db) -> int:
    """Count all sessions for this user."""
```

**Update `backend/core/session_manager.py`:**

```python
async def start_session(user_id: str, db) -> SessionState:
    # ADD at the top:
    if not await freemium.is_session_allowed(user_id, db):
        await freemium.send_upgrade_prompt(user_id, db)
        raise HTTPException(402, "Session limit reached. Please upgrade to continue.")
    # ... rest of existing start_session logic
```

**Update `backend/scheduler/session_initiator.py`:**

In `initiate_sessions`, before creating a new session:
```python
if not await freemium.is_session_allowed(user.user_id, db):
    await freemium.send_upgrade_prompt(user.user_id, db)
    logger.info(f"Skipped session initiation for {user.user_id} — session limit reached")
    continue
```

**Test (`test_freemium.py`):**
- Assert `is_session_allowed` returns True when session count is 0
- Assert `is_session_allowed` returns True when session count is 9
- Assert `is_session_allowed` returns False when session count is 10
- Assert `is_session_allowed` returns True for plan='premium' regardless of count
- Assert `send_upgrade_prompt` calls SES (mock)
- Assert `start_session` raises 402 when limit reached (mock `is_session_allowed` to return False)

---

### Step 4 — `backend/api/routes/admin.py` — Data deletion

```python
@router.delete("/user/{user_id}")
async def delete_user(
    user_id: str,
    current_user = Depends(get_current_user),
    db = Depends(get_db),
):
    """
    DPDP Act data deletion endpoint.
    Only callable by the family account linked to this user_id (JWT check).
    
    Deletion order (respect FK constraints):
    1. Fetch all memory_cards for user_id → collect S3 keys
    2. Delete S3 objects: for each s3_key, call storage.delete_media(s3_key)
    3. DELETE FROM memory_cards WHERE user_id = ?
    4. DELETE FROM story_atoms WHERE user_id = ?
    5. DELETE FROM facts WHERE user_id = ?
    6. DELETE FROM sessions WHERE user_id = ?
    7. DELETE FROM user_profiles WHERE user_id = ?
    8. DELETE FROM magic_link_tokens WHERE email = (SELECT email FROM family_accounts WHERE user_id = ?)
    9. Anonymize consent_records: UPDATE consent_records SET user_id='DELETED', ip_address=NULL, user_agent=NULL WHERE user_id = ?
       (email_hash is retained for audit — this is intentional per DPDP Act)
    10. DELETE FROM family_accounts WHERE user_id = ?
    11. Clear the katha_token cookie in the response
    12. Return {"status": "deleted", "message": "All data has been permanently removed."}
    
    If any step fails: log the error, continue with remaining steps (best-effort deletion).
    Log the full deletion event with timestamp for internal audit.
    """
```

**Auth check:** `current_user['user_id']` must match the `user_id` in the route param. A family account can only delete their own linked user, not someone else's.

**Test (`test_data_deletion.py`):**
- Assert deletion fails with 403 if JWT user_id doesn't match route user_id
- Assert deletion calls S3 delete for each memory card (mock S3)
- Assert deletion removes story_atoms, facts, sessions, user_profiles from DB (mock DB, assert correct DELETE calls)
- Assert consent_records are anonymized, not deleted (check user_id='DELETED', email_hash still present)
- Assert family_account is deleted
- Assert response clears the cookie

---

### Step 5 — Frontend: onboarding wizard

**`frontend/app/family/onboarding/page.tsx`** — Single page, 5-step wizard using React state.

```typescript
// Step state machine: 'email' | 'verify' | 'profile' | 'consent' | 'done'
// Each step is a separate view rendered within the same page — no navigation between steps.
```

**Step 1 — Email:**
- Email input + "Continue" button
- Calls `POST /onboarding/start`
- On success: advance to Step 2 regardless of status ("existing", "new", "incomplete")

**Step 2 — Check your email:**
- Static message: "We've sent a login link to {email}. Click it to continue."
- The magic link redirects to `/family/auth/verify`, which sets the cookie and redirects to `/family/onboarding` (update `GET /auth/verify` redirect target: if `onboarding_complete=False`, redirect to `/family/onboarding` instead of `/family`)
- Once the cookie is set, the wizard auto-advances to Step 3

**Step 3 — Parent profile + seed context:**
Four fields:
- Parent's name (text input)
- WhatsApp number (tel input, placeholder: +91 98765 43210)
- Preferred language (select: Hindi, Tamil, Telugu, Malayalam, Kannada, Bengali, Marathi, Gujarati, English — map to BCP-47 codes)
- Best time to call (time picker: HH:MM — IST)
- Seed context (textarea, placeholder: "E.g. Grew up in Chennai. Worked as a schoolteacher for 35 years. Has two children.")
- Calls `POST /onboarding/profile`
- On success: advance to Step 4

**Step 4 — DPDP consent:**
Show clearly (not buried in fine print):

```
Before we begin, please read and agree to the following:

✓ Katha will record voice conversations with [parent name] via WhatsApp
✓ Conversations are transcribed and stored to preserve life stories
✓ Story summaries and quotes are shared with you (the family account holder)
✓ Your family's data is stored securely in India (Mumbai)
✓ You can delete all data at any time from your account settings
✓ Katha does not use your family's data to train AI models
✓ Katha is not a medical service. For emergencies, please call 112.

[Checkbox] I have read and agree to Katha's Privacy Policy and the above terms.

[Link: Read Privacy Policy →]
```

- Checkbox must be checked to enable "I Agree" button
- Calls `POST /onboarding/consent`
- On success: advance to Step 5

**Step 5 — Done:**
- Confirmation screen: "Katha will message [parent name] tomorrow at [time] IST."
- "Go to dashboard →" button → navigates to `/family`

---

### Step 6 — Update `GET /auth/verify` redirect logic

In `backend/api/routes/auth.py`, after setting the cookie:

```python
# Look up if onboarding is complete
family_account = await db.get_family_account_by_email(email)
if not family_account.onboarding_complete:
    return RedirectResponse("/family/onboarding", status_code=302)
return RedirectResponse("/family", status_code=302)
```

---

### Step 7 — Upgrade banner in family dashboard

**Update `frontend/app/family/layout.tsx`:**

Fetch session count from backend (add `GET /family/stats` — already implemented in Phase 6, add `session_count` and `session_limit` fields if not already there).

If `session_count >= 10 AND plan == 'free'`:
```tsx
<div className="upgrade-banner">
  {parentName} has completed all 10 free sessions with Katha.
  <a href="mailto:hello@katha.life?subject=Upgrade">Contact us to continue →</a>
</div>
```

---

### Step 8 — `frontend/app/privacy/page.tsx`

A static page with the privacy policy. Content should include:
- What data is collected (voice transcriptions, story atoms, family contact details)
- How it's used (story preservation, family dashboard)
- Data residency (India, Mumbai)
- Retention period (until user requests deletion)
- Right to deletion (link to account settings or email)
- No AI training on user content
- Contact: privacy@katha.life

This is a static Next.js page — no API calls needed. Plain text / simple formatting.

---

## Verification Criteria

- [ ] `make migrate` — `consent_records` table and new columns created
- [ ] `make test` — `test_onboarding.py`, `test_freemium.py`, `test_data_deletion.py` all pass
- [ ] `make lint` — clean
- [ ] Full onboarding flow end-to-end (see below)
- [ ] Data deletion end-to-end (see below)
- [ ] Freemium gate — session 11 is blocked and upgrade email sent
- [ ] Privacy policy page live at `/privacy`

**Full onboarding smoke test:**

```
1. Navigate to katha.life/family (or localhost:3000/family)
2. Redirected to /family/login (no session)
3. Enter a fresh email address
4. Check email inbox — receive magic link
5. Click link — cookie set, redirected to /family/onboarding
6. Complete all 5 steps (use sandbox WhatsApp number for parent)
7. Land on /family dashboard
8. Verify in DB:
   - family_accounts row exists with onboarding_complete=TRUE
   - user_profiles row exists with scheduled_time set
   - consent_records row exists with email_hash (not raw email)
9. Wait for scheduled_time — verify Katha sends opening WhatsApp voice note
```

**Data deletion smoke test:**

```bash
# After completing the onboarding smoke test above:
curl -X DELETE http://localhost:8000/user/test_user_1 \
  -H "Cookie: katha_token=<your_jwt>"

# Verify in DB — all tables should be empty for this user:
psql $DATABASE_URL <<EOF
SELECT COUNT(*) FROM story_atoms WHERE user_id='test_user_1';       -- expect 0
SELECT COUNT(*) FROM memory_cards WHERE user_id='test_user_1';      -- expect 0
SELECT COUNT(*) FROM facts WHERE user_id='test_user_1';             -- expect 0
SELECT COUNT(*) FROM sessions WHERE user_id='test_user_1';          -- expect 0
SELECT COUNT(*) FROM user_profiles WHERE user_id='test_user_1';     -- expect 0
SELECT COUNT(*) FROM family_accounts WHERE user_id='test_user_1';   -- expect 0
-- Consent record should remain but anonymized:
SELECT user_id, email_hash FROM consent_records WHERE email_hash = encode(digest('test@email.com', 'sha256'), 'hex');
-- expect: user_id='DELETED', email_hash=<hash>
EOF
```

---

## MVP Go/No-Go — Final Checklist

After Phase 7, verify all PRD Section 12.1 criteria before pilot launch:

- [ ] End-to-end voice loop working on real WhatsApp numbers
- [ ] Onboarding: a new family can self-serve in under 10 minutes
- [ ] DPDP: consent recorded, deletion works, data residency confirmed (S3 ap-south-1)
- [ ] Freemium: session 11 blocked, upgrade email sent
- [ ] Eval regression: TC-01 through TC-11 pass at 80%+ (run eval-runner subagent)
- [ ] WhatsApp production number approved by Meta (separate track — not blocked on code)
- [ ] Privacy policy live at katha.life/privacy
- [ ] Crisis protocol tested: iCall India number (9152987821) appears in crisis response

---

## Notes for Claude Code

- **Consent version:** hardcode `"1.0"` for MVP. When the privacy policy changes, bump this string and add a migration to re-request consent from existing users.
- **Email hash:** use `hashlib.sha256(email.lower().encode()).hexdigest()` — lowercase before hashing to avoid case sensitivity issues.
- **Deletion is best-effort:** if S3 delete fails for one card, log the error and continue deleting DB records. Never leave DB records behind because of an S3 failure.
- **`/auth/verify` redirect:** update the existing Phase 6 route to check `onboarding_complete`. Don't break existing logged-in users who are already past onboarding — they should still go to `/family`.
- **Wizard state:** use React `useState` for the 5-step wizard — not URL params, not separate routes. This avoids partial-state issues if the user navigates back.
- **Branch name:** `feature/phase7-onboarding`
- **Commit after each numbered step.** Run `make lint` and `make test` before each commit.
