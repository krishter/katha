import uuid
from unittest.mock import AsyncMock, MagicMock

from memory.vector_store import retrieve_relevant
from models.story_atom import StoryAtom


def _make_story_atom(narrative="A story about childhood", user_id="user-1"):
    atom = MagicMock(spec=StoryAtom)
    atom.id = uuid.uuid4()
    atom.narrative = narrative
    atom.user_id = user_id
    atom.embedding = None
    return atom


def _make_db(scalars=None):
    db = AsyncMock()
    db.add = MagicMock()
    result = MagicMock()
    scalars_result = MagicMock()
    scalars_result.all.return_value = scalars or []
    result.scalars.return_value = scalars_result
    db.execute = AsyncMock(return_value=result)
    return db


async def test_retrieve_relevant_returns_list():
    atoms = [_make_story_atom(), _make_story_atom()]
    db = _make_db(scalars=atoms)

    result = await retrieve_relevant("user-1", "childhood", top_k=5, db=db)

    assert isinstance(result, list)
    assert len(result) == 2


async def test_retrieve_relevant_no_db_returns_empty():
    result = await retrieve_relevant("user-1", "childhood", top_k=5, db=None)
    assert result == []


async def test_retrieve_relevant_issues_a_query():
    db = _make_db(scalars=[])

    await retrieve_relevant("user-1", "childhood", top_k=3, db=db)

    db.execute.assert_called_once()


async def test_retrieve_relevant_makes_no_external_call():
    """S1.5 removed the embedding call. Retrieval is now pure SQL — if an
    embedding client is ever reintroduced here, this module must not reach
    the network on the turn path again without the caller guarding it."""
    import memory.vector_store as vs

    assert not hasattr(vs, "_embed")
    assert not hasattr(vs, "_client")
    assert not hasattr(vs, "embed_and_store")


async def test_retrieve_relevant_excludes_current_session():
    db = _make_db(scalars=[])
    session_id = str(uuid.uuid4())

    await retrieve_relevant(
        "user-1", "childhood", top_k=5, db=db, current_session_id=session_id
    )

    compiled = str(db.execute.call_args[0][0])
    assert "session_id !=" in compiled
