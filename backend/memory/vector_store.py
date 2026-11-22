from __future__ import annotations

import asyncio
import logging
import uuid as _uuid

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from adapters import gemini_embeddings
from config import settings
from core import usage
from models.story_atom import StoryAtom

logger = logging.getLogger(__name__)

# Two ways to pick story atoms for Layer 3.
#
# retrieve_relevant is recency: the newest atoms, whatever the parent is
# talking about. It is the whole of retrieval while EMBEDDINGS_ENABLED is
# false, and the fallback when a query embedding fails.
#
# retrieve_semantic ranks atoms by cosine distance to a query vector
# (docs/specs/SEMANTIC_RETRIEVAL.md). It finds the old atom recency has
# dropped: in the offline evaluation, recency reached 0% of targets from
# sessions 1-5, and gemini-embedding-001 reached all of them.
#
# The history: S1.5 removed an OpenAI embedding call from this module. It
# was an unguarded hard dependency on every turn, it sent life stories out
# of India, and it ranked against the bare domain name. What came back is
# behind a flag, Mumbai-only, embeds once per session from what the parent
# actually said, and degrades to recency instead of failing the turn. See
# docs/proposals/embedding-strategy.md.

# Atoms are embedded a few at a time: the model takes one input per request.
_EMBED_CONCURRENCY = 4
# The hourly sweep's cap per run (D5).
SWEEP_LIMIT = 50


async def retrieve_relevant(
    user_id: str,
    domain: str,
    top_k: int = 5,
    db: AsyncSession | None = None,
    current_session_id: str | None = None,
) -> list[StoryAtom]:
    """
    Most recent story atoms for this user, newest first, capped at top_k.
    Atoms from the current session are excluded so today's own material is
    not fed back into today's prompt.

    Deliberately NOT filtered to `domain`, though S1.5 originally specified
    that and it was measured before being rejected. Story atoms carry the
    domain they are *about*, which is routinely not the domain of the
    session that surfaced them: a childhood session yields three atoms all
    tagged `childhood`, and session 2 opens on `family_ancestors`. Filtering
    on equality returned zero of twelve available threads and emptied
    Layer 3 — exactly the continuity failure gate WS5.3 exists to catch.
    """
    if db is None:
        return []

    stmt = (
        select(StoryAtom)
        .where(StoryAtom.user_id == user_id)
        .order_by(StoryAtom.created_at.desc())
        .limit(top_k)
    )
    if current_session_id is not None:
        stmt = stmt.where(StoryAtom.session_id != _uuid.UUID(current_session_id))

    result = await db.execute(stmt)
    return list(result.scalars().all())


async def retrieve_semantic(
    user_id: str,
    query_vec: list[float],
    k: int,
    db: AsyncSession | None = None,
    current_session_id: str | None = None,
) -> list[StoryAtom]:
    """
    This user's atoms closest to `query_vec` by cosine distance, nearest
    first. Only atoms embedded by the current model count, so a vector from
    another model can never be ranked against this one. Exact scan, no
    vector index: right at pilot scale.
    """
    if db is None:
        return []
    stmt = (
        select(StoryAtom)
        .where(StoryAtom.user_id == user_id)
        .where(StoryAtom.embedding.is_not(None))
        .where(StoryAtom.embedding_model == gemini_embeddings.MODEL_TAG)
        .order_by(StoryAtom.embedding.cosine_distance(query_vec))
        .limit(k)
    )
    if current_session_id is not None:
        stmt = stmt.where(StoryAtom.session_id != _uuid.UUID(current_session_id))
    result = await db.execute(stmt)
    return list(result.scalars().all())


def atom_text(atom: StoryAtom) -> str:
    """
    What is embedded: title, narrative, and the verbatim quote in curly
    quotes, exactly as stored (D8). The same text the evaluation scored
    (scripts/embedding_eval/run_eval.py atom_text). Nothing is translated:
    in practice the narrative is an English summary and the quote is in
    the parent's own language.
    """
    parts = [atom.title or "", atom.narrative or ""]
    if atom.verbatim_quote:
        parts.append(f"“{atom.verbatim_quote}”")
    return "\n".join(p for p in parts if p)


async def embed_atoms(atoms: list[StoryAtom], db: AsyncSession) -> int:
    """
    Embed these committed atoms and store their vectors. Returns how many
    succeeded.

    Never raises. An atom that cannot be embedded is marked
    embedding_failed for the hourly sweep, and nothing else changes: its
    row, the turn's extraction and everything after it go ahead as if
    embeddings did not exist. Does nothing while EMBEDDINGS_ENABLED is
    false.
    """
    if not settings.EMBEDDINGS_ENABLED or not atoms:
        return 0
    gate = asyncio.Semaphore(_EMBED_CONCURRENCY)

    async def _one(atom: StoryAtom):
        async with gate:
            try:
                # The atom's own scope, so a sweep's ledger rows are
                # charged to the right parent and session.
                with usage.usage_scope(
                    user_id=atom.user_id,
                    session_id=atom.session_id,
                    turn_id=atom.turn_id,
                ):
                    return await gemini_embeddings.embed(
                        atom_text(atom), kind="document"
                    )
            except Exception as e:
                logger.warning(
                    "Embedding story atom %s failed (%s) — marked for the sweep",
                    atom.id,
                    getattr(e, "reason", type(e).__name__),
                )
                return None

    try:
        vectors = await asyncio.gather(*(_one(a) for a in atoms))
        done = 0
        for atom, vector in zip(atoms, vectors):
            if vector is None:
                atom.embedding_failed = True
            else:
                atom.embedding = vector
                atom.embedding_model = gemini_embeddings.MODEL_TAG
                atom.embedding_failed = False
                done += 1
            db.add(atom)
        await db.commit()
        return done
    except Exception:
        logger.exception("Storing story-atom embeddings failed")
        try:
            await db.rollback()
        except Exception:
            pass
        return 0


async def sweep_unembedded(db: AsyncSession, limit: int = SWEEP_LIMIT) -> int:
    """
    The hourly retry (D5): atoms with no vector from the current model,
    failed ones included, up to `limit`. Never-tried atoms go before failed
    ones, each oldest first, so a handful that fail every time cannot use
    up every run. Returns how many it embedded. Never raises; does nothing
    while EMBEDDINGS_ENABLED is false.
    """
    if not settings.EMBEDDINGS_ENABLED:
        return 0
    try:
        atoms = list(
            (
                await db.execute(
                    select(StoryAtom)
                    .where(
                        or_(
                            StoryAtom.embedding.is_(None),
                            StoryAtom.embedding_failed.is_(True),
                            StoryAtom.embedding_model.is_distinct_from(
                                gemini_embeddings.MODEL_TAG
                            ),
                        )
                    )
                    .order_by(
                        StoryAtom.embedding_failed.asc(), StoryAtom.created_at.asc()
                    )
                    .limit(limit)
                )
            )
            .scalars()
            .all()
        )
    except Exception:
        logger.exception("Embedding sweep could not read pending atoms")
        return 0
    return await embed_atoms(atoms, db)
