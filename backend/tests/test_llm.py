from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.llm import LLMResponse, Message, chat


def _make_mock_response(text: str = "Hello! How can I help you?") -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(text=text)]
    response.usage = MagicMock(input_tokens=10, output_tokens=8)
    return response


async def test_chat_returns_llm_response():
    mock_response = _make_mock_response()

    with patch("adapters.llm.AsyncAnthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic_cls.return_value = mock_client

        result = await chat([Message(role="user", content="Hello")])

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello! How can I help you?"
    assert result.content != ""


async def test_chat_returns_positive_token_counts():
    mock_response = _make_mock_response()

    with patch("adapters.llm.AsyncAnthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic_cls.return_value = mock_client

        result = await chat([Message(role="user", content="Hello")])

    assert result.input_tokens > 0
    assert result.output_tokens > 0


async def test_chat_separates_system_message():
    """System messages must be passed as system= param, not in messages list."""
    mock_response = _make_mock_response()

    with patch("adapters.llm.AsyncAnthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)
        mock_anthropic_cls.return_value = mock_client

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


async def test_chat_raises_on_api_error():
    from anthropic import APIStatusError

    with patch("adapters.llm.AsyncAnthropic") as mock_anthropic_cls:
        mock_client = MagicMock()
        mock_response = MagicMock()
        mock_response.status_code = 429
        mock_response.headers = {}
        mock_client.messages.create = AsyncMock(
            side_effect=APIStatusError(
                "Rate limit exceeded",
                response=mock_response,
                body={"error": {"message": "Rate limit exceeded"}},
            )
        )
        mock_anthropic_cls.return_value = mock_client

        with pytest.raises(RuntimeError, match="Anthropic API error"):
            await chat([Message(role="user", content="Hello")])
