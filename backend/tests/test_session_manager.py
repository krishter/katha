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
        status="active",
        whatsapp_number=None,
    )
    defaults.update(kwargs)
    row = MagicMock()
    for k, v in defaults.items():
        setattr(row, k, v)
    return row


def _make_db(row: MagicMock) -> AsyncMock:
    """Return a mock AsyncSession that returns the given row on every execute()."""
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = row
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


def _make_start_session_db(completed_count=0, atom_counts=None):
    """
    A db mock for start_session's two lookups, in call order:
    1. _count_completed_sessions -> scalar_one() -> int
    2. _select_domain -> .all() -> list[(domain_id, count)]
    """
    db = AsyncMock()
    db.add = MagicMock()
    db.commit = AsyncMock()
    db.refresh = AsyncMock()

    count_result = MagicMock()
    count_result.scalar_one.return_value = completed_count

    domain_result = MagicMock()
    domain_result.all.return_value = atom_counts or []

    call_count = 0

    async def fake_execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        return count_result if call_count == 1 else domain_result

    db.execute = fake_execute
    return db


def _make_record_turn_db(row: MagicMock) -> AsyncMock:
    """A db mock for record_turn's single session-row lookup."""
    return _make_db(row)


def _make_apply_extraction_db(row: MagicMock, total_atoms: int = 0):
    """
    A db mock for apply_extraction's two lookups, in call order:
    1. session row lookup -> scalar_one_or_none()
    2. cumulative story_atoms count for this session -> scalar_one()
    """
    session_result = MagicMock()
    session_result.scalar_one_or_none.return_value = row

    count_result = MagicMock()
    count_result.scalar_one.return_value = total_atoms

    call_count = 0

    async def fake_execute(stmt, *a, **kw):
        nonlocal call_count
        call_count += 1
        return session_result if call_count == 1 else count_result

    db = AsyncMock()
    db.execute = fake_execute
    db.commit = AsyncMock()
    db.refresh = AsyncMock()
    return db


# ── start_session ─────────────────────────────────────────────────────────────


async def test_start_session_returns_session_state():
    from core.session_manager import start_session

    db = _make_start_session_db()

    with patch(
        "core.session_manager.freemium.is_session_allowed",
        new=AsyncMock(return_value=True),
    ):
        state = await start_session("user-1", db)

    assert isinstance(state, SessionState)
    assert state.user_id == "user-1"


async def test_start_session_defaults_to_childhood_for_new_user():
    from core.session_manager import start_session

    db = _make_start_session_db(completed_count=0, atom_counts=[])

    with patch(
        "core.session_manager.freemium.is_session_allowed",
        new=AsyncMock(return_value=True),
    ):
        state = await start_session("user-1", db)

    assert state.domain == "childhood"
    assert state.session_number == 1


async def test_start_session_session_number_advances_past_completed():
    from core.session_manager import start_session

    db = _make_start_session_db(completed_count=2, atom_counts=[])

    with patch(
        "core.session_manager.freemium.is_session_allowed",
        new=AsyncMock(return_value=True),
    ):
        state = await start_session("user-1", db)

    assert state.session_number == 3


async def test_start_session_advances_domain_when_target_met():
    from core.session_manager import start_session
    from prompts.domains import get_domain

    target = get_domain("childhood").target_story_atoms
    db = _make_start_session_db(completed_count=1, atom_counts=[("childhood", target)])

    with patch(
        "core.session_manager.freemium.is_session_allowed",
        new=AsyncMock(return_value=True),
    ):
        state = await start_session("user-1", db)

    assert state.domain == "family_ancestors"


async def test_start_session_stays_on_domain_when_target_not_met():
    from core.session_manager import start_session
    from prompts.domains import get_domain

    target = get_domain("childhood").target_story_atoms
    db = _make_start_session_db(
        completed_count=1, atom_counts=[("childhood", target - 1)]
    )

    with patch(
        "core.session_manager.freemium.is_session_allowed",
        new=AsyncMock(return_value=True),
    ):
        state = await start_session("user-1", db)

    assert state.domain == "childhood"


# ── record_turn ───────────────────────────────────────────────────────────────


async def test_record_turn_increments_exchange_count():
    from core.session_manager import record_turn

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id), exchange_count=2)
    db = _make_record_turn_db(row)

    state = await record_turn(session_id, db)

    assert state.exchange_count == 3


async def test_record_turn_does_not_touch_energy_or_goal_met():
    from core.session_manager import record_turn

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(
        id=uuid.UUID(session_id), energy_signal="high", goal_met=False
    )
    db = _make_record_turn_db(row)

    state = await record_turn(session_id, db)

    assert state.energy_signal == "high"
    assert state.goal_met is False


# ── apply_extraction ──────────────────────────────────────────────────────────


async def test_apply_extraction_updates_energy_signal():
    from core.session_manager import apply_extraction

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id), energy_signal="high")
    db = _make_apply_extraction_db(row, total_atoms=0)

    extraction = {"energy_signal": "low", "session_end_suggested": False}
    state = await apply_extraction(session_id, extraction, db)

    assert state.energy_signal == "low"


async def test_apply_extraction_updates_session_end_suggested():
    from core.session_manager import apply_extraction

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id), session_end_suggested=False)
    db = _make_apply_extraction_db(row, total_atoms=0)

    extraction = {"energy_signal": "high", "session_end_suggested": True}
    state = await apply_extraction(session_id, extraction, db)

    assert state.session_end_suggested is True


async def test_apply_extraction_does_not_touch_exchange_count():
    from core.session_manager import apply_extraction

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id), exchange_count=5)
    db = _make_apply_extraction_db(row, total_atoms=0)

    extraction = {"energy_signal": "high", "session_end_suggested": False}
    state = await apply_extraction(session_id, extraction, db)

    assert state.exchange_count == 5


async def test_apply_extraction_sets_goal_met_from_cumulative_atom_count():
    from core.session_manager import apply_extraction
    from prompts.domains import get_domain

    target = get_domain("childhood").target_story_atoms
    session_id = str(uuid.uuid4())
    row = _make_db_session_row(
        id=uuid.UUID(session_id), domain="childhood", goal_met=False
    )
    db = _make_apply_extraction_db(row, total_atoms=target)

    extraction = {"energy_signal": "high", "session_end_suggested": False}
    state = await apply_extraction(session_id, extraction, db)

    assert state.goal_met is True


async def test_apply_extraction_goal_met_uses_cumulative_not_current_turn_count():
    """
    Regression for the original bug: a turn that itself reports `target`
    atoms must NOT set goal_met if the session's cumulative persisted count
    (read from the DB) is still short of the domain target.
    """
    from core.session_manager import apply_extraction
    from prompts.domains import get_domain

    target = get_domain("childhood").target_story_atoms
    session_id = str(uuid.uuid4())
    row = _make_db_session_row(
        id=uuid.UUID(session_id), domain="childhood", goal_met=False
    )
    db = _make_apply_extraction_db(row, total_atoms=target - 1)

    extraction = {
        "energy_signal": "high",
        "session_end_suggested": False,
        "story_atoms": [{"narrative": f"atom {i}"} for i in range(target)],
    }
    state = await apply_extraction(session_id, extraction, db)

    assert state.goal_met is False


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


def test_should_end_when_llm_signals_end():
    state = SessionState(
        session_id="s1",
        user_id="u1",
        session_number=1,
        domain="childhood",
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=True,
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


# ── close_session ─────────────────────────────────────────────────────────────


async def test_close_session_sets_completed_status_and_reason():
    from core.session_manager import close_session

    session_id = str(uuid.uuid4())
    row = _make_db_session_row(id=uuid.UUID(session_id))
    db = _make_db(row)

    state = await close_session(session_id, "goal_met", db)

    assert row.status == "completed"
    assert row.ended_reason == "goal_met"
    assert row.ended_at is not None
    assert isinstance(state, SessionState)


# ── abandon_stale_sessions ────────────────────────────────────────────────────


async def test_abandon_stale_sessions_marks_old_active_sessions():
    from core.session_manager import abandon_stale_sessions

    db = AsyncMock()
    result = MagicMock()
    result.rowcount = 2
    db.execute = AsyncMock(return_value=result)
    db.commit = AsyncMock()

    count = await abandon_stale_sessions(db)

    assert count == 2
    db.commit.assert_called_once()
    stmt = db.execute.call_args.args[0]
    compiled = str(stmt)
    assert "sessions" in compiled.lower()
    assert "UPDATE" in compiled.upper()


# ── get_active_session_by_number ──────────────────────────────────────────────


async def test_get_active_session_by_number_returns_state_for_active_row():
    from core.session_manager import get_active_session_by_number

    row = _make_db_session_row(whatsapp_number="+919876543210", status="active")
    db = _make_db(row)

    state = await get_active_session_by_number("+919876543210", db)

    assert state is not None
    assert isinstance(state, SessionState)


async def test_get_active_session_by_number_returns_none_when_no_row():
    from core.session_manager import get_active_session_by_number

    db = _make_db(None)

    state = await get_active_session_by_number("+919876543210", db)

    assert state is None


async def test_get_active_session_by_number_filters_on_status_not_booleans():
    """
    Activeness must be derived from status + recency — never from
    session_end_suggested/goal_met (see should_end_session for that logic).
    """
    from core.session_manager import get_active_session_by_number

    row = _make_db_session_row(whatsapp_number="+919876543210", status="active")
    db = _make_db(row)

    await get_active_session_by_number("+919876543210", db)

    stmt = db.execute.call_args.args[0]
    where_compiled = str(stmt.whereclause).lower()
    assert "status" in where_compiled
    assert "session_end_suggested" not in where_compiled
    assert "goal_met" not in where_compiled
