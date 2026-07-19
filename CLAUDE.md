# Katha — Claude Code Project Context

## Project Overview

Katha (`katha.life`) is a WhatsApp-first AI conversational agent that helps elderly Indians document their life stories through daily voice conversations — simultaneously reducing loneliness and preserving family legacies.

- **Primary user:** Elderly Indians 65+, on WhatsApp
- **Primary buyer:** Adult children 35–55, metro or NRI
- **Core delivery:** Voice conversations via WhatsApp Business API → structured story archive → family dashboard

## Key Docs

- Product requirements: @docs/PRD.md
- Technical design & AI architecture: @docs/TECH_DESIGN.md
- Background research & ideation: @docs/context/Claude-Award-winning AI capstone project framework and ideation.md

## Tech Stack

- **STT:** Sarvam Saaras V3 (all 22 Indian languages, code-mixing, elderly speech)
- **TTS:** Sarvam Bulbul V3 (35+ Indian voices, emotional prosody)
- **LLM:** Claude Sonnet 4.6 (`claude-sonnet-4-6`) for MVP (evaluate Sarvam native LLM in Phase 2)
- **Messaging:** WhatsApp Business API (via Meta or Twilio/360Dialog intermediary)
- **Memory/RAG:** Pinecone or pgvector + OpenAI text-embedding-3-small
- **Frontend:** Next.js (family dashboard at katha.life/family)
- **Storage:** AWS Mumbai / Azure India (DPDP Act data residency requirement)

## Architecture

See @docs/TECH_DESIGN.md Section 1 for full system diagram and data flow.

Key flow: WhatsApp webhook → Sarvam STT → LLM (dialogue + extraction) → Sarvam TTS → WhatsApp voice note. Story extraction runs async post-session.

## AI System

- 5-layer system prompt: Persona → Therapeutic protocol → RAG-injected life context → Session state → Output format (see @docs/TECH_DESIGN.md Section 2.1)
- 8 life domains interview framework (Section 2.2)
- Story atom schema with 5W completeness scoring (Section 2.4)
- Dual-store memory: vector store (semantic) + structured fact JSON (explicit recall)

## Coding Conventions

- Language: Python backend, TypeScript/Next.js frontend
- Imports: ES modules on frontend; no CommonJS
- Tests: write tests before marking features complete
- Linting: run lint and type checks before every commit
- Never commit to `main` directly — always branch + PR

## Workflow Rules

- @.claude/rules/git.md for branching, commit, and PR conventions
- @.claude/rules/testing.md for test requirements and eval targets
- Run targeted tests, not the full suite, during implementation

## Critical Constraints

- **DPDP Act compliance:** All user data (voice, stories) requires explicit consent; user can delete all data; no AI training on user content
- **WhatsApp API:** Outbound messages require pre-approved message templates; 24-hour conversation window resets on user reply
- **Sarvam API:** STT ~₹1/min, TTS ~₹0.50/min — track API costs per session in unit economics
- **No medical claims:** Katha is not therapy; include disclaimers; crisis protocol must reference iCall India (9152987821)

## Evaluation

See @docs/TECH_DESIGN.md Section 3 for the full evaluation framework.
- TC-01 through TC-10 are the regression test cases — run on every prompt change
- Target: 80%+ objective, 75%+ rubric-based
- Use the `eval-runner` subagent: "Use eval-runner subagent to test the current prompt"

## MVP Scope (Phase 1)

WhatsApp conversational agent + story extraction + memory cards + family dashboard (read-only). Single elderly user per family account. No multi-language output yet (capture in user's language; English summary only).
