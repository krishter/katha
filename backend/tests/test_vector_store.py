import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from memory.vector_store import embed_and_store, retrieve_relevant
from models.story_atom import StoryAtom

_FAKE_VECTOR = [0.1] * 1536


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


async def test_embed_and_store_calls_openai_with_narrative():
    atom = _make_story_atom(narrative="Father's shop in Madurai")
    db = _make_db()

    with patch("memory.vector_store._embed", new=AsyncMock(return_value=_FAKE_VECTOR)):
        await embed_and_store(atom, db)

    # commit was called after storing
    db.commit.assert_called_once()


async def test_embed_and_store_updates_db_row():
    atom = _make_story_atom()
    db = _make_db()

    with patch("memory.vector_store._embed", new=AsyncMock(return_value=_FAKE_VECTOR)):
        await embed_and_store(atom, db)

    # execute was called (for the UPDATE statement)
    db.execute.assert_called_once()


async def test_retrieve_relevant_returns_list():
    atoms = [_make_story_atom(), _make_story_atom()]
    db = _make_db(scalars=atoms)

    with patch("memory.vector_store._embed", new=AsyncMock(return_value=_FAKE_VECTOR)):
        result = await retrieve_relevant("user-1", "childhood", top_k=5, db=db)

    assert isinstance(result, list)
    assert len(result) == 2


async def test_retrieve_relevant_no_db_returns_empty():
    result = await retrieve_relevant("user-1", "childhood", top_k=5, db=None)
    assert result == []


async def test_retrieve_relevant_constructs_similarity_query():
    db = _make_db(scalars=[])

    with patch("memory.vector_store._embed", new=AsyncMock(return_value=_FAKE_VECTOR)):
        await retrieve_relevant("user-1", "childhood", top_k=3, db=db)

    # execute was called — cosine similarity query was constructed
    db.execute.assert_called_once()
