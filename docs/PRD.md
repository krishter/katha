# Katha — Product Requirements Document

**Product:** Katha (`katha.life`)  
**Industry:** Digital Health · Mental Health & Aging Care  
**Author:** Krishnaraj CK  
**Version:** 1.0 (Foundation)  
**Date:** June 2026  
**Status:** Active — Real Product Build

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [Problem Statement](#2-problem-statement)
3. [Market Context](#3-market-context)
4. [Target Personas](#4-target-personas)
5. [Solution Overview](#5-solution-overview)
6. [User Journey](#6-user-journey)
7. [Core Features — MVP](#7-core-features--mvp)
8. [Feature Roadmap](#8-feature-roadmap)
9. [Business Model](#9-business-model)
10. [Risk Assessment](#10-risk-assessment)
11. [Success Metrics](#11-success-metrics)
12. [Launch Strategy](#12-launch-strategy)
13. [Legal, Privacy & Compliance](#13-legal-privacy--compliance)
14. [Open Questions](#14-open-questions)

> **Technical reference:** Architecture, AI system design, and evaluation framework are documented in [`TECH_DESIGN.md`](./TECH_DESIGN.md).

---

## 1. Executive Summary

Katha is a WhatsApp-first AI conversational agent that helps elderly Indians document their life stories through daily voice conversations — simultaneously combating loneliness and preserving family legacies for future generations.

**The core insight:** Reminiscence is both therapeutic and archival. A daily 15–20 minute conversation about one's past life not only reduces the loneliness epidemic among aging Indians but also captures irreplaceable stories before they are lost forever. By living inside WhatsApp — an app 85%+ of elderly Indians already use — Katha removes every barrier to adoption.

**Primary customer:** Adult children aged 35–55 (in metros or abroad) who purchase Katha as a gift for their elderly parents.  
**Primary user:** Elderly Indians aged 65+, living independently, with limited daily social interaction.  
**Core output:** A growing, searchable family archive of life stories — delivered as memory cards, audio clips, and a web dashboard — created through natural WhatsApp voice conversations guided by an empathetic AI.

**Why now:**  
India's 60+ population is growing from 153M (2024) to 347M by 2050. Sarvam.ai, now government-selected under the IndiaAI Mission, has achieved production-grade accuracy for all 22 Indian languages including code-mixing and elderly speech patterns. WhatsApp reaches 85% of Indian adults. The intersection of demographic urgency, mature AI infrastructure, and WhatsApp ubiquity creates a narrow window to build this product.

---

## 2. Problem Statement

### 2.1 Legacy Loss is Permanent and Pervasive

95%+ of elderly Indians' life stories are never documented before death or cognitive decline. The few who attempt documentation face a compounding set of barriers:

**Status Quo #1 — Oral storytelling during infrequent visits (90% of families)**  
Adult children visit parents 2–4 times a year. Stories are shared spontaneously during meals. Nothing is recorded. When the parent passes, the stories disappear with them. Adult children are left with deep, irreversible regret.

**Status Quo #2 — DIY recording attempts (20% attempt, 80% fail)**  
The family decides to "document Dad's stories" during a holiday visit. The parent feels uncomfortable being filmed. The child runs out of questions after 15 minutes. They capture one or two stories on a phone and never organize them. The files sit on a hard drive, unwatched.

**Status Quo #3 — Professional memoir services (< 5% of families)**  
Biographers charge ₹50,000–2,00,000 per project. Timelines stretch 6–18 months. Scheduling is logistically complex. Completion rates are 40–50%. This option is entirely out of reach for most families.

**Status Quo #4 — Do nothing (most common end state)**  
Life is busy. The intent to capture stories is sincere but never acted upon. By the time urgency arrives — through illness, dementia, or sudden death — the window has closed permanently.

### 2.2 Elderly Loneliness is a Public Health Crisis

India's Longitudinal Ageing Study (2023) reports 13.3% of elderly experience severe loneliness. Among widowed elderly in metros, depression rates are 3.4× higher than married peers. Traditional joint family structures are dissolving in urban areas. Adult children in Bangalore, Mumbai, and Delhi — let alone the US, UK, and Gulf — cannot provide daily companionship. Senior centers have geographic and mobility barriers. Professional therapy carries cultural stigma and cost.

The result: hundreds of millions of elderly Indians face the final chapters of their lives in isolation, with their stories untold.

### 2.3 Why This Problem is AI-Native

This problem cannot be solved without generative AI:
- **Infinite patience:** An AI can listen to the same story told slightly differently across ten sessions without fatigue or impatience.
- **Consistent follow-up:** An AI never runs out of questions and can probe for the five W's (who, what, when, where, why) across dozens of life domains.
- **Context continuity:** An AI remembers what was said in Session 1 when conducting Session 15, building an ever-richer model of the person's life.
- **Multi-format output:** An AI can transform unstructured voice conversations into structured stories, searchable archives, memory cards, and age-appropriate formats for grandchildren.
- **Always available:** Geography, time zones, and scheduling are irrelevant to an AI.

---

## 3. Market Context

### 3.1 Industry

Katha operates at the intersection of three growing industries: Digital Health (Mental Health), Senior Technology Services, and AI-powered Family Legacy Preservation.

**Market Growth (2025–2030):**
- India Digital Health: 18–25% CAGR (Grand View Research, IMARC 2024)
- Mental Health Apps: 18.5% CAGR (Grand View 2024)
- Senior Tech Services (India): 22.4% CAGR (FMI 2025)
- Conservative blended estimate for Katha's TAM growth: **18–22% CAGR**

### 3.2 Market Size

| Segment | Size | Rationale |
|---|---|---|
| **TAM** | 153M elderly Indians | All Indians 60+ (UNFPA 2024) |
| **SAM** | ~23M | Have smartphone access + at least one adult child with digital literacy |
| **SOM (Year 1)** | ~50,000 families | Metro-first (Bangalore, Mumbai, Delhi, Chennai, Hyderabad) with active NRI diaspora networks |
| **SOM (Year 3)** | ~500,000 families | Extended metros + Tier 2 cities + institutional B2B2C channels |

### 3.3 Demographic Tailwinds

- India's 60+ population will grow from 153M (2024) → 347M (2050)
- 35.42M Indian diaspora globally; 15.85M NRIs — geographically distant from aging parents
- 13.3% of elderly report severe loneliness (LASI 2017–18, PMC Study)
- Traditional joint family structures declining in urban areas (UNFPA India Ageing Report 2023)
- Post-COVID digital adoption among adults: mobile usage grew from 70% → 85% between 2020–2023

### 3.4 Technology Tailwinds

- Sarvam.ai selected (April 2025) under IndiaAI Mission as India's first sovereign AI startup
- Sarvam Saaras V3 supports all 22 scheduled Indian languages with real-time speech recognition, code-mixing, and noisy audio handling (Republic World, Feb 2026)
- WhatsApp Business API now widely available at scale; elderly WhatsApp adoption >85%

### 3.5 Headwinds

- Smartphone penetration among elderly 55+ remains below 10% nationally (~47.5% overall)
- Technology anxiety and fear of "doing something wrong"
- Cultural stigma around framing loneliness as requiring intervention
- Category creation burden: no established demand for "AI reminiscence agent" in India
- Two-sided adoption challenge: requires buy-in from elderly user AND adult child

### 3.6 Competitive Landscape

No single competitor addresses the intersection of elderly-specific mental health and legacy preservation in the Indian cultural context:

| Competitor | Strengths | Why Katha Wins |
|---|---|---|
| **Replika / Pi** (AI companionship) | Strong conversational AI | Not India-focused; no legacy capture; no therapeutic framework |
| **StoryCorps / LifeBio** (legacy tools) | Established brand; story capture | No daily engagement; family-effort-dependent; 73% app abandonment; no Indian language support |
| **Wysa / Amaha** (mental health) | Clinically validated | Youth/adult focused; text-based; no reminiscence or legacy features |
| **Papa / Birdsong** (senior engagement) | Human facilitation | Expensive; limited availability; content-consumption focused, not story extraction |
| **Generic LLMs** (ChatGPT, Gemini) | Capable AI | Elderly won't self-serve; no structure; no Indian context optimization |

**Katha's White Space:** The only solution combining (1) India-first voice AI (Sarvam.ai), (2) structured reminiscence interview protocol, (3) elderly-specific WhatsApp delivery, (4) dual benefit of mental health + legacy, and (5) family connection loop.

---

## 4. Target Personas

### 4.1 Primary Buyer — The Adult Child

**Demographics:** 35–55 years old, working professional, metro or NRI. Degree-educated. Tech-savvy. Often the primary financial decision-maker in the family.

**Context:**
- Lives in a different city or country from their parents
- Feels guilt about limited interaction with aging parents
- Has tried calling more often but finds conversations thin and repetitive ("How's your health? Eating properly?")
- Has thought "I should record Dad's stories someday" but has never started
- Treats premium subscriptions as acts of care (Spotify for parents, etc.)

**Goals:**
- Give their parent meaningful daily engagement and reduced loneliness
- Preserve stories before it's too late — especially those relating to the family's history, ancestors, hardships, and wisdom
- Feel at peace knowing they've done something meaningful even while living far away

**Trigger Events:**
- Parent's partner passes away (acute loneliness onset)
- Parent shows early signs of cognitive decline
- Family gathering where a parent shares a story the adult child had never heard
- Sibling or friend mentions guilt over not capturing their own parent's stories

**Willingness to Pay:** ₹3,000–8,000 per year, framed as a "gift for parents." Primary objection is not price but trust and proof of value — hence the freemium model.

### 4.2 Primary User — The Elderly Parent

**Demographics:** 65–85 years old, educated, living independently or with spouse. May live in the same home they've inhabited for decades. Children live elsewhere. Has significant life experience but rarely prompted to share it.

**Context:**
- Uses WhatsApp daily for family photos and messages
- Has never used a chatbot, AI, or voice assistant beyond accidental activations
- May have cultural modesty ("My life is nothing special")
- Has deep, rich life stories they've never been asked to systematically share
- Experiences loneliness especially in the evenings and mornings

**Goals:**
- Feel heard and valued
- Enjoy the conversation (primary) while building something lasting (secondary)
- Leave something meaningful for grandchildren

**Barriers:**
- Technology anxiety (fear of "doing it wrong")
- Self-deprecation about their stories' value
- Inconsistent engagement without external prompting

**Design Implication:** Katha must call the user (not wait to be opened). It must be warm, unhurried, and feel like a conversation — not a form to fill out. Immediate output (a memory card after each session) creates intrinsic reward.

### 4.3 Secondary Customer — B2B2C Channels

- **Premium Senior Living Communities:** Facilities with programming budgets seeking engagement activities. Katha can be offered as a resident benefit.
- **Corporate Employee Benefits:** IT companies and MNCs offering CSR programs for employees' parents.
- **Active Senior Citizen Associations:** RWA clubs, social groups with organized membership seeking digital programming.

*Note: Charitable old age homes are explicitly excluded as non-viable due to budget constraints.*

---

## 5. Solution Overview

### 5.1 Product Philosophy

Katha is built on a single insight: **the act of reminiscing is both therapeutic for the person doing it and archival for the family watching it unfold.**

Clinical reminiscence therapy has decades of evidence demonstrating improvement in mood, cognitive engagement, and sense of identity among elderly individuals. Katha makes reminiscence therapy accessible, daily, and culturally native — delivered through the most familiar digital interface in the elderly Indian's life: WhatsApp.

### 5.2 Core Value Proposition

**For the elderly user:** A patient, curious friend who calls every day, genuinely wants to hear your stories, remembers everything you've said, and never judges.

**For the adult child:** Peace of mind that their parent is engaged daily, combined with a growing archive of irreplaceable stories they can access anytime from anywhere.

**The "Dual Output" model:**
- **Today:** Reduced loneliness, improved daily engagement, sense of purpose
- **Tomorrow:** Complete family legacy archive — searchable stories, memory cards, audio clips, visual timelines

### 5.3 Why WhatsApp

WhatsApp is the only viable delivery channel for this product:

| Channel | Elderly Activation Rate | Rationale |
|---|---|---|
| Native app download | ~40% | App store friction, device storage anxiety, setup complexity |
| Web browser | ~25% | Elderly rarely navigate to websites independently |
| SMS | ~60% | Familiar but text-only; no voice; limited engagement |
| Smart speaker | ~15% | Low India penetration, setup complexity |
| **WhatsApp** | **~95%** | Already installed, already used daily, voice notes are familiar |

WhatsApp Business API enables:
- Outbound AI-initiated messages (Katha calls the user, not the reverse)
- Voice note exchange (user speaks naturally; Katha responds in voice)
- Text fallback for proper nouns (names, dates)
- No app download required

### 5.4 Key Differentiators

1. **India-first AI infrastructure:** Sarvam.ai's Saaras V3 and Bulbul V3 — purpose-built for Indian accents, code-mixing, and all 22 scheduled languages. Global AI models achieve <60% conversational quality with elderly Indians.

2. **WhatsApp-first zero-friction distribution:** 95% hypothesized activation rate vs. 40% industry benchmark. Viral growth through native contact sharing.

3. **Structured interview protocol:** Katha is not a generic chatbot. It follows a research-backed oral history interview framework covering 8 life domains. Each session has a goal, a structure, and produces an immediate output.

4. **Dual-purpose design:** Simultaneously addresses loneliness (daily therapeutic engagement) and legacy (story archive). Neither alone is as compelling as both together.

5. **Outbound AI model:** Katha initiates the conversation at a pre-agreed time. The user doesn't have to remember to open anything. This is the single most important engagement design decision.

---

## 6. User Journey

### 6.1 Onboarding Flow (Day 0)

**Who does what:**

1. **Adult child** visits katha.life, creates family account, pays for subscription or starts free trial.
2. **Setup wizard** collects: elderly parent's name, WhatsApp number, preferred conversation time (e.g., "10:30 AM daily"), language preference, 5–10 optional context prompts ("Dad grew up in Kolkata," "Dad was a schoolteacher").
3. **Adult child** is prompted to share context: 3–5 important life facts and a few photos (optional) to prime early conversations.
4. **Welcome message** is sent to the elderly parent's WhatsApp: a warm introduction from "Katha" — voice note + text — explaining who Katha is, that their child set this up as a gift, and that Katha will call at the agreed time tomorrow.
5. **Day 1 session** begins the next morning. Katha initiates.

### 6.2 Daily Session — Happy Path

**Duration:** 15–25 minutes  
**Frequency:** Daily (configurable; 5x/week minimum recommended)  
**Initiation:** Katha sends a WhatsApp voice note to start the session

**Sample Day 3 flow:**

> *Katha (voice note):* "Good morning, Subramaniam ji! I've been thinking about what you told me about your father's shop in Madurai. I'd love to know more about what it was like growing up in that neighbourhood. What do you remember about it?"

> *User responds:* A 3–5 minute voice note describing the street, the smells, the people.

> *Katha:* "That's beautiful. You mentioned your father's shop. What did he sell? And was the shop always in the same location, or did it move over the years?"

*[Conversation continues for 4–6 exchanges, guided by Katha toward structured story extraction]*

> *Katha (closing):* "Thank you so much for sharing that with me today. I've kept track of all of it. Tomorrow I'd love to hear about your school days in Madurai — what was your school like?"

**Post-session (automated):**
- Story extraction runs in background: facts, themes, named entities extracted
- Memory card generated: a shareable visual card with a quote from the session
- Adult child receives a WhatsApp message: "Today Subramaniam spoke about growing up in Madurai. Here's a memory from today's conversation. [Memory card image]"

### 6.3 Family Dashboard Access

The adult child (and other designated family members) can access a web dashboard at katha.life that shows:
- All captured stories organized by life domain
- Audio clips from each session
- Timeline visualization of the parent's life
- Progress: "8 of 8 life chapters covered" with story count per chapter
- Memory cards (shareable as images to WhatsApp groups)

---

## 7. Core Features — MVP

The MVP focuses on the core conversation-to-archive loop for a single elderly user and one family admin. Everything else is Phase 2+.

### 7.1 WhatsApp Conversational Agent

**Outbound session initiation:** Katha sends a voice note at pre-set time to start the day's session. If no response in 30 minutes, sends a gentle follow-up text: "No rush — I'll be here when you're ready to chat."

**Voice-first interaction:** User responds via WhatsApp voice notes. Katha processes via Sarvam Saaras V3, generates response via LLM, converts to voice via Sarvam Bulbul V3, sends as WhatsApp voice note.

**Text fallback:** For names, dates, and places, Katha occasionally requests text confirmation: "Did I hear that correctly — you graduated from Loyola College in 1968?"

**Session management:** Sessions have a defined arc (opening, theme exploration, closing). Katha gracefully ends sessions if the user seems tired or disengaged.

**Context recall:** At the start of each session, Katha references relevant content from previous sessions to demonstrate continuity and build trust.

### 7.2 Story Extraction Engine

After each session, the LLM extracts:
- **Named entities:** People (with relationships), places, institutions, events
- **Life themes:** Which of 8 domains the conversation touched (see Section 10.2)
- **Story atoms:** Individual coherent story units with extracted 5 W's (who, what, when, where, why/how)
- **Gaps:** Which themes have not yet been explored, triggering the next session's focus area

Extracted content is stored in structured JSON and indexed for the family dashboard.

### 7.3 Memory Cards

After each session, Katha auto-generates one "memory card" — a shareable image containing:
- A verbatim quote or summary from the session
- The date and life domain
- Katha branding
- Optional: a background illustration (Phase 2)

Memory cards are sent directly to the adult child's WhatsApp and are available in the family dashboard.

### 7.4 Family Dashboard (Web)

A lightweight read-only web interface at katha.life/family for:
- Viewing all captured stories by life domain
- Listening to audio clips (optional, with user consent)
- Viewing and downloading memory cards
- Seeing engagement metrics (sessions completed, domains covered, stories captured)

Access is controlled by the adult child. The elderly parent does not need to access this.

---

## 8. Feature Roadmap

### Phase 1 — MVP (Months 1–4)

**Goal:** Validate core conversation loop and story extraction with real users.

- WhatsApp outbound conversational agent (voice-in, voice-out)
- Sarvam.ai integration (Saaras V3 + Bulbul V3)
- 8-domain structured interview protocol
- Story extraction engine (structured JSON output)
- Memory card generation (static template)
- Family dashboard (web, read-only)
- Freemium model (10 free sessions → paid subscription)
- Basic onboarding wizard for adult child

**Success gate:** 40%+ of free-trial users convert to paid; 50%+ of paid users complete 30+ sessions in first 60 days.

### Phase 2 — Family Engagement (Months 5–9)

**Goal:** Bring the family into the loop beyond passive consumption.

- Multi-generational content formats: adult version, grandchild version ("Story for a 7-year-old")
- Adult child can submit questions for Katha to explore: "Please ask Dad about the 1984 Delhi trip"
- Rich memory cards with AI-illustrated backgrounds
- Audio highlight clips per story domain
- Visual life timeline (birth → present)
- WhatsApp group sharing (memory card delivery to family group)
- Multi-language output (story captured in Hindi/Tamil/Kannada, summary in English)

### Phase 3 — Scale & Legacy Products (Months 10–18)

**Goal:** Deepen the archive and expand distribution.

- Intergenerational engagement: grandchild "quest" mode ("Ask Grandpa about his school cricket team")
- Printed memory book (physical product, ~₹2,000 add-on)
- B2B2C: Senior living community dashboard and bulk licensing
- Corporate gifting channel
- Longitudinal mood tracking dashboard for family
- Provider integration: family can share mood trends with doctor or care manager

---

## 9. Business Model

### 9.1 Revenue Model

**Primary: Freemium → Annual Family Subscription (B2C)**

| Tier | Price | Features | Target |
|---|---|---|---|
| **Free Trial** | ₹0 | 10 WhatsApp conversations (~30 story atoms) | Prove engagement before asking for payment |
| **Family Plan** | ₹5,000/year (~₹417/month) | Unlimited conversations, full web archive, family dashboard, memory cards, audio clips | Mainstream buyer |
| **Family Plus** | ₹9,000/year | All above + priority processing, printed memory book (annual), WhatsApp group delivery | Premium segment |

**Pricing rationale:** ₹5,000/year is positioned as a "gift for parents" — comparable to a premium restaurant dinner or a weekend trip. Annual billing improves retention and reduces churn.

### 9.2 Secondary Revenue Channels

| Channel | Model | Price | Target |
|---|---|---|---|
| **Senior Living Communities** | Per-resident monthly license | ₹500/resident/month | Premium facilities with programming budget |
| **Corporate Employee Benefits** | Bulk annual licenses | ₹4,000/year (volume discount) | IT companies, MNCs (CSR programs) |
| **Printed Memory Book** | Per-book add-on | ₹2,000/book | Milestone events (birthdays, anniversaries) |

### 9.3 Unit Economics (Year 1 Target)

- Average Revenue Per Account: ₹5,000/year
- Free-to-paid conversion target: 30%
- Annual churn target: <20%
- Gross margin target: >70% (primary cost: API usage — Sarvam.ai ~₹1/minute, LLM calls)
- Payback period target: <12 months for acquisition cost

### 9.4 Cost Structure

Key variable costs:
- **Sarvam Saaras V3 (STT):** ~₹1/minute (estimated; verify with Sarvam pricing)
- **Sarvam Bulbul V3 (TTS):** ~₹0.50/minute
- **LLM API calls:** ~₹2–4/session (depending on context length)
- **WhatsApp Business API:** Per-conversation pricing (Meta)
- **Storage:** Minimal (audio + text per user)

Estimated variable cost per user per year (assuming 200 sessions × 20 min average): ~₹800–1,200. At ₹5,000/year subscription, gross margin ~75–84%.

---

## 10. Risk Assessment

### 10.1 Value Risks

**RISK V1: Inconsistent user engagement (P0 — Highest Priority)**

Description: Elderly users may not complete multi-session arc. Industry data shows 65–75% drop-off in memoir apps (StoryCorps: 73% abandonment after download).

Mitigations:
- **Outbound AI model:** Katha initiates at pre-agreed time — user doesn't have to remember anything
- **Family co-activation:** Adult child participates in Session 1 setup; social accountability effect
- **Progressive commitment:** Sessions start at 5 minutes and build to 20 minutes over the first week
- **Immediate gratification loop:** Memory card delivered after EVERY session — tangible reward per conversation
- **Asynchronous tolerance:** If user misses scheduled time, Katha leaves a gentle voice note; resumes next day

Success target: 50% of paid users complete 30+ sessions in 60 days (vs. ~25% industry baseline)

**RISK V2: Value perception mismatch**

Description: Elderly user does "this for the kids" (extrinsic motivation), not for themselves — reducing intrinsic engagement.

Mitigation: Product framing centers on "YOUR stories, YOUR voice." The archive is presented as a personal memory keepsake first, family heirloom second. Adult child receives the memory cards; the elderly user receives the experience of being heard.

**RISK V3: "My life was ordinary" cultural modesty**

Description: Users self-deprecate and disengage early ("Nothing special to tell").

Mitigation: Katha's question protocol targets sensory and everyday memories ("What did your street smell like?"), not achievements. Onboarding includes real examples of ordinary stories that became priceless.

### 10.2 Usability Risks

**RISK U1: WhatsApp voice note learning curve**

Description: While most elderly use WhatsApp, sending voice notes (press-and-hold) is not universally practiced.

Mitigation: Onboarding includes a 2-minute "practice session" guided by the adult child. First real session is text-based option available. Katha's voice response models the expected behavior.

**RISK U2: Privacy concerns about digital story storage**

Description: Users may be uncomfortable with stories being "on the internet" or accessible to specific family members.

Mitigation: Granular sharing controls (per-story permissions). Clear data ownership language: "Your stories are yours — export or delete anytime." No AI training on user content (explicit policy). DPDP Act compliance.

### 10.3 Feasibility Risks

**RISK F1: Context continuity across many sessions**

Description: Maintaining coherent recall across 50–100 sessions is technically complex. Vector retrieval may surface irrelevant prior content.

Mitigation: Dual-store architecture — vector store for semantic search + structured fact JSON for explicit recall. Session start always includes the structured fact store (names, dates, relationships) regardless of relevance scoring. Deduplication guard prevents redundant questions.

**RISK F2: Story extraction consistency**

Description: LLM may miss key facts in meandering or code-mixed conversations.

Mitigation: Two-pass extraction: real-time (per exchange) and post-session (full transcript). User transcript review step allows correction. 70% accuracy threshold defined as MVP "good enough"; improving with prompt iteration and user feedback loop.

**RISK F3: WhatsApp Business API limitations**

Description: Meta imposes template message requirements for outbound messages and has 24-hour conversation windows. Session initiation may require approved message templates.

Mitigation: All outbound conversation starters registered as WhatsApp Business message templates. Katha's session structure is designed to generate a user response within the 24-hour window (which resets the window). Fallback: SMS as secondary initiation channel.

### 10.4 Business Risks

**RISK B1: Unclear payment pathway (who pays, who decides)**

Description: Elderly users rarely pay for digital products independently. Adult children must be the primary payment path.

Mitigation: Freemium model removes initial barrier. Subscription is purchased and managed by adult child. Gift framing ("for your parents") removes purchase hesitation. Pilot data showing engagement within free tier provides the conversion trigger.

**RISK B2: SAM is smaller than TAM**

Description: Of 153M elderly, realistic serviceable market requires: smartphone, family support for setup, sufficient cognitive function. Actual SAM is ~23M.

Mitigation: Metro-first strategy targets highest-density addressable segment. B2B2C channel (senior living communities) provides pre-qualified, tech-supported users. SAM is still sufficient for a large business at ₹5,000 ARPU.

### 10.5 Risk Priority Matrix

| Risk | Impact | Likelihood | Priority | Mitigation Effort |
|---|---|---|---|---|
| Inconsistent engagement | High | High | P0 | Medium — design-level changes |
| WhatsApp API constraints | Medium | Medium | P1 | Low — pre-registration of templates |
| Context continuity | Medium | Medium | P1 | Medium — dual-store architecture |
| Privacy concerns | Medium | Low | P2 | Low — clear policies + controls |
| Payment pathway | Low | Medium | P2 | Low — freemium + gift framing |

---

## 11. Success Metrics

### 11.1 Framework: Four Levels of Success

**L1 — Engagement Metrics (Lead indicators)**
- Daily Active Rate: % of subscribed users who complete a session in a given day
  - Target: 60% after Month 1; 45% after Month 3 (habit formation plateau)
- Session Completion Rate: % of initiated sessions that run 10+ minutes
  - Target: 70%+
- 30-Day Retention: % of paid users still active after 30 days
  - Target: 65%+

**L2 — Story Quality Metrics (Product metrics)**
- Stories Captured per User per Month: Target 20+ story atoms
- Domain Coverage: % of users who have covered 6+ of 8 life domains by Month 3
  - Target: 50%+
- Story Completeness Score: Average 5W completeness across all story atoms
  - Target: 3.5/5 average
- Extraction Accuracy: % of extracted facts confirmed correct by user (in optional review flow)
  - Target: 85%+

**L3 — Family Impact Metrics (Outcome indicators)**
- Memory Card Opens: % of delivered memory cards opened by adult child
  - Target: 75%+ open rate
- Dashboard Monthly Active Rate: % of adult child accounts viewing dashboard ≥1x/month
  - Target: 40%+
- NPS (quarterly): Target 50+ for both elderly user and adult child
- Qualitative: % of users who report feeling "less lonely" in optional monthly check-in
  - Target: 40%+ improvement reported (qualitative, not primary metric)

**L4 — Business Metrics (Lagging indicators)**
- Free-to-paid conversion rate: Target 30%
- Annual churn rate: Target <20%
- CAC (Customer Acquisition Cost): Target <₹1,500
- CAC Payback Period: Target <12 months
- MRR Growth Rate: Target 15%+ MoM in Year 1

### 11.2 AI-Specific Metrics

- Extraction precision: True positives / (True positives + False positives) on story facts
- Extraction recall: True positives / (True positives + False negatives) on story facts
- Context recall accuracy: % of sessions where Katha correctly references prior content
- Question relevance rate: % of Katha follow-up questions rated "relevant" by human evaluators
- Evaluation set pass rate: Target 80%+ on objective cases; 75%+ on rubric-based cases

---

## 12. Launch Strategy

### 12.1 Pilot (Months 1–2)

**Who:** 20–30 families recruited from founder's network — specifically targeting NRI adult children with elderly parents in Bangalore, Chennai, or Mumbai.

**Selection criteria:**
- Elderly parent is 65+, uses WhatsApp regularly, has at least one adult child living in a different city/country
- Adult child is 35–55, smartphone-native, willing to spend 30 minutes in setup
- Family has expressed concern about parent's loneliness or about preserving family stories

**What we measure:**
- Session completion rate
- Story extraction quality (manual review of first 50 story atoms)
- Drop-off points and reasons (exit interviews at Day 7 and Day 30)
- Family dashboard engagement
- Qualitative: "What did you like / dislike? What confused you?"

**Go/No-Go criteria for Phase 1 launch:**
- ≥50% of pilot users complete 10+ sessions
- ≥70% of adult child accounts open at least one memory card
- Story extraction accuracy ≥75% (manual validation)
- No critical WhatsApp API issues or Sarvam STT failures in normal operation

### 12.2 Phase 1 Launch (Months 3–6)

**Channel:** Primarily organic + referral from pilot users. No paid acquisition in Phase 1.

**Distribution:**
- Landing page at katha.life with demo video and pilot testimonials
- WhatsApp forward campaign: pilot users share memory cards in family groups
- NRI community groups on WhatsApp and Facebook (Indian diaspora in Singapore, US, UK, UAE)
- Targeted content: short-form video of a memory card and the conversation that created it

**Pricing activation:** Freemium model goes live. 10 free sessions, then ₹5,000/year prompt.

### 12.3 Scale (Months 7–18)

- Paid acquisition begins once CAC is validated (<₹1,500 target)
- B2B2C: Pilot with 2–3 premium senior living communities in Bangalore
- PR: Story-driven media outreach (HT, TOI, Economic Times) around elderly loneliness + legacy theme
- Corporate gifting channel: 2–3 anchor corporate partnerships for Diwali/festival gifting

### 12.4 Operational Readiness Checklist

**Technical:**
- [ ] Sarvam.ai API integration tested at 200 concurrent sessions
- [ ] WhatsApp Business API account approved, templates registered
- [ ] LLM API rate limits and fallback handling configured
- [ ] Session initiation scheduling system tested for timezone reliability
- [ ] Data backup and recovery procedures documented

**Privacy & Legal:**
- [ ] Privacy policy compliant with Digital Personal Data Protection Act (DPDP Act, India)
- [ ] Data Processing Agreement with Sarvam.ai and LLM provider
- [ ] User consent flow in WhatsApp onboarding (explicit consent for voice storage)
- [ ] Data retention policy defined (user-controlled deletion)

**Support:**
- [ ] User support WhatsApp number (human escalation for elderly user issues)
- [ ] Adult child support email and FAQ
- [ ] Escalation procedure for sensitive content (grief, health disclosures)

---

## 13. Legal, Privacy & Compliance

### 13.1 Data & Privacy Principles

1. **User owns their data:** Users can export all stories and audio in standard formats (JSON, MP3) or request full deletion at any time.
2. **Granular sharing controls:** Each story atom can be marked private (user only), family (designated viewers), or public (future: community feature).
3. **No AI training on user content:** Katha explicitly commits to not using user conversations to train or fine-tune any model.
4. **Minimally invasive storage:** Audio clips are stored only with explicit user consent; default is text + extraction only.

### 13.2 Regulatory Requirements

- **Digital Personal Data Protection Act (DPDP Act, India 2023):** Katha processes sensitive personal data (life stories, family history). Requires explicit informed consent, data minimization, and user rights (access, correction, deletion). Data Fiduciary obligations apply.
- **WhatsApp Business Policy:** Compliance with Meta's WhatsApp Business Terms of Service including message template approval, opt-in requirements, and prohibited content.
- **Healthcare adjacency:** Katha is explicitly NOT a medical device or clinical service. Disclaimers should be clear: "Katha is not therapy and does not replace mental health care." If a user discloses a mental health crisis, Katha is designed to provide iCare/Vandrevala Foundation helpline information.

### 13.3 Content Moderation

- **Sensitive topic guardrails:** LLM system prompt includes explicit guidance on handling grief, loss, health disclosures, and family conflict — acknowledge warmly, do not probe, provide appropriate redirect.
- **Crisis detection:** If user language suggests acute distress or crisis, Katha pauses the session, expresses care, and provides mental health helpline information (iCall India: 9152987821).
- **Adult child notification (optional):** If crisis flag is triggered, adult child can be optionally notified (configurable at setup).

---

## 14. Open Questions

These questions require research, user testing, or external input before the relevant decisions can be finalized:

| # | Question | Who Decides | When Needed | Current Hypothesis |
|---|---|---|---|---|
| 1 | **Optimal session frequency:** Daily vs. 5x/week vs. user-configurable? | User research | Before Phase 1 launch | 5x/week with weekend off is likely optimal for habit without fatigue |
| 2 | **Optimal session duration:** 15 min vs. 20 min vs. user-paced? | User testing (pilot) | Before Phase 1 launch | Start at 10 min, let user lead; cap at 25 min |
| 3 | **Voice gender/persona for Katha:** Neutral, warm female, warm male, configurable? | User research | Before pilot | Warm female voice as default; configurable |
| 4 | **Language of interaction:** Does the AI respond in the same language the user speaks? | Engineering + UX | Phase 1 | Yes — Katha mirrors user's language mix |
| 5 | **Memory card design:** Photo of the user? AI illustration? Text-only? | Design | Before pilot | Text + contextual illustration (Phase 2); text-only for MVP |
| 6 | **Data residency:** India-only storage required for DPDP Act compliance? | Legal | Before launch | Yes — explore AWS Mumbai or Azure India regions |
| 7 | **LLM provider selection:** GPT-4o vs. Claude vs. Sarvam's native LLM | Engineering | Month 1 | GPT-4o for MVP (best instruction-following); evaluate Sarvam LLM when available |
| 8 | **Pricing sensitivity:** Is ₹5,000/year the right price for the metro NRI segment? | Market testing | Phase 1 | Pilot with ₹3,999 vs. ₹5,999 A/B test |
| 9 | **Printed memory book production partner:** Who fulfills this in India? | Business development | Phase 2 | Explore Zoomin / Picsy / Chatbooks India |
| 10 | **Clinical validation partnership:** Can we partner with a gerontologist or clinical psych to validate therapeutic framing? | Partnerships | Phase 2 | Yes — approach AIIMS geriatrics dept or NIMHANS |

---

*Document maintained by: Krishnaraj CK*  
*Next review: After pilot completion (Month 2)*  
*Questions: krishnarajck@gmail.com*
