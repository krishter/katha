# Embedding strategy — why Katha has no embedding vendor

**Status:** Decided and implemented (Sprint 1, S1.5)
**Date:** 2026-08-14
**Supersedes:** the `OpenAI text-embedding-3-small` entry in `CLAUDE.md`'s Tech Stack

---

## Decision

Katha no longer calls an embeddings API. Story-atom retrieval for Layer 3 is a recency query against Postgres. The `story_atoms.embedding` column and the pgvector extension remain in place, unwritten, for a later reinstatement.

## Why OpenAI was removed

**Data residency.** The DPDP Act constraint that drives storage into AWS Mumbai applies to the same content the embedding call was sending abroad. `embed_and_store` embedded `story_atom.narrative` — the actual text of an elderly person's life history, including names, relationships, places and health details. Every atom, on every turn. Storing that in Mumbai while routing it through a US API is a residency posture that does not survive being explained.

**Single point of failure on the critical path.** `build_prior_context` ran on every voice turn and was not wrapped. When the OpenAI account exhausted its credits, every turn raised, fell through to the webhook's last-resort handler, and every user received generic fallback text instead of a conversation. The core loop was fully non-functional and the cause was a billing state at a third vendor. This is now fixed twice over: the call is gone, and the retrieval that replaced it is wrapped (S1.5.2).

**Third vendor for a job SQL was already doing.** The embedding was never used to *interpret* anything. It ranked atoms by cosine similarity against the **domain name string** — `retrieve_relevant(user_id, domain)` embedded `domain` and ranked against it. At five atoms per user, ranking by similarity-to-domain and ordering by recency select nearly the same rows, at very different cost and risk.

## Why swapping vendors was not an option

Neither of Katha's existing AI vendors sells embeddings:

- **Anthropic** has no embeddings endpoint and directs users to Voyage AI.
- **Sarvam** ships Saaras (STT), Bulbul (TTS), Sarvam-M and the 30B/105B models — no vector service.

So "consolidate to two vendors" could not be satisfied by changing providers. The options were to remove the capability or to self-host it. For pilot scale, removal wins.

## What was kept, and why

`story_atoms.embedding` (1536-dim, nullable) and the pgvector extension are deliberately retained. No migration drops them.

Nothing writes to the column now. But the argument for removal is explicitly a **pilot-scale** argument: at five story atoms per user, recency is a fine proxy for relevance. At a hundred sessions and several hundred atoms, it stops being one — the atoms most worth resurfacing will not be the most recent, and that is exactly the problem semantic search solves. Dropping the column would make that reinstatement a migration instead of a one-file change.

`retrieve_relevant` also keeps its now-unused `domain` parameter for the same reason: it is the query text a semantic implementation would embed.

## One thing measured along the way

S1.5 originally specified filtering the recency query to the session's current domain. That was implemented, measured, and rejected. Story atoms carry the domain they are *about*, which is routinely not the domain of the session that surfaced them: a childhood session produced three atoms all tagged `childhood`, while session 2 opened on `family_ancestors`. Equality filtering returned zero of twelve available threads and emptied Layer 3 — the precise continuity failure gate WS5.3 exists to catch. Retrieval is therefore not domain-scoped.

This is worth remembering when semantic retrieval returns: the domain is a poor filter, and it was a poor query too.

## Phase 2 path

Self-host an open multilingual embedding model on the AWS Mumbai / Azure India infrastructure the DPDP constraint already requires for storage:

- **BGE-M3** — strong multilingual retrieval, 100+ languages, permissive licence
- **multilingual-e5-large** — smaller, well-benchmarked
- An Indic-tuned equivalent, if one has matured by then

Self-hosting satisfies both constraints that no hosted API can satisfy together: the data never leaves the jurisdiction, and the vendor count does not increase. It also removes the per-call cost that made embedding every atom a running expense.

**Trigger for revisiting:** when a typical user passes roughly 50 story atoms, or when open threads start visibly repeating because recency keeps surfacing the same window. Whichever comes first.

## Open question deferred from S1.5.4

`recent_stories` was deleted rather than implemented. It was assembled on every turn — verbatim quotes from retrieved atoms — and rendered nowhere; `_layer3_life_context` never referenced it. Somebody intended quotes to reach Layer 3 and the wiring was never finished.

Deleting it was the low-risk call mid-compliance-sprint. But the idea has merit and should be reconsidered in Phase 2, because it is the strongest argument for bringing semantic retrieval back: selecting the most *evocative* quote to remind Katha of is a judgement cosine similarity makes better than `ORDER BY created_at DESC`. If quotes in Layer 3 measurably improve conversation quality, that is a reason to reinstate embeddings that recency cannot answer.
