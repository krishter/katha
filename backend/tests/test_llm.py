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


# ── SDK signature drift (production incident, 2026-10-02) ───────────────────
#
# Every test above patches _client.messages.create, so the kwargs chat()
# builds are only ever checked against a MagicMock — which accepts anything.
# anthropic 1.x removed `temperature` from messages.create(); requirements.txt
# said `anthropic>=0.40`, so the production image built against 1.7.0 while
# laptops stayed on 0.116.0. Every dialogue turn raised
#   TypeError: AsyncMessages.create() got an unexpected keyword argument
# and the elderly user got the "something went wrong" fallback on every voice
# note, for four days, with the whole suite green.
#
# These two tests check the kwargs against the *real installed SDK* instead of
# a mock. No network: inspecting a signature and binding arguments to it are
# both local operations.


def _kwargs_chat_builds() -> dict:
    """The exact kwargs chat() hands to messages.create, captured once."""
    captured = {}

    async def _capture(**kwargs):
        captured.update(kwargs)
        return _make_mock_response()

    import asyncio

    with patch("adapters.llm._client") as mock_client:
        mock_client.messages.create = AsyncMock(side_effect=_capture)
        asyncio.get_event_loop_policy().new_event_loop().run_until_complete(
            chat([Message(role="user", content="hi")], system="sys")
        )
    return captured


def test_chat_kwargs_are_accepted_by_the_installed_sdk():
    """
    Bind what chat() sends to the real signature of messages.create. If the
    installed SDK drops or renames a parameter, this fails at test time rather
    than on an elderly user's voice note.
    """
    import inspect

    from anthropic import AsyncAnthropic

    sig = inspect.signature(AsyncAnthropic(api_key="test-key").messages.create)
    kwargs = _kwargs_chat_builds()
    assert kwargs, "captured no kwargs — the capture harness itself is broken"

    accepts_var_kw = any(
        p.kind is inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
    )
    unknown = [k for k in kwargs if k not in sig.parameters]
    assert not unknown or accepts_var_kw, (
        f"adapters.llm.chat passes {unknown} which the installed anthropic SDK "
        f"does not accept. This is the 2026-10-02 production outage: pin the SDK "
        f"in requirements.txt, or update chat() to the new signature and re-run "
        f"the eval set (sampling parameters change model behaviour)."
    )


def test_installed_anthropic_major_version_is_pinned_below_1():
    """
    requirements.txt pins anthropic<1.0 because 1.x changed the Messages
    signature. If someone lifts that bound, adapters/llm.py has to be adapted
    and TC-01..TC-11 re-run — not discovered in production.
    """
    from importlib.metadata import version

    major = int(version("anthropic").split(".")[0])
    assert major < 1, (
        f"anthropic {version('anthropic')} is installed but adapters/llm.py is "
        "written against the 0.x Messages signature (it passes `temperature`, "
        "removed in 1.x). Update llm.py and re-run the eval set before lifting "
        "the pin in requirements.txt."
    )
