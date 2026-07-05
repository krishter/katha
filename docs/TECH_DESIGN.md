# Katha — Technical Design Document

**Product:** Katha (`katha.life`)  
**Related:** [PRD.md](./PRD.md)  
**Version:** 1.0  
**Date:** June 2026  
**Status:** Active

---

## Table of Contents

1. [Technical Architecture](#1-technical-architecture)
2. [AI System Design](#2-ai-system-design)
3. [Evaluation Framework](#3-evaluation-framework)

---

## 1. Technical Architecture

### 1.1 System Overview

```
[WhatsApp Business API]
       ↕ Voice Notes / Messages
[Katha Conversation Orchestrator]
       ↕                    ↕
[Sarvam Saaras V3]    [LLM Engine]
   (Voice → Text)   (Dialogue + Extraction)
       ↓                    ↓
[Session Memory / RAG]  [Story Database]
       ↓                    ↓
[Sarvam Bulbul V3]    [Family Dashboard API]
   (Text → Voice)      (Web Interface)
```

### 1.2 Core Components

**WhatsApp Business API Layer**
- Webhook receiver for incoming voice notes and text
- Outbound message scheduling (session initiation)
- Media handling (voice note upload/download)
- Provider: Meta WhatsApp Business API (or Twilio/360Dialog as intermediary)

**Sarvam Saaras V3 (Speech-to-Text)**
- Real-time streaming transcription
- Language auto-detection across 22 Indian languages
- Code-mixing support (English + Hindi/Tamil/etc.)
- Word-level timestamps for clip extraction
- Speaker identification for multi-speaker scenarios
- Selection rationale: Only production-validated STT for Indian accents, government-selected under IndiaAI Mission, 1M+ daily minutes at scale

**LLM Engine**
- Primary use: Dialogue generation, story extraction, context recall
- Options: GPT-4o, Claude 3.5 Sonnet, Sarvam's native LLM (when available)
- System prompt architecture: Persona + therapeutic protocol + life context + conversation state
- Function calling: `extract_story`, `identify_theme`, `generate_memory_card`, `check_gaps`

**Sarvam Bulbul V3 (Text-to-Speech)**
- 35+ voices across 11+ Indian languages (expanding to 22)
- Emotional prosody for empathetic delivery
- Telephony-grade audio (optimized for elderly hearing)
- Handles code-switching and regional pronunciation

**Session Memory & RAG**
- Per-user vector store of all session content
- Structured fact store (JSON): names, relationships, dates, places, events
- Retrieval at session start: inject relevant prior context into LLM system prompt
- Technology: Pinecone / pgvector + embedding model (OpenAI text-embedding-3-small)

**Story Database**
- Structured storage of extracted story atoms
- Schema: `user_id`, `session_id`, `domain`, `story_text`, `named_entities`, `5W_completeness_score`, `created_at`
- Search index for family dashboard

**Family Dashboard**
- Next.js web application
- Read-only view for designated family members
- Story browser by domain, timeline, memory card gallery

### 1.3 Data Flow — Session Processing

1. User voice note received via WhatsApp webhook
2. Audio forwarded to Sarvam Saaras V3 → transcript returned
3. Transcript + prior session context injected into LLM prompt
4. LLM generates: (a) conversational response and (b) story extraction JSON
5. Response text → Sarvam Bulbul V3 → audio file
6. Audio sent to user as WhatsApp voice note
7. Story extraction stored asynchronously in database
8. At session close: memory card generated, sent to adult child's WhatsApp

---

## 2. AI System Design

### 2.1 Master Prompt Architecture

The Katha conversational AI uses a layered system prompt:

```
LAYER 1: Persona & Tone
"You are Katha, a warm and curious companion for [User Name]. You are patient, 
unhurried, genuinely interested, and never judgmental. You speak in a mix of 
English and [preferred language] as feels natural. You have the manner of a 
respectful younger person listening to an elder — curious, deferential, and 
deeply engaged."

LAYER 2: Therapeutic Protocol
"Your goal is to guide [User Name] through a structured reminiscence conversation. 
Each session focuses on one life domain. You ask open, sensory questions before 
factual ones. You never rush. You celebrate ordinary details as much as dramatic 
ones. You follow-up when you hear incomplete stories. You probe for the 5 W's 
(who, what, when, where, why) without making it feel like an interrogation."

LAYER 3: Life Context (RAG-injected per session)
"What you already know about [User Name]:
- [Structured facts from previous sessions]
- [Family context provided at onboarding]
- [Open story threads from prior sessions]
Today's focus domain: [Domain]. Known gaps in this domain: [Gaps]."

LAYER 4: Session State & Constraints
"Today is Session [N]. This session's goal: at least 2 complete story atoms from 
[Domain]. Duration target: 15–20 minutes (6–8 voice note exchanges). 
End-of-session trigger: when goal is met OR if user energy appears low.
Never bring up: medical details, financial struggles (unless user initiates). 
If user brings up grief/loss: acknowledge warmly, then gently redirect."

LAYER 5: Output Format
"After each user turn, output:
1. Conversational response (for voice delivery)
2. Extraction JSON (hidden from user):
{
  'story_atoms': [...],
  'named_entities': {...},
  'themes': [...],
  'energy_signal': 'high|medium|low',
  'gaps_remaining': [...],
  'session_end_suggested': true|false
}"
```

### 2.2 Life Domains — Interview Framework

Katha's structured interview covers 8 life domains across a multi-session arc:

| # | Domain | Example Entry Prompt | Target Story Atoms |
|---|---|---|---|
| 1 | **Childhood & Home** | "Tell me about the house you grew up in — what did it look like?" | 3–5 |
| 2 | **Family & Ancestors** | "Tell me about your grandparents. What do you remember of them?" | 4–6 |
| 3 | **Education & Coming of Age** | "What was your school like? Who was a teacher you remember?" | 3–4 |
| 4 | **Career & Work Life** | "How did you end up in your career? What was your first job?" | 4–6 |
| 5 | **Love, Marriage & Family Building** | "Tell me about when you and [spouse] first met." | 3–5 |
| 6 | **Historical Events Witnessed** | "You lived through [significant event]. What do you remember of that time?" | 2–3 |
| 7 | **Hobbies, Passions & Talents** | "Was there something you were known for? A skill or hobby?" | 2–4 |
| 8 | **Wisdom & Life Lessons** | "What's something you know now that you wish you'd known at 25?" | 2–3 |

### 2.3 Conversation Design Principles

**Open before factual:** "What did your street smell like in the mornings?" before "What year did you move to Chennai?"

**Ordinary magic:** Katha is designed to surface the richness in everyday memories. "You mentioned your mother's kitchen — what did she cook on Sundays?" is more valuable than "What were your mother's achievements?"

**Graceful repetition handling:** Elderly users often repeat stories. Katha never signals frustration. Instead: "You've mentioned the flood of '72 before — today I'd love to hear what came after. Where did your family go?"

**Mood adaptation:** If the user's voice note is short, flat, or the user says "I'm tired today," Katha shortens the session: "Of course. Let's just chat for a few minutes — no pressure today. Tell me one small memory..."

**Cultural modesty reframe:** When a user says "My life was ordinary, nothing special," Katha responds: "That's exactly the kind of life I want to learn about. The everyday things — the neighbourhood, the people, the small moments — those are the most precious stories to preserve."

### 2.4 Story Extraction Engine

After each user voice note, the LLM extracts structured data in parallel with generating the conversational response:

**Story Atom Schema:**
```json
{
  "story_id": "uuid",
  "session_id": "uuid",
  "domain": "childhood",
  "title": "The Street in Madurai",
  "narrative": "User described the street outside their childhood home...",
  "who": ["father", "neighbours"],
  "what": "Daily street life and father's shop",
  "when": "circa 1955-1962",
  "where": "Madurai, Tamil Nadu",
  "why": "Context of growing up in a traditional market area",
  "completeness_score": 4,
  "verbatim_quote": "The street always smelled of jasmine and filter coffee in the mornings",
  "open_threads": ["name of father's shop", "what father sold"],
  "audio_timestamp": {"start": 12.4, "end": 48.2}
}
```

**Completeness scoring:**
- 1 point each for: Who, What, When, Where, Why/How
- Score of 3+ = publishable story atom
- Score of 1–2 = open thread to revisit in future sessions

### 2.5 Context Continuity (RAG)

**At session end:**
- All story atoms stored to vector database (embedding + metadata)
- Structured fact store updated: `{person_name: "Kamala", relationship: "sister", first_mentioned: "session_3"}`

**At session start:**
- Query vector store for semantically relevant prior content based on today's focus domain
- Inject top 5 relevant story snippets + full fact store into system prompt
- Explicit callback instruction: "Reference [specific prior content] early in today's session"

**Deduplication guard:** Before asking any question, LLM checks fact store to avoid asking about information already captured.

---

## 3. Evaluation Framework

### 3.1 What "Good" Looks Like

Katha's primary success criterion is **high-quality story extraction** — not subjective measures of warmth or empathy. This makes evaluation rigorous and objective.

| Dimension | Good | Bad | Measurement |
|---|---|---|---|
| **Factual extraction accuracy** | Correctly captures name, date, place, event | Misses or distorts key facts | Precision/recall vs. user-confirmed transcript |
| **Theme coverage** | Collects 2+ stories from target domain per session | Stays on surface, collects 0–1 | Story atoms per domain per session |
| **Story completeness** | 3+ of 5 W's captured per story atom | Only 1–2 W's, incomplete narrative | Completeness score per story atom |
| **Context continuity** | References prior session content appropriately | Asks redundant questions; ignores prior context | Recall accuracy test cases |
| **Question appropriateness** | Follow-up relates directly to user's response | Tangential question that breaks flow | Binary relevance scoring |
| **Session engagement** | User completes 6–8 exchanges, 15+ minutes | User sends 1–2 short responses, drops off | Session completion rate |

### 3.2 Test Case Structure

**Format for each test case:**
```
ID: [TC-XX]
Domain: [Life domain being tested]
Setup: [Prior session context injected]
User Input: [Transcribed voice note]
Expected Katha Output:
  - Contains: [What the response must include]
  - Avoids: [What the response must not do]
Expected Extraction:
  - Story atoms: [Expected entities extracted]
  - Completeness: [Expected score]
Pass Criteria: [Objective conditions for pass]
```

### 3.3 Evaluation Test Cases (Representative Set)

**TC-01: Factual Extraction — Basic**  
Input: "I was born in 1948 in a small village near Mysore. My father was a schoolteacher there."  
Expected extraction: `{birth_year: 1948, birth_place: "village near Mysore", father_occupation: "schoolteacher"}`  
Pass: All three facts correctly extracted.

**TC-02: Theme Identification**  
Input: A 90-second voice note about childhood games.  
Expected: Tagged as domain=`childhood`, sub-theme=`leisure/play`.  
Pass: Correct domain + at least one sub-theme.

**TC-03: Context Recall — Prior Session Reference**  
Setup: Session 1 included mention of "sister Kamala."  
In Session 5 discussing family: Katha should reference Kamala proactively.  
Pass: Response contains reference to Kamala without being explicitly told.

**TC-04: Graceful Repetition Handling**  
Setup: User mentioned "the flood of '72" in Session 3.  
Input: User brings up the flood again in Session 7.  
Expected: Katha acknowledges, redirects to an unexplored aspect.  
Pass: Response doesn't re-ask questions already answered; introduces new angle.

**TC-05: Low-Energy User Adaptation**  
Input: A very short (8-second) voice note: "I'm a bit tired today."  
Expected: Katha shortens session plan, asks one light question, does not push for full session.  
Pass: Response is brief, warm, low-pressure; session_end_suggested flag = true within 3 exchanges.

**TC-06: Code-Mixed Input**  
Input: "Humara ghar bahut bada tha, you know, a joint family — we were at least 15 people."  
Expected: Correct transcription of mixed Hindi-English; entities extracted: `{family_type: "joint", family_size: 15}`.  
Pass: Transcription accuracy >90%; entities extracted.

**TC-07: Cultural Modesty Handling**  
Input: "Nothing much to tell — I was just a simple housewife."  
Expected: Katha reframes with warmth, surfaces a specific ordinary question to unlock stories.  
Pass: Response does not accept "nothing to tell" at face value; asks a specific sensory/contextual question.

**TC-08: Story Completeness — Incomplete Story**  
Input: "There was a man in our neighbourhood who used to make the best sweets."  
Expected: Katha follows up to capture: Who (man's name/description), What (what sweets), When (what period), Where (which neighbourhood), Why (why memorable).  
Pass: At least 2 follow-up questions targeting missing W's over next 2 exchanges.

**TC-09: Session Close Trigger**  
Setup: 7 exchanges completed, 2 story atoms extracted, goal met.  
Expected: Katha closes session gracefully, previews tomorrow's domain.  
Pass: `session_end_suggested = true`; response includes preview of next session.

**TC-10: Sensitive Topic Handling — Grief**  
Input: User spontaneously mentions a deceased child.  
Expected: Katha acknowledges warmly, does not push for elaboration, gently offers to move on or stay.  
Pass: Response validates the grief; does not pivot to story-extraction mode immediately; passes human review for tone.

### 3.4 Evaluation Process

- **Manual review:** All test cases reviewed by a panel of 2 human evaluators against pass criteria
- **Automated checks:** Factual extraction, completeness scores, and domain classification via automated scoring script
- **Evaluation frequency:** Before each major prompt revision; re-run on regression set after any prompt change
- **Target:** 80%+ pass rate on objective test cases; 75%+ pass on rubric-based cases

---

*Document maintained by: Krishnaraj CK*  
*Next review: After pilot completion (Month 2)*  
*Questions: krishnarajck@gmail.com*
