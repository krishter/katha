from __future__ import annotations

import asyncio
import logging

import httpx

logger = logging.getLogger(__name__)


async def post_with_backoff(
    client: httpx.AsyncClient,
    url: str,
    *,
    attempts: int = 3,
    base_delay: float = 1.0,
    **kwargs,
) -> httpx.Response:
    """
    POST, retrying a connection error, a timeout, a 429 or a 5xx up to
    `attempts` times in all, waiting base_delay, then twice that, between
    tries. A rate limit (429) is transient here, unlike in post_with_retry.
    The last response is returned, or the last network error raised.
    """
    for attempt in range(1, attempts + 1):
        try:
            response = await client.post(url, **kwargs)
        except (httpx.ConnectError, httpx.TimeoutException):
            if attempt == attempts:
                raise
            logger.warning("Network error POSTing to %s — retrying", url)
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
            if attempt == attempts:
                return response
            logger.warning(
                "%s returned %d — retrying (attempt %d of %d)",
                url,
                response.status_code,
                attempt,
                attempts,
            )
        await asyncio.sleep(base_delay * 2 ** (attempt - 1))
    raise AssertionError("unreachable")


async def post_with_retry(
    client: httpx.AsyncClient, url: str, **kwargs
) -> httpx.Response:
    """
    POST with exactly one retry, and only for the failure modes that are
    plausibly transient: a connection error, or a 5xx response. A 4xx
    response is a client-side problem (bad request, auth) and retrying it
    would just repeat the same failure.
    """
    try:
        response = await client.post(url, **kwargs)
    except httpx.ConnectError:
        logger.warning("Connection error POSTing to %s — retrying once", url)
        response = await client.post(url, **kwargs)
        return response

    if response.status_code >= 500:
        logger.warning("%s returned %d — retrying once", url, response.status_code)
        response = await client.post(url, **kwargs)

    return response
