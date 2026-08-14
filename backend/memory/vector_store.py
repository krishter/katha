from __future__ import annotations

import logging
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.story_atom import StoryAtom

logger = logging.getLogger(__name__)

# This module no longer embeds anything.
#
# It used to call OpenAI text-embedding-3-small to rank story atoms by
# cosine similarity against the domain name. That was removed in Sprint 1
# S1.5: the call was an unguarded hard dependency on every turn, it sent
# verbatim life-history narratives across a border the DPDP constraint
# exists to prevent, and the thing it approximated — "atoms belonging to
# this domain" — is something SQL answers exactly.
#
# `story_atoms.embedding` and the pgvector extension are deliberately
# retained. Nothing writes to them now, but at 100 sessions per user
# rather than 5, recency stops being a good proxy for relevance and
# semantic search starts earning its place again. The module keeps its
# name and its function signature so reinstating it is a one-file change.
# See docs/proposals/embedding-strategy.md.


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

    `domain` is kept in the signature because this function is the seam for
    reinstating semantic retrieval, which would use it as the query text.
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
