import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from core.session_manager import SessionState, should_end_session


def _make_db_session_row(**kwargs) -> MagicMock:
    """Build a mock Session ORM row."""
    defaults = dict(
        id=uuid.uuid4(),
        user_id="user-1",
        session_number=1,
        domain="childhood",
        exchange_count=0,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    defaults.update(kwargs)
    row = MagicMock()
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_db(row: MagicMock) -> AsyncMock:
    """Return a mock AsyncSession that returns the given row on execute."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ── start_session ─────────────────────────────────────────────────────────────


async def test_start_session_domain_is_childhood():
    from core.session_manager import start_session

    row = _make_db_session_row()
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock(side_effect=lambda r: None)

    # Patch Session constructor to return our mock row
    with patch("core.session_manager.Session", return_value=row):
        state = await start_session("user-1", db)

    assert state.domain == "childhood"


async def test_start_session_returns_session_state():
    from core.session_manager import start_session

    row = _make_db_session_row()
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    with patch("core.session_manager.Session", return_value=row):
        state = await start_session("user-1", db)

    assert isinstance(state, SessionState)
    assert state.user_id == "user-1"


# ── update_session ────────────────────────────────────────────────────────────


async def test_update_session_increments_exchange_count():
    from core.session_manager import update_session

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id), exchange_count=2)
    db = _make_db(row)

    extraction = {
        "energy_signal": "high",
        "session_end_suggested": False,
        "story_atoms": [],
    }
    state = await update_session(session_id, extraction, db)

    assert state.exchange_count == 3


async def test_update_session_updates_energy_signal():
    from core.session_manager import update_session

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id), energy_signal="high")
    db = _make_db(row)

    extraction = {
        "energy_signal": "low",
        "session_end_suggested": False,
        "story_atoms": [],
    }
    state = await update_session(session_id, extraction, db)

    assert state.energy_signal == "low"


# ── should_end_session ────────────────────────────────────────────────────────


def test_should_end_when_goal_met():
    state = SessionState(
        session_id="s1",
        user_id="u1",
        session_number=1,
        domain="childhood",
        exchange_count=5,
        energy_signal="high",
        goal_met=True,
        session_end_suggested=False,
    )
    assert should_end_session(state) is True


def test_should_end_when_low_energy_and_enough_exchanges():
    state = SessionState(
        session_id="s1",
        user_id="u1",
        session_number=1,
        domain="childhood",
        exchange_count=4,
        energy_signal="low",
        goal_met=False,
        session_end_suggested=False,
    )
    assert should_end_session(state) is True


def test_should_not_end_when_low_energy_but_few_exchanges():
    state = SessionState(
        session_id="s1",
        user_id="u1",
        session_number=1,
        domain="childhood",
        exchange_count=1,
        energy_signal="low",
        goal_met=False,
        session_end_suggested=False,
    )
    assert should_end_session(state) is False


def test_should_not_end_in_normal_conditions():
    state = SessionState(
        session_id="s1",
        user_id="u1",
        session_number=1,
        domain="childhood",
        exchange_count=3,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    assert should_end_session(state) is False
