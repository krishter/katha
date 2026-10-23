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


async def test_flag_off_no_path_reaches_the_network(monkeypatch):
    """
    S1.5 removed an embedding call that ran on every turn; this test used
    to pin that by asserting the module had no embedding client at all.
    Semantic retrieval brought one back behind EMBEDDINGS_ENABLED
    (SEMANTIC_RETRIEVAL.md D1), so what is pinned now is the flag: with it
    off, no path in this module sends anything anywhere.
    """
    import httpx

    import memory.vector_store as vs
    from config import settings

    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", False)
    post = AsyncMock()
    embed = AsyncMock()
    monkeypatch.setattr(httpx.AsyncClient, "post", post)
    monkeypatch.setattr(vs.gemini_embeddings, "embed", embed)
    db = _make_db(scalars=[_make_story_atom()])

    await vs.retrieve_relevant("user-1", "childhood", top_k=5, db=db)
    assert await vs.embed_atoms([_make_story_atom()], db) == 0
    assert await vs.sweep_unembedded(db) == 0

    embed.assert_not_called()
    post.assert_not_called()


async def test_retrieve_relevant_excludes_current_session():
    db = _make_db(scalars=[])
    session_id = str(uuid.uuid4())

    await retrieve_relevant(
        "user-1", "childhood", top_k=5, db=db, current_session_id=session_id
    )

    compiled = str(db.execute.call_args[0][0])
    assert "session_id !=" in compiled


# ── retrieve_semantic (SEMANTIC_RETRIEVAL.md §5.4) ──────────────────────────


def _compiled(db) -> str:
    from sqlalchemy.dialects import postgresql

    stmt = db.execute.call_args[0][0]
    return str(stmt.compile(dialect=postgresql.dialect()))


async def test_retrieve_semantic_filters_user_model_and_null_vectors():
    from memory.vector_store import retrieve_semantic

    db = _make_db(scalars=[])
    await retrieve_semantic("user-1", [0.1] * 1536, k=8, db=db)

    sql = _compiled(db)
    assert "story_atoms.user_id = %(user_id_1)s" in sql
    assert "story_atoms.embedding IS NOT NULL" in sql
    assert "story_atoms.embedding_model = %(embedding_model_1)s" in sql
    assert "<=>" in sql  # cosine distance
    assert "LIMIT" in sql
    params = db.execute.call_args[0][0].compile().params
    assert params["embedding_model_1"] == "gemini-embedding-001@1536"
    assert params["user_id_1"] == "user-1"
    assert params["param_1"] == 8


async def test_retrieve_semantic_excludes_current_session():
    from memory.vector_store import retrieve_semantic

    db = _make_db(scalars=[])
    await retrieve_semantic(
        "user-1", [0.1] * 1536, k=8, db=db, current_session_id=str(uuid.uuid4())
    )
    assert "story_atoms.session_id !=" in _compiled(db)


async def test_retrieve_semantic_no_db_returns_empty():
    from memory.vector_store import retrieve_semantic

    assert await retrieve_semantic("user-1", [0.1] * 1536, k=8, db=None) == []


# ── embed_atoms and the sweep (§5.3, D5) ───────────────────────────────────


def _atom(**kw):
    atom = StoryAtom(
        id=uuid.uuid4(),
        session_id=uuid.uuid4(),
        user_id="user-1",
        domain="childhood",
        title=kw.get("title", "Father's shop"),
        narrative=kw.get("narrative", "Her father kept a brass shop."),
        verbatim_quote=kw.get("verbatim_quote", "കട"),
    )
    atom.embedding_failed = False
    return atom


async def test_embed_atoms_stores_vector_and_model_tag(monkeypatch):
    import memory.vector_store as vs
    from config import settings

    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", True)
    embed = AsyncMock(return_value=[1.0] + [0.0] * 1535)
    monkeypatch.setattr(vs.gemini_embeddings, "embed", embed)
    atom = _atom()
    db = _make_db()

    assert await vs.embed_atoms([atom], db) == 1

    assert embed.call_args.args[0] == (
        "Father's shop\nHer father kept a brass shop.\n\u201cകട\u201d"
    )
    assert embed.call_args.kwargs == {"kind": "document"}
    assert atom.embedding[0] == 1.0
    assert atom.embedding_model == "gemini-embedding-001@1536"
    assert atom.embedding_failed is False
    db.commit.assert_awaited()


async def test_embed_atoms_marks_failures_and_keeps_going(monkeypatch):
    import memory.vector_store as vs
    from adapters.gemini_embeddings import EmbeddingUnavailable
    from config import settings

    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", True)
    ok, bad = _atom(title="ok"), _atom(title="bad")

    async def embed(text, kind):
        if text.startswith("bad"):
            raise EmbeddingUnavailable("timeout")
        return [1.0] + [0.0] * 1535

    monkeypatch.setattr(vs.gemini_embeddings, "embed", embed)
    db = _make_db()

    assert await vs.embed_atoms([ok, bad], db) == 1
    assert ok.embedding_model == "gemini-embedding-001@1536"
    assert bad.embedding_failed is True
    assert bad.embedding is None


async def test_embed_atoms_never_raises_when_the_commit_fails(monkeypatch):
    import memory.vector_store as vs
    from config import settings

    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(
        vs.gemini_embeddings, "embed", AsyncMock(return_value=[1.0] + [0.0] * 1535)
    )
    db = _make_db()
    db.commit = AsyncMock(side_effect=RuntimeError("db down"))
    db.rollback = AsyncMock()

    assert await vs.embed_atoms([_atom()], db) == 0
    db.rollback.assert_awaited()


async def test_sweep_picks_pending_before_failed_capped_at_50(monkeypatch):
    from sqlalchemy.dialects import postgresql

    import memory.vector_store as vs
    from config import settings

    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(vs, "embed_atoms", AsyncMock(return_value=0))
    db = _make_db(scalars=[])

    await vs.sweep_unembedded(db)

    stmt = db.execute.call_args[0][0]
    sql = str(stmt.compile(dialect=postgresql.dialect()))
    assert "story_atoms.embedding IS NULL" in sql
    assert "story_atoms.embedding_failed IS true" in sql
    order = "ORDER BY story_atoms.embedding_failed ASC, story_atoms.created_at ASC"
    assert order in sql
    assert stmt.compile().params["param_1"] == 50
