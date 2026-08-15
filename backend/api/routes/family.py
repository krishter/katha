from __future__ import annotations

import math
import uuid
from datetime import datetime, timezone
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core.auth import get_current_user
from core.freemium import FREE_SESSION_LIMIT
from media import storage
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
        select(FamilyAccount.plan, FamilyAccount.onboarding_complete).where(
            FamilyAccount.user_id == user_id
        )
    )
    account_row = account_result.first()
    plan = (account_row.plan if account_row else None) or "free"
    onboarding_complete = (
        bool(account_row.onboarding_complete) if account_row else False
    )

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
        select(MemoryCard.image_s3_key)
        .where(MemoryCard.user_id == user_id)
        .order_by(MemoryCard.created_at.desc())
        .limit(1)
    )
    latest_card_key = card_result.scalars().first()
    latest_card_url = (
        await storage.generate_presigned_url(latest_card_key)
        if latest_card_key
        else None
    )

    # Counted, not inferred. The deletion confirmation names exactly what is
    # destroyed, and stats previously exposed only latest_card_url — leaving
    # the UI to either invent a card number or omit cards from the copy.
    card_count_result = await db.execute(
        select(func.count(MemoryCard.id)).where(MemoryCard.user_id == user_id)
    )
    total_memory_cards = card_count_result.scalar_one()

    return {
        # The deletion endpoint is /user/{user_id} and validates the path
        # against the caller's own JWT. The portal needs to know its own id
        # to call it at all; exposing it to the authenticated owner grants
        # nothing they do not already hold.
        "user_id": user_id,
        "user_name": user_name,
        "total_sessions": total_sessions,
        "total_story_atoms": total_story_atoms,
        "total_memory_cards": total_memory_cards,
        "domains_covered": domains_covered,
        "domain_breakdown": domain_breakdown,
        "latest_card_url": latest_card_url,
        "plan": plan,
        "session_count": total_sessions,
        "session_limit": FREE_SESSION_LIMIT,
        "onboarding_complete": onboarding_complete,
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
                "image_url": await storage.generate_presigned_url(card.image_s3_key),
                "created_at": card.created_at.isoformat(),
            }
            for card in rows
        ],
        "total": total,
    }


@router.get("/family/export")
async def export_data(
    current_user: dict = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> dict:
    """
    Everything captured about this family, as JSON.

    Offered immediately before deletion. A user who deletes their mother's
    stories because they meant to cancel a subscription is a support
    incident nobody can undo, so the destructive path has a way out that
    costs one click.

    Scoped to the caller's own user_id via the JWT, exactly like deletion —
    there is no user_id parameter to tamper with.

    Audio is deliberately excluded. Voice notes live in S3 behind
    short-lived presigned URLs, and a JSON document full of links that
    expire in fifteen minutes is worse than honest silence about them. PRD
    13.1 promises audio export too; that needs an async job producing a
    durable archive, which is Phase 2.
    """
    user_id = current_user["user_id"]

    profile_result = await db.execute(
        select(UserProfileModel).where(UserProfileModel.user_id == user_id)
    )
    profile = profile_result.scalar_one_or_none()

    sessions_result = await db.execute(
        select(Session).where(Session.user_id == user_id).order_by(Session.started_at)
    )
    sessions = sessions_result.scalars().all()

    atoms_result = await db.execute(
        select(StoryAtom)
        .where(StoryAtom.user_id == user_id)
        .order_by(StoryAtom.created_at)
    )
    atoms = atoms_result.scalars().all()

    cards_result = await db.execute(
        select(MemoryCard)
        .where(MemoryCard.user_id == user_id)
        .order_by(MemoryCard.created_at)
    )
    cards = cards_result.scalars().all()

    return {
        "exported_at": datetime.now(timezone.utc).isoformat(),
        "schema_version": "1.0",
        "audio_included": False,
        "profile": {
            "name": profile.name if profile else None,
            "preferred_language": profile.preferred_language if profile else None,
            "onboarding_context": profile.onboarding_context if profile else None,
        },
        "sessions": [
            {
                "session_number": s.session_number,
                "domain": s.domain,
                "status": s.status,
                "started_at": s.started_at.isoformat() if s.started_at else None,
                "ended_at": s.ended_at.isoformat() if s.ended_at else None,
                "exchange_count": s.exchange_count,
            }
            for s in sessions
        ],
        "stories": [
            {
                "domain": a.domain,
                "domain_label": get_domain(a.domain).name
                if a.domain in get_domain_sequence()
                else a.domain,
                "title": a.title,
                "narrative": a.narrative,
                "who": a.who,
                "what": a.what,
                "when": a.when_approx,
                "where": a.where_approx,
                "why": a.why,
                "verbatim_quote": a.verbatim_quote,
                "completeness_score": a.completeness_score,
                "created_at": a.created_at.isoformat() if a.created_at else None,
            }
            for a in atoms
        ],
        "memory_cards": [
            {
                "verbatim_quote": c.verbatim_quote,
                "domain": c.domain,
                "created_at": c.created_at.isoformat() if c.created_at else None,
            }
            for c in cards
        ],
    }
