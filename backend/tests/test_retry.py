from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from adapters.retry import post_with_retry


def _response(status_code: int) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    return resp


async def test_post_with_retry_returns_on_first_success():
    client = MagicMock()
    ok = _response(200)
    client.post = AsyncMock(return_value=ok)

    result = await post_with_retry(client, "https://example.com")

    assert result is ok
    assert client.post.await_count == 1


async def test_post_with_retry_does_not_retry_4xx():
    client = MagicMock()
    bad_request = _response(422)
    client.post = AsyncMock(return_value=bad_request)

    result = await post_with_retry(client, "https://example.com")

    assert result is bad_request
    assert client.post.await_count == 1


async def test_post_with_retry_retries_once_on_5xx():
    client = MagicMock()
    server_error = _response(503)
    ok = _response(200)
    client.post = AsyncMock(side_effect=[server_error, ok])

    result = await post_with_retry(client, "https://example.com")

    assert result is ok
    assert client.post.await_count == 2


async def test_post_with_retry_gives_up_after_one_retry_on_5xx():
    client = MagicMock()
    server_error = _response(503)
    client.post = AsyncMock(return_value=server_error)

    result = await post_with_retry(client, "https://example.com")

    assert result.status_code == 503
    assert client.post.await_count == 2


async def test_post_with_retry_retries_once_on_connect_error():
    client = MagicMock()
    ok = _response(200)
    client.post = AsyncMock(side_effect=[httpx.ConnectError("refused"), ok])

    result = await post_with_retry(client, "https://example.com")

    assert result is ok
    assert client.post.await_count == 2


async def test_post_with_retry_raises_if_connect_error_twice():
    client = MagicMock()
    client.post = AsyncMock(
        side_effect=[httpx.ConnectError("refused"), httpx.ConnectError("refused")]
    )

    with pytest.raises(httpx.ConnectError):
        await post_with_retry(client, "https://example.com")

    assert client.post.await_count == 2
