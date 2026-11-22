"""adapters/gemini_embeddings.py (docs/specs/SEMANTIC_RETRIEVAL.md §5.1, §6).

No test reaches Google: the HTTP call and the token are replaced."""

import asyncio
import math
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from adapters import gemini_embeddings as ge
from config import settings

# The real token function, before the autouse fixture replaces it.
_real_access_token = ge._access_token


def _values(n=ge.DIMENSIONS, value=3.0):
    return [value] * n


def _response(status=200, values=None, tokens=12):
    body = {
        "predictions": [
            {
                "embeddings": {
                    "values": values if values is not None else _values(),
                    "statistics": {"token_count": tokens, "truncated": False},
                }
            }
        ]
    }
    return httpx.Response(status, json=body if status == 200 else {"error": "x"})


@pytest.fixture(autouse=True)
def _enabled(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", True)
    monkeypatch.setattr(settings, "GOOGLE_CLOUD_PROJECT", "kathadotlife")
    monkeypatch.setattr(settings, "GOOGLE_APPLICATION_CREDENTIALS", "/keys/k.json")
    with patch.object(ge, "_access_token", AsyncMock(return_value="tok")):
        yield


def _posting(*responses):
    """Patch the HTTP POST; returns the mock so calls can be inspected."""
    post = AsyncMock(side_effect=list(responses))
    return patch.object(httpx.AsyncClient, "post", post), post


# ── Request ──────────────────────────────────────────────────────────────


def test_endpoint_is_mumbai_regional():
    url = ge.endpoint("kathadotlife")
    assert url == (
        "https://asia-south1-aiplatform.googleapis.com/v1/projects/kathadotlife/"
        "locations/asia-south1/publishers/google/models/gemini-embedding-001:predict"
    )


@pytest.mark.parametrize(
    "url",
    [
        "https://aiplatform.googleapis.com/v1/projects/p/locations/global/x",
        "https://generativelanguage.googleapis.com/v1beta/models/x:embedContent",
        "https://us-central1-aiplatform.googleapis.com/v1/x",
    ],
)
def test_host_check_refuses_anything_but_mumbai(url):
    with pytest.raises(RuntimeError, match="refusing"):
        ge.check_host(url)


@pytest.mark.parametrize(
    "kind,task", [("document", "RETRIEVAL_DOCUMENT"), ("query", "RETRIEVAL_QUERY")]
)
def test_request_body(kind, task):
    body = ge.request_body("a story", kind)
    assert body == {
        "instances": [{"content": "a story", "task_type": task}],
        "parameters": {"outputDimensionality": 1536, "autoTruncate": True},
    }


async def test_call_sends_bearer_token_to_mumbai_and_no_api_key():
    patcher, post = _posting(_response())
    with patcher:
        await ge.embed("her father's shop", kind="document")
    url = post.call_args.args[0]
    kwargs = post.call_args.kwargs
    assert url.startswith("https://asia-south1-aiplatform.googleapis.com/")
    assert kwargs["headers"] == {"Authorization": "Bearer tok"}
    assert "x-goog-api-key" not in kwargs["headers"]
    assert kwargs["json"]["instances"][0]["task_type"] == "RETRIEVAL_DOCUMENT"


# ── Response ─────────────────────────────────────────────────────────────


async def test_vectors_come_back_l2_normalised():
    patcher, _ = _posting(_response(values=_values(value=3.0)))
    with patcher:
        vector = await ge.embed("text", kind="query")
    assert len(vector) == 1536
    assert math.isclose(math.sqrt(sum(v * v for v in vector)), 1.0, rel_tol=1e-9)


async def test_wrong_dimension_count_is_an_error():
    patcher, _ = _posting(_response(values=[1.0] * 3072))
    with patcher, pytest.raises(ge.EmbeddingUnavailable) as e:
        await ge.embed("text", kind="document")
    assert e.value.reason == "error"


# ── Retries, ceiling, fallback signal ────────────────────────────────────


@pytest.mark.parametrize("status", [429, 500, 503])
async def test_document_retries_transient_errors(status, monkeypatch):
    monkeypatch.setattr("adapters.retry.asyncio.sleep", AsyncMock())
    patcher, post = _posting(_response(status), _response(status), _response())
    with patcher:
        await ge.embed("text", kind="document")
    assert post.call_count == 3


async def test_query_gets_one_retry_only(monkeypatch):
    monkeypatch.setattr("adapters.retry.asyncio.sleep", AsyncMock())
    patcher, post = _posting(_response(503), _response(503), _response())
    with patcher, pytest.raises(ge.EmbeddingUnavailable) as e:
        await ge.embed("text", kind="query")
    assert post.call_count == 2
    assert e.value.reason == "error"


async def test_query_has_a_hard_ceiling(monkeypatch):
    monkeypatch.setattr(ge, "_QUERY_CEILING_SECONDS", 0.05)

    async def slow(*args, **kwargs):
        await asyncio.sleep(1)

    with patch.object(httpx.AsyncClient, "post", slow):
        with pytest.raises(ge.EmbeddingUnavailable) as e:
            await ge.embed("text", kind="query")
    assert e.value.reason == "timeout"


async def test_empty_text_is_unavailable_without_a_call():
    patcher, post = _posting()
    with patcher, pytest.raises(ge.EmbeddingUnavailable) as e:
        await ge.embed("   ", kind="query")
    assert e.value.reason == "empty"
    post.assert_not_called()


async def test_query_is_capped():
    patcher, post = _posting(_response())
    with patcher:
        await ge.embed("x" * 10_000, kind="query")
    sent = post.call_args.kwargs["json"]["instances"][0]["content"]
    assert len(sent) == 2000


async def test_flag_off_refuses_to_send(monkeypatch):
    monkeypatch.setattr(settings, "EMBEDDINGS_ENABLED", False)
    patcher, post = _posting(_response())
    with patcher, pytest.raises(RuntimeError, match="EMBEDDINGS_ENABLED"):
        await ge.embed("text", kind="document")
    post.assert_not_called()


# ── Ledger ───────────────────────────────────────────────────────────────


async def test_ledger_row_uses_its_own_vendor_and_google_tokens():
    record = AsyncMock()
    patcher, _ = _posting(_response(tokens=41))
    with patcher, patch.object(ge.usage, "record", record):
        await ge.embed("text", kind="query")
    record.assert_awaited_once()
    vendor, operation, units = record.call_args.args
    assert vendor == "google_embeddings"
    assert operation == "embed_query"
    assert units["tokens"] == 41
    assert record.call_args.kwargs["model"] == "gemini-embedding-001@1536"


async def test_fallback_row_carries_the_reason():
    record = AsyncMock()
    with patch.object(ge.usage, "record", record):
        await ge.record_query_fallback("timeout")
    assert record.call_args.args == (
        "google_embeddings",
        "embed_query_fallback",
        {"reason": "timeout"},
    )


async def test_no_ledger_row_when_the_call_fails():
    record = AsyncMock()
    patcher, _ = _posting(_response(400))
    with patcher, patch.object(ge.usage, "record", record):
        with pytest.raises(ge.EmbeddingUnavailable):
            await ge.embed("text", kind="document")
    record.assert_not_awaited()


# ── Auth ─────────────────────────────────────────────────────────────────


async def test_token_refresh_runs_off_the_event_loop():
    """Loading the key and refreshing are blocking; both go to a thread."""
    creds = MagicMock(valid=False, token="fresh")
    ge.reset_credentials_for_tests()
    try:
        with patch.object(ge, "_load_credentials", return_value=creds) as load:
            with patch.object(ge, "_refresh") as refresh:
                with patch.object(
                    ge.asyncio, "to_thread", wraps=asyncio.to_thread
                ) as thread:
                    token = await _real_access_token()
    finally:
        ge.reset_credentials_for_tests()
    assert token == "fresh"
    load.assert_called_once()
    refresh.assert_called_once_with(creds)
    assert thread.call_count == 2
