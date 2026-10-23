"""
Story-atom embeddings for Layer 3 retrieval (docs/specs/SEMANTIC_RETRIEVAL.md
§5.1): gemini-embedding-001 on Vertex AI, in Mumbai.

Chosen by the offline evaluation (docs/proposals/embedding-eval-results.md):
100% Recall@5 against Titan V2's 90%, and at 1536 dimensions it fits
story_atoms.embedding without a migration.

- **Mumbai only.** The location is a constant, not a setting, so there is
  nothing to misconfigure: the global host (aiplatform.googleapis.com) may
  process anywhere, and the Gemini Developer API key that
  adapters/gemini_tts.py uses is a different service altogether. Every URL
  is checked against HOST before anything is sent. Note that Google's
  data-residency table (checked 2026-10-22) does not list this model for
  ML processing in India, so the regional host is where the request goes,
  not a contractual promise about where it is processed.
- **Nothing leaves while EMBEDDINGS_ENABLED is false.** Callers check the
  flag; embed() checks it again.
- **A service account key**, read from GOOGLE_APPLICATION_CREDENTIALS. The
  Lightsail box has no IAM role to federate from.
- **Every vector is L2-normalised** before it is stored or compared. Google
  advises it for shortened Gemini vectors, and the evaluation's scores
  assume it.

Nothing outside this module knows the model is Gemini: callers see embed(),
MODEL_TAG and EmbeddingUnavailable. Swapping to Titan V2 in ap-south-1 (the
fallback if Google cannot confirm residency) is this file plus a
vector(1024) migration.
"""

from __future__ import annotations

import asyncio
import logging
import math
from typing import Literal, Optional
from urllib.parse import urlparse

import httpx

from adapters.retry import post_with_backoff
from config import settings
from core import usage

logger = logging.getLogger(__name__)

MODEL = "gemini-embedding-001"
DIMENSIONS = 1536
# Stored beside every vector (story_atoms.embedding_model) and filtered on
# at query time: vectors from two models are not comparable, and mixing
# them in one ranking is silently wrong rather than an error.
MODEL_TAG = f"{MODEL}@{DIMENSIONS}"

LOCATION = "asia-south1"
HOST = f"{LOCATION}-aiplatform.googleapis.com"

# Its own ledger name. core/metrics and core/pricing read every
# vendor == "google" row as a Gemini TTS clip.
VENDOR = "google_embeddings"

_SCOPES = ("https://www.googleapis.com/auth/cloud-platform",)

# A query sits on the turn path (the first turn of a session), so it gets
# one retry and a hard ceiling. A document is embedded in the background.
_QUERY_CEILING_SECONDS = 2.5
_QUERY_ATTEMPTS = 2
_QUERY_RETRY_DELAY = 0.2
_DOCUMENT_TIMEOUT_SECONDS = 30.0
_DOCUMENT_ATTEMPTS = 3
# A runaway transcript must not become a long call. autoTruncate covers
# the model's own token limit; this covers the request.
_QUERY_MAX_CHARS = 2000
_DOCUMENT_MAX_CHARS = 8000

Kind = Literal["document", "query"]
_TASK_TYPE = {"document": "RETRIEVAL_DOCUMENT", "query": "RETRIEVAL_QUERY"}


class EmbeddingUnavailable(Exception):
    """No vector this time. `reason` is "timeout", "error" or "empty"."""

    def __init__(self, reason: str, detail: str = ""):
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


def endpoint(project: str) -> str:
    url = (
        f"https://{HOST}/v1/projects/{project}/locations/{LOCATION}"
        f"/publishers/google/models/{MODEL}:predict"
    )
    check_host(url)
    return url


def check_host(url: str) -> None:
    """Refuse any host but the Mumbai regional one."""
    host = urlparse(url).hostname
    if host != HOST:
        raise RuntimeError(f"refusing to send embeddings to {host!r}: only {HOST}")


def request_body(text: str, kind: Kind) -> dict:
    # One instance per request: the model takes one input at a time.
    return {
        "instances": [{"content": text, "task_type": _TASK_TYPE[kind]}],
        "parameters": {"outputDimensionality": DIMENSIONS, "autoTruncate": True},
    }


def normalise(values: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in values))
    if norm == 0:
        raise EmbeddingUnavailable("error", "zero vector")
    return [v / norm for v in values]


_credentials = None


def _load_credentials():
    from google.oauth2 import service_account

    return service_account.Credentials.from_service_account_file(
        settings.GOOGLE_APPLICATION_CREDENTIALS, scopes=_SCOPES
    )


def _refresh(credentials) -> None:
    from google.auth.transport.requests import Request

    credentials.refresh(Request())


async def _access_token() -> str:
    """A bearer token for the service account. Loading the key and
    refreshing the token are blocking, so they run in a thread."""
    global _credentials
    if _credentials is None:
        _credentials = await asyncio.to_thread(_load_credentials)
    if not _credentials.valid:
        await asyncio.to_thread(_refresh, _credentials)
    return _credentials.token


async def _call(text: str, kind: Kind, attempts: int, timeout: float) -> dict:
    if not settings.GOOGLE_CLOUD_PROJECT or not settings.GOOGLE_APPLICATION_CREDENTIALS:
        raise EmbeddingUnavailable(
            "error", "GOOGLE_CLOUD_PROJECT or credentials not set"
        )
    url = endpoint(settings.GOOGLE_CLOUD_PROJECT)
    token = await _access_token()
    async with httpx.AsyncClient(timeout=timeout) as client:
        response = await post_with_backoff(
            client,
            url,
            attempts=attempts,
            base_delay=_QUERY_RETRY_DELAY if kind == "query" else 1.0,
            headers={"Authorization": f"Bearer {token}"},
            json=request_body(text, kind),
        )
    if response.status_code != 200:
        raise EmbeddingUnavailable(
            "error", f"Vertex {response.status_code}: {response.text[:200]}"
        )
    return response.json()


async def embed(text: str, *, kind: Kind) -> list[float]:
    """
    One L2-normalised vector of DIMENSIONS floats, or EmbeddingUnavailable.

    Writes one ledger row per successful call (embed_document or
    embed_query) with the tokens Google counted.
    """
    if not settings.EMBEDDINGS_ENABLED:
        raise RuntimeError("EMBEDDINGS_ENABLED is false: nothing may be sent")
    text = (text or "").strip()
    if not text:
        raise EmbeddingUnavailable("empty")

    try:
        if kind == "query":
            text = text[:_QUERY_MAX_CHARS]
            body = await asyncio.wait_for(
                _call(text, kind, _QUERY_ATTEMPTS, _QUERY_CEILING_SECONDS),
                timeout=_QUERY_CEILING_SECONDS,
            )
        else:
            text = text[:_DOCUMENT_MAX_CHARS]
            body = await _call(
                text, kind, _DOCUMENT_ATTEMPTS, _DOCUMENT_TIMEOUT_SECONDS
            )
    except EmbeddingUnavailable:
        raise
    except (asyncio.TimeoutError, httpx.TimeoutException) as e:
        raise EmbeddingUnavailable("timeout", type(e).__name__) from e
    except Exception as e:
        raise EmbeddingUnavailable("error", type(e).__name__) from e

    try:
        prediction = body["predictions"][0]["embeddings"]
        values = [float(v) for v in prediction["values"]]
    except (KeyError, IndexError, TypeError, ValueError) as e:
        raise EmbeddingUnavailable("error", "unexpected response shape") from e
    if len(values) != DIMENSIONS:
        raise EmbeddingUnavailable("error", f"got {len(values)} dimensions")

    tokens = int((prediction.get("statistics") or {}).get("token_count") or 0)
    await usage.record(
        VENDOR,
        f"embed_{kind}",
        {"tokens": tokens, "characters": len(text)},
        model=MODEL_TAG,
    )
    return normalise(values)


async def record_query_fallback(reason: str) -> None:
    """Ledger row for a query that fell back to recency. Never raises."""
    await usage.record(
        VENDOR, "embed_query_fallback", {"reason": reason}, model=MODEL_TAG
    )


def reset_credentials_for_tests(credentials: Optional[object] = None) -> None:
    global _credentials
    _credentials = credentials
