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

Nothing writes to the column now. But the argument for removal is explicitly a **pilot-scale** argument: at five story atoms per user, recency is a fine proxy for relevance. Past that it stops being one — the atoms most worth resurfacing will not be the most recent, and that is exactly the problem semantic search solves. Dropping the column would make that reinstatement a migration instead of a one-file change.

**Correction, measured after this document was first written.** An earlier draft put that ceiling at "a hundred sessions and several hundred atoms." That is too generous. `retrieve_relevant` takes `top_k=5` ordered purely by recency, so once a user has more than five atoms spread across domains, the oldest domains are starved out of Layer 3 entirely — a direct probe against seeded six-domain history confirmed `childhood` disappearing completely from what Layer 3 receives. At a daily-session, 8-domain cadence a user can cross that line by roughly **session 5–6**, i.e. inside the pilot's first two weeks, not at session 100.

This does not change the decision — the residency and single-point-of-failure arguments stand on their own, and no current eval case exercises enough domain breadth to fail on it. It does mean the trigger below is nearer than it looks, and that raising `top_k` is the cheap stopgap if threads start visibly thinning before Phase 2 lands.

`retrieve_relevant` also keeps its now-unused `domain` parameter for the same reason: it is the query text a semantic implementation would embed.

## One thing measured along the way

S1.5 originally specified filtering the recency query to the session's current domain. That was implemented, measured, and rejected. Story atoms carry the domain they are *about*, which is routinely not the domain of the session that surfaced them: a childhood session produced three atoms all tagged `childhood`, while session 2 opened on `family_ancestors`. Equality filtering returned zero of twelve available threads and emptied Layer 3 — the precise continuity failure gate WS5.3 exists to catch. Retrieval is therefore not domain-scoped.

This is worth remembering when semantic retrieval returns: the domain is a poor filter, and it was a poor query too.

## Phase 2 path

Two routes. Both keep data in India; they differ on vendor count, cost, and effort.

### Route A — Azure OpenAI in an India region *(chosen; provisioning started 2026-08-14)*

Deploy an embedding model on Azure OpenAI in an India region and point `vector_store` at it. Resolves the residency exposure and moves quota management under the same subscription as the rest of the India infrastructure, rather than a separate consumer billing account that can silently run dry.

**What it does not do: reduce vendor count.** It moves the dependency from OpenAI to Microsoft. The vendor map becomes Anthropic (LLM), Sarvam (STT/TTS), Microsoft (embeddings). Residency was the substantive risk; vendor count was the proxy for it. Recorded plainly so nobody later believes the consolidation goal was met.

### Route B — self-hosted open model *(retained)*

Self-host an open multilingual embedding model on the AWS Mumbai / Azure India infrastructure the DPDP constraint already requires for storage:

- **BGE-M3** — strong multilingual retrieval, 100+ languages, permissive licence
- **multilingual-e5-large** — smaller, well-benchmarked
- An Indic-tuned equivalent, if one has matured by then

Self-hosting satisfies both constraints that no hosted API can satisfy together: the data never leaves the jurisdiction, and the vendor count does not increase. It also removes the per-call cost that made embedding every atom a running expense. Rejected for now on effort — GPU or CPU inference capacity, deployment, monitoring, plus its own quality evaluation on code-mixed Indic text.

**Trigger for revisiting either route:** when a typical user passes roughly 50 story atoms, or when open threads start visibly repeating because recency keeps surfacing the same window. Whichever comes first.

---

## Route A provisioning — Azure OpenAI

Provisioning is started ahead of the trigger because quota approval and contractual confirmations have external turnaround. **Provisioning is not wiring.** The account can sit idle; no code changes until the trigger above is hit.

### The one thing that must not be got wrong

Azure OpenAI offers several deployment types. **Global Standard** — frequently the default, and the one with the widest model catalogue — routes inference to Azure capacity anywhere in the world. Data at rest stays in your region; **processing does not**.

Provisioning Global Standard in an India region and assuming residency would leave the exposure exactly where it was before S1.5, while adding a vendor and a migration for nothing.

The requirement is a **regional (single-region) Standard deployment** in an India region. This is a property of the *deployment*, not the resource. Verify it on the deployment blade and keep the evidence.

Expect this to constrain model choice: regional deployments carry a narrower catalogue than global ones, and reports on `text-embedding-3-small` availability in Indian regions are inconsistent — at least one indicates it is absent from Central India, with South India carrying a different set. Verify actual availability in the portal for the specific region *and* deployment type before committing.

### Steps

1. **Create the Azure OpenAI resource** in an India region — Central India or South India. Prefer proximity to the existing data footprint (S3 is `ap-south-1`, Mumbai). Record the chosen region here once decided.
2. **List models available for regional Standard deployment** in that region, in the portal. Not the general model catalogue, which reflects global availability.
3. **Deploy the embedding model** as a Standard (regional) deployment. Record the *deployment name* — the API addresses deployments, not model names.
4. **Request quota.** Default embedding TPM allocations are small and approval is not instant. Size against pilot load: one embedding per story atom written, plus one per turn for retrieval, at 10–20 families.
5. **Capture** endpoint URL, API key, deployment name, API version.
6. **Get written confirmation from the Microsoft account team** that the regional deployment carries no cross-border processing, and that prompts are not retained for abuse monitoring outside the region. The abuse-monitoring exemption is applied for separately and is not on by default. Documentation alone is not sufficient — file the written confirmation with the DPDP records.

Steps 4 and 6 are the long poles. Start them first.

### Dimension check before committing to a model

`story_atoms.embedding` is `vector(1536)`.

| Model | Dimensions | Migration needed? |
|---|---|---|
| `text-embedding-3-small` | 1536 | no |
| `text-embedding-ada-002` | 1536 | no |
| `text-embedding-3-large` | 3072 | **yes** — column type change |

If the regional catalogue only offers `text-embedding-3-large`, that is a migration, not a config change. Worth knowing at step 2 rather than step 5.

### Environment and config changes *(when wiring, not when provisioning)*

No environment changes are needed to provision. When Route A is actually wired:

`.env.example` — add a new block. There is currently no embedding entry at all; `OPENAI_API_KEY` was never in this file even when the code required it, which is its own small lesson.

```
# Phase 2 — Azure OpenAI (embeddings; India region, regional deployment only)
AZURE_OPENAI_ENDPOINT=              # https://<resource>.openai.azure.com/
AZURE_OPENAI_API_KEY=
AZURE_OPENAI_EMBEDDING_DEPLOYMENT=  # deployment name, not model name
AZURE_OPENAI_API_VERSION=
```

`backend/config.py` — add the same four as optional fields defaulting to `""`, alongside the existing Phase-4 blocks. Keep them optional: the recency path must remain the default so the app boots and runs with no embedding vendor configured. Do **not** add them to `validate_production_config`'s required list unless embeddings become load-bearing.

`backend/requirements.txt` — re-add `openai>=1.0`. The Azure client ships in the same package (`AsyncAzureOpenAI`), so this is the same dependency that S1.5 removed.

`backend/memory/vector_store.py` — construct `AsyncAzureOpenAI` with `azure_endpoint`, `api_key`, `api_version`. Calls pass the *deployment name* where `model` previously took `text-embedding-3-small`.

### Migration when wiring

Any existing embeddings are from the old direct-OpenAI account, and vectors from different deployments are not comparable. At pilot scale, re-embed everything in a one-off script rather than versioning the column. Do not attempt to preserve the old vectors — mixing embedding spaces in one column produces silently wrong retrieval rather than an error.

### Open questions

- Which India region, once regional model availability is confirmed.
- Whether `text-embedding-3-small` is available regionally, or whether a dimension change is forced.
- Written confirmation from Microsoft on cross-border processing and abuse-monitoring retention.
- Whether the DPDP consent copy needs to name Microsoft as a processor. Likely yes — it currently names no processors at all, which is a gap independent of this decision.

---

## Open question deferred from S1.5.4

`recent_stories` was deleted rather than implemented. It was assembled on every turn — verbatim quotes from retrieved atoms — and rendered nowhere; `_layer3_life_context` never referenced it. Somebody intended quotes to reach Layer 3 and the wiring was never finished.

Deleting it was the low-risk call mid-compliance-sprint. But the idea has merit and should be reconsidered in Phase 2, because it is the strongest argument for bringing semantic retrieval back: selecting the most *evocative* quote to remind Katha of is a judgement cosine similarity makes better than `ORDER BY created_at DESC`. If quotes in Layer 3 measurably improve conversation quality, that is a reason to reinstate embeddings that recency cannot answer.
