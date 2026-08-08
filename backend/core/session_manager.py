from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from fastapi import HTTPException
from sqlalchemy import desc, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core import freemium
from models.session import Session
from models.story_atom import StoryAtom
from prompts.domains import get_domain, get_domain_sequence, get_next_domain

logger = logging.getLogger(__name__)

_STALE_SESSION_AGE = timedelta(hours=4)


@dataclass
class SessionState:
    session_id: str
    user_id: str
    session_number: int
    domain: str
    exchange_count: int
    energy_signal: str
    goal_met: bool
    session_end_suggested: bool


def _to_state(row: Session) -> SessionState:
    return SessionState(
        session_id=str(row.id),
        user_id=row.user_id,
        session_number=row.session_number,
        domain=row.domain,
        exchange_count=row.exchange_count,
        energy_signal=row.energy_signal,
        goal_met=row.goal_met,
        session_end_suggested=row.session_end_suggested,
    )


async def _count_completed_sessions(user_id: str, db: AsyncSession) -> int:
    result = await db.execute(
        select(func.count())
        .select_from(Session)
        .where(Session.user_id == user_id)
        .where(Session.status == "completed")
    )
    return result.scalar_one()


async def _select_domain(user_id: str, db: AsyncSession) -> str:
    """
    Advance past any domain whose cumulative story-atom count across all of
    the user's sessions has met its target. Falls back to the first domain
    once every domain is covered (repeat sessions).
    """
    result = await db.execute(
        select(StoryAtom.domain, func.count())
        .where(StoryAtom.user_id == user_id)
        .group_by(StoryAtom.domain)
    )
    counts = dict(result.all())
    covered = [
        domain_id
        for domain_id in get_domain_sequence()
        if counts.get(domain_id, 0) >= get_domain(domain_id).target_story_atoms
    ]
    return get_next_domain(covered).id


async def start_session(user_id: str, db: AsyncSession) -> SessionState:
    """
    Create a new session record. session_number advances past the user's
    completed sessions; domain advances past any domain whose target has
    been met (see _select_domain).
    """
    if not await freemium.is_session_allowed(user_id, db):
        await freemium.send_upgrade_prompt(user_id, db)
        raise HTTPException(
            status_code=402, detail="Session limit reached. Please upgrade to continue."
        )

    session_number = await _count_completed_sessions(user_id, db) + 1
    domain = await _select_domain(user_id, db)

    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=session_number,
        domain=domain,
        exchange_count=0,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
        status="active",
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info(
        "Started session %s for user %s: number=%d domain=%s",
        session.id,
        user_id,
        session_number,
        domain,
    )
    return _to_state(session)


async def get_session(session_id: str, db: AsyncSession) -> SessionState:
    """Load session state from DB."""
    result = await db.execute(
        select(Session).where(Session.id == uuid.UUID(session_id))
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Session not found: {session_id}")
    return _to_state(row)


async def update_session(
    session_id: str,
    extraction_json: dict,
    db: AsyncSession,
) -> SessionState:
    """Update session state from extraction JSON, persist to DB."""
    result = await db.execute(
        select(Session).where(Session.id == uuid.UUID(session_id))
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Session not found: {session_id}")

    row.exchange_count += 1
    row.energy_signal = extraction_json.get("energy_signal", row.energy_signal)
    row.session_end_suggested = extraction_json.get(
        "session_end_suggested", row.session_end_suggested
    )

    # Goal is met once the session's cumulative persisted atom count reaches
    # the domain target — not the current turn's atom count in isolation.
    atom_count_result = await db.execute(
        select(func.count())
        .select_from(StoryAtom)
        .where(StoryAtom.session_id == row.id)
    )
    total_atoms = atom_count_result.scalar_one()
    domain = get_domain(row.domain)
    if total_atoms >= domain.target_story_atoms:
        row.goal_met = True

    await db.commit()
    await db.refresh(row)
    logger.info(
        "Updated session %s: exchange=%d energy=%s goal_met=%s",
        session_id,
        row.exchange_count,
        row.energy_signal,
        row.goal_met,
    )
    return _to_state(row)


def should_end_session(state: SessionState) -> bool:
    """
    Return True if the LLM signalled the end, the domain goal is met, or
    the user is low-energy and the session has run long enough. This is
    the single source of truth for "is this session over" — callers must
    not re-derive it from session_end_suggested/goal_met directly.
    """
    if state.session_end_suggested:
        return True
    if state.goal_met:
        return True
    if state.energy_signal == "low" and state.exchange_count >= 3:
        return True
    return False


async def close_session(
    session_id: str, ended_reason: str, db: AsyncSession
) -> SessionState:
    """Mark a session completed. The single writer of terminal session state."""
    result = await db.execute(
        select(Session).where(Session.id == uuid.UUID(session_id))
    )
    row = result.scalar_one_or_none()
    if row is None:
        raise ValueError(f"Session not found: {session_id}")

    row.status = "completed"
    row.ended_at = datetime.now(timezone.utc)
    row.ended_reason = ended_reason
    await db.commit()
    await db.refresh(row)
    logger.info("Closed session %s: reason=%s", session_id, ended_reason)
    return _to_state(row)


async def abandon_stale_sessions(db: AsyncSession) -> int:
    """
    Mark any session that has been 'active' for longer than the stale
    threshold as 'abandoned', so it stops blocking the next day's
    conversation. Returns the number of sessions abandoned.
    """
    cutoff = datetime.now(timezone.utc) - _STALE_SESSION_AGE
    result = await db.execute(
        update(Session)
        .where(Session.status == "active")
        .where(Session.started_at < cutoff)
        .values(
            status="abandoned",
            ended_at=datetime.now(timezone.utc),
            ended_reason="timeout",
        )
    )
    await db.commit()
    if result.rowcount:
        logger.info("Abandoned %d stale session(s)", result.rowcount)
    return result.rowcount


async def get_active_session_by_number(
    whatsapp_number: str, db: AsyncSession
) -> SessionState | None:
    """
    Look up the most recent active session for a given WhatsApp number.
    "Active" means status == 'active' AND started within the stale window —
    never inferred from session_end_suggested/goal_met booleans.
    """
    cutoff = datetime.now(timezone.utc) - _STALE_SESSION_AGE
    result = await db.execute(
        select(Session)
        .where(Session.whatsapp_number == whatsapp_number)
        .where(Session.status == "active")
        .where(Session.started_at > cutoff)
        .order_by(desc(Session.started_at))
        .limit(1)
    )
    row = result.scalar_one_or_none()
    if row is None:
        return None
    return _to_state(row)


async def touch_last_message(session_id: str, db: AsyncSession) -> None:
    """Update last_user_message_at to now for the given session."""
    await db.execute(
        update(Session)
        .where(Session.id == uuid.UUID(session_id))
        .values(last_user_message_at=datetime.now(timezone.utc))
    )
    await db.commit()
