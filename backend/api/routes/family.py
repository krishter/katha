from __future__ import annotations

import math
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user
from core.freemium import FREE_SESSION_LIMIT
from models.db import get_db
from models.family_account import FamilyAccount
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.user_profile import UserProfileModel
from prompts.domains import get_domain, get_domain_sequence

router = APIRouter()


class StoryAtomResponse(BaseModel):
    id: str
    domain: str
    domain_label: str
    title: Optional[str]
    narrative: str
    who: list[str]
    what: Optional[str]
    when_approx: Optional[str]
    where_approx: Optional[str]
    why: Optional[str]
    completeness_score: int
    verbatim_quote: Optional[str]
    created_at: str


def _to_story_response(atom: StoryAtom) -> StoryAtomResponse:
    return StoryAtomResponse(
        id=str(atom.id),
        domain=atom.domain,
        domain_label=get_domain(atom.domain).name,
        title=atom.title,
        narrative=atom.narrative,
        who=list(atom.who or []),
        what=atom.what,
        when_approx=atom.when_approx,
        where_approx=atom.where_approx,
        why=atom.why,
        completeness_score=atom.completeness_score,
        verbatim_quote=atom.verbatim_quote,
        created_at=atom.created_at.isoformat(),
    )


@router.get("/family/stats")
async def get_stats(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user["user_id"]

    profile_result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()
    user_name = profile.name if profile else "Friend"

    account_result = await db.execute(
        select(FamilyAccount.plan).where(FamilyAccount.user_id == user_id)
    )
    plan = account_result.scalar_one_or_none() or "free"

    session_count_result = await db.execute(
        select(func.count(Session.id)).where(Session.user_id == user_id)
    )
    total_sessions = session_count_result.scalar_one()

    domain_counts_result = await db.execute(
        select(StoryAtom.domain, func.count(StoryAtom.id))
        .where(StoryAtom.user_id == user_id)
        .group_by(StoryAtom.domain)
    )
    counts_by_domain = dict(domain_counts_result.all())

    domain_breakdown = []
    domains_covered = 0
    total_story_atoms = 0
    for domain_id in get_domain_sequence():
        domain = get_domain(domain_id)
        story_count = counts_by_domain.get(domain_id, 0)
        total_story_atoms += story_count
        if story_count > 0:
            domains_covered += 1
        domain_breakdown.append(
            {
                "domain_id": domain_id,
                "domain_label": domain.name,
                "story_count": story_count,
                "target": domain.target_story_atoms,
            }
        )

    card_result = await db.execute(
        select(MemoryCard.image_public_url)
        .where(MemoryCard.user_id == user_id)
        .order_by(MemoryCard.created_at.desc())
        .limit(1)
    )
    latest_card_url = card_result.scalars().first()

    return {
        "user_name": user_name,
        "total_sessions": total_sessions,
        "total_story_atoms": total_story_atoms,
        "domains_covered": domains_covered,
        "domain_breakdown": domain_breakdown,
        "latest_card_url": latest_card_url,
        "plan": plan,
        "session_count": total_sessions,
        "session_limit": FREE_SESSION_LIMIT,
    }


@router.get("/family/stories")
async def list_stories(
    domain: Optional[str] = Query(default=None),
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user["user_id"]

    base_filter = [StoryAtom.user_id == user_id]
    if domain:
        base_filter.append(StoryAtom.domain == domain)

    total_result = await db.execute(
        select(func.count(StoryAtom.id)).where(*base_filter)
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(StoryAtom)
        .where(*base_filter)
        .order_by(StoryAtom.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = rows_result.scalars().all()

    return {
        "stories": [_to_story_response(atom) for atom in rows],
        "total": total,
        "page": page,
        "pages": math.ceil(total / limit) if total else 0,
    }


@router.get("/family/stories/{story_id}")
async def get_story(
    story_id: str,
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoryAtomResponse:
    try:
        story_uuid = uuid.UUID(story_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Story not found")

    result = await db.execute(select(StoryAtom).where(StoryAtom.id == story_uuid))
    atom = result.scalar_one_or_none()

    if atom is None or atom.user_id != current_user["user_id"]:
        raise HTTPException(status_code=404, detail="Story not found")

    return _to_story_response(atom)


@router.get("/family/cards")
async def list_cards(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    user_id = current_user["user_id"]

    total_result = await db.execute(
        select(func.count(MemoryCard.id)).where(MemoryCard.user_id == user_id)
    )
    total = total_result.scalar_one()

    rows_result = await db.execute(
        select(MemoryCard)
        .where(MemoryCard.user_id == user_id)
        .order_by(MemoryCard.created_at.desc())
        .offset((page - 1) * limit)
        .limit(limit)
    )
    rows = rows_result.scalars().all()

    return {
        "cards": [
            {
                "id": str(card.id),
                "verbatim_quote": card.verbatim_quote,
                "domain": card.domain,
                "image_url": card.image_public_url,
                "created_at": card.created_at.isoformat(),
            }
            for card in rows
        ],
        "total": total,
    }
