from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.llm import LLMResponse, Message, chat


def _make_mock_response(text: str = "Hello! How can I help you?") -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=10, output_tokens=8)
    return response


def _mock_client(response=None, side_effect=None):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response, side_effect=side_effect)
    return client


async def test_chat_returns_llm_response():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        result = await chat([Message(role="user", content="Hello")])

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello! How can I help you?"
    assert result.content != ""


async def test_chat_returns_positive_token_counts():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        result = await chat([Message(role="user", content="Hello")])

    assert result.input_tokens > 0
    assert result.output_tokens > 0


async def test_chat_separates_system_message():
    """System messages must be passed as system= param, not in messages list."""
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        await chat(
            [
                Message(role="system", content="You are a helpful assistant."),
                Message(role="user", content="Hello"),
            ]
        )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "You are a helpful assistant."
    # System message must not appear in the messages list
    for msg in call_kwargs["messages"]:
        assert msg["role"] != "system"


async def test_chat_defaults_max_tokens_to_500():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        await chat([Message(role="user", content="Hello")])

    assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 500


async def test_chat_passes_through_custom_max_tokens():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        await chat([Message(role="user", content="Hello")], max_tokens=2000)

    assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 2000


async def test_chat_raises_on_api_error():
    from anthropic import APIStatusError

    mock_response = MagicMock()
    mock_response.status_code = 429
    mock_response.headers = {}
    mock_client = _mock_client(
        side_effect=APIStatusError(
            "Rate limit exceeded",
            response=mock_response,
            body={"error": {"message": "Rate limit exceeded"}},
        )
    )

    with patch("adapters.llm._client", mock_client):
        with pytest.raises(RuntimeError, match="Anthropic API error"):
            await chat([Message(role="user", content="Hello")])
