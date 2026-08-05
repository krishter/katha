from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)


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
