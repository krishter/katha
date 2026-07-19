# Katha — MVP Build Plan

**Product:** Katha (`katha.life`)  
**Scope:** Phase 1 MVP only (see `docs/PRD.md` Section 7)  
**Build context:** Solo developer · Sarvam API ready · WhatsApp API pending approval  
**Last updated:** June 2026

---

## Key Constraint: WhatsApp API Approval

Meta's WhatsApp Business API approval takes **1–3 weeks**. This is the single biggest external dependency. The build plan is structured so that the entire core system can be built and tested without WhatsApp — using a local adapter — while approval is in progress. WhatsApp wiring is a late phase.

**Action required immediately:** Submit WhatsApp Business API application now (via Meta or Twilio/360Dialog intermediary). Don't wait until Phase 3 to start this.

---

## Dependency Map

```
[P0: Scaffolding]
       ↓
[P1: Core Loop — CLI testable]
  Sarvam STT → LLM → Sarvam TTS
       ↓
[P2: AI Conversation System]
  System prompt · 8-domain framework · Session state
       ↓
[P3: Story Extraction + Memory]
  Story atoms · Dual-store memory · Vector RAG
       ↓
[P4: WhatsApp Integration]        ← unblocks once Meta approves
  Webhook · Voice note I/O · Session scheduler · Templates
       ↓
[P5: Memory Cards]
  Post-session generation · WhatsApp delivery
       ↓
[P6: Family Dashboard]
  Next.js · Story browser · Auth
       ↓
[P7: Onboarding + Business Layer]
  Setup wizard · Freemium gating · DPDP consent
```

---

## Phase 0 — Infrastructure & Scaffolding

**Goal:** Working dev environment; CI runs; both backend and frontend start cleanly.  
**Estimated effort:** 2–3 days  
**Blockers:** None

### Components

**Backend (Python/FastAPI)**
- `backend/` — FastAPI app with health endpoint
- `backend/config.py` — env var loading (Sarvam key, OpenAI key, etc.)
- `backend/adapters/` — interface layer for external services (STT, TTS, LLM, WhatsApp)
- `backend/tests/` — pytest setup
- `pyproject.toml` / `requirements.txt`

**Frontend (Next.js)**
- `frontend/` — Next.js app scaffold (`app/` router)
- `frontend/app/family/` — placeholder route for family dashboard
- TypeScript strict mode on

**DevOps**
- `.github/workflows/ci.yml` — lint + test on every PR
- `docker-compose.yml` — local dev (backend + postgres)
- `Makefile` — common commands (`make test`, `make lint`, `make dev`)

**Database**
- PostgreSQL with pgvector extension (local + production)
- Initial schema: `users`, `sessions`, `story_atoms`, `facts`
- Alembic for migrations

### Verification
`make dev` starts both services. `make test` passes with zero tests (empty suite is fine). `make lint` passes.

---

## Phase 1 — Core Conversation Loop

**Goal:** Audio in → transcript → LLM response → audio out. Testable via CLI or HTTP endpoint — no WhatsApp needed.  
**Estimated effort:** 3–5 days  
**Blockers:** Sarvam API key (already have)

### Components

**`backend/adapters/sarvam_stt.py`**
- `transcribe(audio_bytes, language="auto") -> TranscriptResult`
- Calls Sarvam Saaras V3 API
- Returns: text, detected language, word timestamps
- Test: pass a recorded Hindi/English voice note, assert non-empty transcript

**`backend/adapters/sarvam_tts.py`**
- `synthesize(text, voice="warm_female", language="auto") -> bytes`
- Calls Sarvam Bulbul V3 API
- Returns: MP3 audio bytes
- Test: pass a short sentence, assert audio bytes returned

**`backend/adapters/llm.py`**
- `chat(messages: list[Message], functions=None) -> LLMResponse`
- Wraps Claude Sonnet 4.6 (`claude-sonnet-4-6`) via Anthropic SDK
- Handles function calling for extraction
- Test: pass a minimal message, assert response

**`backend/core/orchestrator.py`**
- `process_voice_turn(audio_bytes, session_context) -> (response_audio, extraction_json)`
- Wires STT → LLM → TTS in sequence
- Returns audio for delivery + extraction for storage

**`backend/api/routes/conversation.py`**
- `POST /conversation/turn` — accepts audio file + session_id, returns audio + extraction JSON
- This is the local test endpoint; WhatsApp adapter will call this same core logic later

### Verification
`POST /conversation/turn` with a real voice note file returns audible, coherent response audio. Run TC-06 (code-mixed input) manually to validate Sarvam STT.

---

## Phase 2 — AI Conversation System

**Goal:** Katha converses like Katha, not like a generic chatbot. System prompt, interview structure, and session arc all in place.  
**Estimated effort:** 4–6 days  
**Blockers:** Phase 1 complete

### Components

**`backend/prompts/system_prompt.py`**
- Builds the 5-layer system prompt dynamically per session:
  1. Persona & tone
  2. Therapeutic protocol
  3. Life context (injected from memory — empty for new users)
  4. Session state & constraints (session number, domain, goal, energy signal)
  5. Output format (conversational response + extraction JSON schema)
- `build_system_prompt(user_profile, session_state, prior_context) -> str`

**`backend/prompts/domains.py`**
- 8-domain interview framework as structured data
- Entry prompts, follow-up question banks, target story atom counts per domain
- Domain sequencing logic (which domain comes next based on coverage gaps)

**`backend/core/session_manager.py`**
- `SessionState` dataclass: session number, domain, exchange count, energy signal, goal met flag
- `start_session(user_id) -> SessionState`
- `update_session(state, extraction_json) -> SessionState`
- `should_end_session(state) -> bool` — triggers on goal met OR low energy × 2 exchanges
- Persists session state to DB

**`backend/core/conversation_policy.py`**
- Pre-turn checks before LLM call:
  - Deduplication guard (don't ask what's already in fact store)
  - Topic constraint (flag if user brings up medical/financial unless they initiated)
  - Crisis detection (flag keywords → trigger crisis protocol)
- Post-turn checks:
  - Session close trigger logic

### Verification
Run all 10 evaluation test cases (TC-01 through TC-10) from `docs/TECH_DESIGN.md` Section 3.3 manually against the prompt. Target: 7/10 pass at this stage. Use `eval-runner` subagent for scoring.

---

## Phase 3 — Story Extraction & Memory

**Goal:** Every session produces structured story atoms. Every new session knows what was said before.  
**Estimated effort:** 4–5 days  
**Blockers:** Phase 2 complete; pgvector running locally

### Components

**`backend/extraction/story_extractor.py`**
- `extract(transcript, session_context) -> list[StoryAtom]`
- LLM function call: `extract_story` returns story atoms matching schema in `TECH_DESIGN.md` Section 2.4
- Completeness scorer: counts populated W fields → score 1–5
- Runs async post-session (not blocking voice response)

**`backend/extraction/entity_extractor.py`**
- `extract_entities(transcript) -> NamedEntities`
- Named people (with relationships), places, dates, institutions
- Merges into the structured fact store (upsert, not duplicate)

**`backend/memory/fact_store.py`**
- Structured JSON store per user: `{name, relationship, first_mentioned_session, details}`
- `get_facts(user_id) -> dict` — always injected into system prompt
- `update_facts(user_id, new_entities)` — called post-session

**`backend/memory/vector_store.py`**
- pgvector adapter wrapping OpenAI `text-embedding-3-small`
- `embed_and_store(story_atom)` — called post-session for each atom
- `retrieve_relevant(user_id, domain, top_k=5) -> list[StoryAtom]`
- Retrieved atoms injected into Layer 3 of system prompt at session start

**`backend/models/story_atom.py`**
- Pydantic model matching schema in `TECH_DESIGN.md` Section 2.4
- DB persistence via SQLAlchemy

### Verification
After a simulated 3-session sequence, assert: (1) story atoms are stored and retrievable, (2) fact store contains names/places mentioned, (3) Session 3 system prompt includes content from Session 1. Run TC-03 (context recall) and TC-08 (story completeness) from eval set.

---

## Phase 4 — WhatsApp Integration

**Goal:** The actual WhatsApp channel. Katha sends voice notes, receives voice notes, initiates sessions on schedule.  
**Estimated effort:** 4–6 days  
**Blockers:** Meta WhatsApp Business API approval · Phases 1–3 complete

> ⚠️ This phase cannot start until the WhatsApp API is approved. Submit the application NOW and build Phases 1–3 in parallel. Once approved, this phase slots in cleanly since the core logic is already adapter-based.

### Components

**`backend/adapters/whatsapp.py`**
- `send_voice_note(to_number, audio_bytes) -> MessageId`
- `send_text(to_number, text) -> MessageId`
- `download_voice_note(media_id) -> bytes`
- Provider: Meta Cloud API or Twilio/360Dialog — abstract behind interface so provider is swappable

**`backend/api/routes/webhook.py`**
- `POST /webhook/whatsapp` — receives incoming WhatsApp events
- Handles: voice note received, text received, message status updates
- Validates webhook signature (Meta HMAC)
- Dispatches to orchestrator

**`backend/scheduler/session_initiator.py`**
- Cron-based (APScheduler or Celery Beat): fires at user's pre-set time
- Looks up users with sessions due, sends opening voice note via WhatsApp adapter
- Uses pre-approved WhatsApp message template for session opening
- Handles 30-min no-response → sends gentle follow-up text

**WhatsApp Message Templates (register with Meta)**
- `katha_session_open` — opening voice note text (template required for outbound)
- `katha_followup` — gentle follow-up if no response in 30 min
- `katha_memory_card` — memory card delivery message

### Verification
End-to-end test: Katha initiates a session to a real test WhatsApp number → user responds with a voice note → Katha replies with a voice note. Verify the full loop with a real phone.

---

## Phase 5 — Memory Cards

**Goal:** Every session closes with a shareable memory card sent to the adult child's WhatsApp.  
**Estimated effort:** 2–3 days  
**Blockers:** Phase 3 (need story atoms) · Phase 4 (need WhatsApp delivery)

### Components

**`backend/memory_cards/generator.py`**
- `generate(session_id) -> MemoryCard`
- Selects best verbatim quote from session's story atoms (highest completeness score)
- Fills static template: quote + date + life domain label + Katha branding
- Output: PNG image (Pillow)

**`backend/memory_cards/templates/`**
- Static card template image(s) — text overlaid at runtime
- MVP: one template, no AI illustration (Phase 2 adds dynamic backgrounds)

**`backend/api/routes/post_session.py`**
- `POST /session/close` — triggered by session manager when session ends
- Calls story extractor (async), then memory card generator
- Delivers card to adult child's WhatsApp number

### Verification
After a test session, assert: PNG card generated with non-empty quote, delivered to adult child's WhatsApp number within 2 minutes of session close.

---

## Phase 6 — Family Dashboard

**Goal:** Adult child can view all captured stories, memory cards, and session progress on the web.  
**Estimated effort:** 5–7 days  
**Blockers:** Phase 3 (story atoms needed to display)

### Components

**`frontend/app/family/`**
- Auth: magic link email login (adult child only; elderly user never touches this)
- `page.tsx` — dashboard home: session count, domains covered, latest memory card
- `stories/page.tsx` — story browser filtered by life domain
- `stories/[id]/page.tsx` — individual story atom view
- `cards/page.tsx` — memory card gallery (downloadable)

**`backend/api/routes/family.py`**
- `GET /family/stories?domain=childhood` — paginated story atoms
- `GET /family/cards` — memory cards list with presigned URLs
- `GET /family/stats` — sessions completed, domains covered, story count
- Auth: JWT issued after magic link flow

**`frontend/components/`**
- `DomainProgress` — 8-domain coverage bar
- `StoryCard` — renders a single story atom
- `MemoryCardGallery` — grid of memory card images

### Verification
Seed DB with test story atoms → log in as adult child → assert all 8 domains appear, stories display correctly, memory card images load. Lighthouse accessibility score ≥ 85 (important: some adult children may use assistive tech).

---

## Phase 7 — Onboarding + Business Layer

**Goal:** A new family can sign up, set up their elderly parent, and get to Session 1 without any manual intervention.  
**Estimated effort:** 4–5 days  
**Blockers:** All prior phases · WhatsApp API approved

### Components

**`frontend/app/onboarding/`**
- Step 1: Adult child account creation (email)
- Step 2: Elderly parent profile — name, WhatsApp number, preferred language, session time
- Step 3: Context prompts — 5–10 seed facts about the parent ("Grew up in Chennai")
- Step 4: DPDP consent — explicit opt-in for voice storage, story extraction, data retention policy
- Step 5: Confirmation + "Katha will message [parent name] tomorrow at [time]"

**`backend/core/freemium.py`**
- Session counter per account
- Gate at 10: block new session initiation, trigger upgrade prompt to adult child
- `is_session_allowed(user_id) -> bool`

**`backend/api/routes/onboarding.py`**
- `POST /onboarding/family` — creates family account + elderly user profile
- `POST /onboarding/consent` — records explicit DPDP consent with timestamp
- Schedules first session initiation

**DPDP compliance checklist (must be done before any real users)**
- Consent recorded with timestamp and version of privacy policy
- Data deletion endpoint: `DELETE /user/{id}` removes all voice, transcripts, story atoms, facts
- No Sarvam/OpenAI API calls store data on their end (verify data processing agreements)
- Data residency: confirm AWS Mumbai or Azure India region for all storage

### Verification
Full onboarding flow test: create family account → complete all 5 steps → verify first session is scheduled → verify consent record written to DB → verify `DELETE /user/{id}` removes all data.

---

## MVP Go/No-Go Criteria

From `docs/PRD.md` Section 12.1 — all must pass before pilot launch:

- [ ] ≥50% of pilot users (20–30 families) complete 10+ sessions
- [ ] ≥70% of adult child accounts open at least one memory card
- [ ] Story extraction accuracy ≥75% (manual validation of first 50 story atoms)
- [ ] No critical WhatsApp API or Sarvam STT failures in normal operation
- [ ] Eval regression set: 80%+ pass on TC-01 through TC-10

---

## What Lives in SPEC.md

Once you're ready to build a specific phase, use CoWork to write a tight implementation spec into `SPEC.md`. One phase at a time. Claude Code reads SPEC.md at the start of each implementation session.

**Suggested first SPEC:** Phase 0 (Infrastructure & Scaffolding) — no external dependencies, gets the repo into a buildable state immediately.

---

*Maintained by: Krishnaraj CK*  
*Next review: After Phase 0 complete*
