import logging
import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from models.session import Session
from prompts.domains import get_domain, get_domain_sequence

logger = logging.getLogger(__name__)


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


async def start_session(user_id: str, db: AsyncSession) -> SessionState:
    """Create a new session record. Domain defaults to 'childhood' for session 1."""
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=1,
        domain=get_domain_sequence()[0],  # "childhood"
        exchange_count=0,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    db.add(session)
    await db.commit()
    await db.refresh(session)
    logger.info("Started session %s for user %s", session.id, user_id)
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

    # Check if domain goal is met based on story atoms collected
    atoms = extraction_json.get("story_atoms", [])
    if atoms:
        domain = get_domain(row.domain)
        if len(atoms) >= domain.target_story_atoms:
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
    """Return True if goal_met OR (energy_signal == 'low' AND exchange_count >= 3)."""
    if state.goal_met:
        return True
    if state.energy_signal == "low" and state.exchange_count >= 3:
        return True
    return False
