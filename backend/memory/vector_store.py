from __future__ import annotations

import logging

from openai import AsyncOpenAI
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from models.story_atom import StoryAtom

logger = logging.getLogger(__name__)

_EMBED_MODEL = "text-embedding-3-small"
_EMBED_DIM = 1536


async def _embed(text: str) -> list[float]:
    client = AsyncOpenAI(api_key=settings.OPENAI_API_KEY)
    response = await client.embeddings.create(input=text, model=_EMBED_MODEL)
    return response.data[0].embedding


async def embed_and_store(story_atom: StoryAtom, db: AsyncSession) -> None:
    """
    Embed story_atom.narrative using text-embedding-3-small.
    Store the resulting 1536-dim vector in story_atoms.embedding for the given row.
    Idempotent — overwrites if embedding already exists.
    """
    vector = await _embed(story_atom.narrative)
    await db.execute(
        update(StoryAtom).where(StoryAtom.id == story_atom.id).values(embedding=vector)
    )
    await db.commit()
    logger.info("Stored embedding for story atom %s", story_atom.id)


async def retrieve_relevant(
    user_id: str,
    domain: str,
    top_k: int = 5,
    db: AsyncSession | None = None,
    current_session_id: str | None = None,
) -> list[StoryAtom]:
    """
    Embed the domain name as the query.
    Run cosine similarity search against story_atoms.embedding for this user.
    Returns top_k results ordered by similarity.
    Excludes story atoms from current_session_id (to avoid circular injection).
    """
    if db is None:
        return []

    query_vector = await _embed(domain)

    stmt = (
        select(StoryAtom)
        .where(StoryAtom.user_id == user_id)
        .where(StoryAtom.embedding.isnot(None))
        .order_by(StoryAtom.embedding.op("<=>")(query_vector))
        .limit(top_k)
    )
    if current_session_id is not None:
        import uuid as _uuid

        stmt = stmt.where(StoryAtom.session_id != _uuid.UUID(current_session_id))

    result = await db.execute(stmt)
    return list(result.scalars().all())
