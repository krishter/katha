from __future__ import annotations

import os

import pytest
from sqlalchemy import delete, text

from models.crisis_event import CrisisEvent
from models.db import AsyncSessionLocal, engine
from models.fact import Fact
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel

# Deletion order matters for FK constraints (children before parents).
# Deliberately excludes family_accounts — these tests create their own
# user_profiles/sessions rows but never a family account, so there's no
# reason to touch that table.
_TABLES_IN_DELETE_ORDER = [
    CrisisEvent,
    MemoryCard,
    StoryAtom,
    Turn,
    Fact,
    Session,
    UserProfileModel,
]


async def _db_reachable() -> tuple[bool, str]:
    """Returns (reachable, reason) — the reason is surfaced in the skip or
    failure message, because "no reachable Postgres" hides a wide range of
    causes (daemon down, wrong driver in DATABASE_URL, migrations not run)
    and diagnosing it from a bare skip line wastes the time this fixture
    is meant to save."""
    try:
        async with AsyncSessionLocal() as session:
            await session.execute(text("SELECT 1"))
        return True, ""
    except Exception as exc:
        return False, f"{type(exc).__name__}: {exc}"


@pytest.fixture
async def real_db():
    """
    A real AsyncSession against Postgres (see REMEDIATION_PLAN WS5) — these
    tests exercise the actual DB-write behavior (embeddings, cumulative
    atom counts, unique constraints) that mocked unit tests can't.

    When no DB is reachable this skips by default, so a developer without
    Postgres running still gets a useful local run. CI sets
    KATHA_REQUIRE_DB=1, which turns that skip into a failure: CI now
    provides a Postgres service, and a silent skip there would report
    green for the four suites that actually exercise what Sprint 1 fixed
    (deletion E2E, failure injection, pilot rehearsal, parent consent).
    That is the exact hole S4.0(c) was opened to close.

    Rows created during the test are cleaned up afterward with an explicit
    DELETE per table, not a transaction rollback — simpler to reason about
    given how many commits the orchestrator makes per turn, and this is a
    disposable pilot-verification database, not shared state.

    The engine's connection pool is disposed after every test (not just
    once at session end): pytest-asyncio hands each test function its own
    event loop by default, but `models.db.engine` is a single module-level
    object created once at import time. A pooled asyncpg connection is
    bound to the loop it was created on — reused from a prior test's
    (now-closed) loop, it fails silently, and `_db_reachable` turns that
    into a false "no reachable Postgres" skip for every test after the
    first in a multi-test module. Disposing forces fresh connections on
    the next test's loop.
    """
    reachable, reason = await _db_reachable()
    if not reachable:
        message = (
            "No reachable Postgres at DATABASE_URL — run `docker compose up -d "
            "db` and `alembic upgrade head` to enable @pytest.mark.integration "
            f"tests. Underlying error: {reason}"
        )
        if os.getenv("KATHA_REQUIRE_DB") == "1":
            pytest.fail(
                "KATHA_REQUIRE_DB=1 but the database is unreachable, so the "
                "integration suite would have skipped itself silently. " + message
            )
        pytest.skip(message)

    async with AsyncSessionLocal() as session:
        yield session

    async with AsyncSessionLocal() as cleanup:
        for model in _TABLES_IN_DELETE_ORDER:
            await cleanup.execute(delete(model))
        await cleanup.commit()

    await engine.dispose()
