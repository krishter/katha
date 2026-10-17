from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from adapters.llm import LLMResponse, Message, chat


def _make_mock_response(
    text: str = "Hello! How can I help you?", stop_reason: str = "end_turn"
) -> MagicMock:
    response = MagicMock()
    response.content = [MagicMock(type="text", text=text)]
    response.usage = MagicMock(input_tokens=10, output_tokens=8)
    response.stop_reason = stop_reason
    return response


def _mock_client(response=None, side_effect=None):
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response, side_effect=side_effect)
    return client


async def test_chat_returns_llm_response():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        result = await chat(
            [Message(role="user", content="Hello")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

    assert isinstance(result, LLMResponse)
    assert result.content == "Hello! How can I help you?"
    assert result.content != ""


async def test_chat_returns_positive_token_counts():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        result = await chat(
            [Message(role="user", content="Hello")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

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
            ],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

    call_kwargs = mock_client.messages.create.call_args.kwargs
    assert call_kwargs["system"] == "You are a helpful assistant."
    # System message must not appear in the messages list
    for msg in call_kwargs["messages"]:
        assert msg["role"] != "system"


async def test_chat_defaults_max_tokens_to_500():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        await chat(
            [Message(role="user", content="Hello")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

    assert mock_client.messages.create.call_args.kwargs["max_tokens"] == 500


async def test_chat_passes_through_custom_max_tokens():
    mock_client = _mock_client(response=_make_mock_response())

    with patch("adapters.llm._client", mock_client):
        await chat(
            [Message(role="user", content="Hello")],
            max_tokens=2000,
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

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
            await chat(
                [Message(role="user", content="Hello")],
                model="claude-sonnet-4-6",
                purpose="dialogue",
            )


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
            chat(
                [Message(role="user", content="hi")],
                system="sys",
                model="claude-sonnet-4-6",
                purpose="dialogue",
            )
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


async def test_chat_surfaces_stop_reason():
    """Callers need truncation as a fact from the API, not an inference from
    the shape of the text: an unclosed <response> on a complete generation
    is a malformed reply, while the same text on a truncated one is a good
    reply missing its tail (see conversation_policy.check_post_turn)."""
    mock_client = _mock_client(response=_make_mock_response(stop_reason="max_tokens"))

    with patch("adapters.llm._client", mock_client):
        result = await chat(
            [Message(role="user", content="Hello")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

    assert result.stop_reason == "max_tokens"


async def test_chat_defaults_stop_reason_when_api_omits_it():
    mock_client = _mock_client(response=_make_mock_response(stop_reason=None))

    with patch("adapters.llm._client", mock_client):
        result = await chat(
            [Message(role="user", content="Hello")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

    assert result.stop_reason == "end_turn"


# ── COST_TO_SERVE.md §6 and §8: models, thinking, caching ─────────────────────


def _blocks_response(*blocks, stop_reason="end_turn", **usage_fields):
    response = MagicMock()
    response.content = list(blocks)
    response.usage = SimpleNamespace(
        input_tokens=usage_fields.get("input_tokens", 10),
        output_tokens=usage_fields.get("output_tokens", 8),
        cache_creation_input_tokens=usage_fields.get("cache_creation_input_tokens", 0),
        cache_read_input_tokens=usage_fields.get("cache_read_input_tokens", 0),
    )
    response.stop_reason = stop_reason
    return response


def _text(text):
    return SimpleNamespace(type="text", text=text)


def _thinking():
    return SimpleNamespace(type="thinking", thinking="", signature="sig")


async def _sent_kwargs(model, **chat_kwargs):
    client = _mock_client(response=_blocks_response(_text("hi")))
    with patch("adapters.llm._client", client):
        await chat(
            [Message(role="user", content="Hello")],
            model=model,
            purpose="dialogue",
            **chat_kwargs,
        )
    return client.messages.create.call_args.kwargs


async def test_model_and_purpose_are_required():
    with pytest.raises(TypeError):
        await chat([Message(role="user", content="hi")], purpose="dialogue")
    with pytest.raises(TypeError):
        await chat([Message(role="user", content="hi")], model="claude-sonnet-4-6")


async def test_the_requested_model_is_sent():
    kwargs = await _sent_kwargs("claude-haiku-5-5")
    assert kwargs["model"] == "claude-haiku-5-5"


@pytest.mark.parametrize("model", ["claude-sonnet-4-6", "claude-haiku-4-5"])
async def test_legacy_models_get_temperature_and_nothing_new(model):
    """The rollback path sends exactly what it always sent."""
    kwargs = await _sent_kwargs(model, effort="medium")
    assert kwargs["temperature"] == 0.7
    assert "output_config" not in kwargs
    assert "extra_body" not in kwargs


async def test_sonnet_55_gets_between_tools_thinking_and_effort():
    kwargs = await _sent_kwargs("claude-sonnet-5-5", effort="medium")
    assert "temperature" not in kwargs  # a 400 on the 5.5 models
    assert kwargs["output_config"] == {"effort": "medium"}
    # Not "disabled", which this model rejects.
    assert kwargs["extra_body"] == {"thinking": {"type": "between_tools"}}


async def test_haiku_55_gets_effort_and_adaptive_thinking():
    kwargs = await _sent_kwargs("claude-haiku-5-5", effort="low")
    assert "temperature" not in kwargs
    assert kwargs["output_config"] == {"effort": "low"}
    assert "extra_body" not in kwargs  # thinking left unset: adaptive


async def test_text_is_taken_from_text_blocks_when_thinking_leads():
    client = _mock_client(
        response=_blocks_response(
            _thinking(), _text("<response>Hel"), _text("lo</response>")
        )
    )
    with patch("adapters.llm._client", client):
        result = await chat(
            [Message(role="user", content="hi")],
            model="claude-haiku-5-5",
            purpose="extraction",
        )
    assert result.content == "<response>Hello</response>"
    assert result.stop_reason == "end_turn"


async def test_no_text_at_all_is_reported_as_truncation():
    """Thinking used the whole budget: conversation_policy's truncation path."""
    client = _mock_client(
        response=_blocks_response(_thinking(), stop_reason="max_tokens")
    )
    with patch("adapters.llm._client", client):
        result = await chat(
            [Message(role="user", content="hi")],
            model="claude-haiku-5-5",
            purpose="extraction",
        )
    assert result.content == ""
    assert result.stop_reason == "max_tokens"


async def test_system_is_two_blocks_with_a_cache_breakpoint_when_enabled(monkeypatch):
    monkeypatch.setattr("adapters.llm.settings.PROMPT_CACHE_ENABLED", True)
    monkeypatch.setattr("adapters.llm.settings.PROMPT_CACHE_TTL", "1h")
    kwargs = await _sent_kwargs(
        "claude-sonnet-5-5", system="LAYERS 4-5", cache_system_prefix="LAYERS 1-3"
    )
    assert kwargs["system"] == [
        {
            "type": "text",
            "text": "LAYERS 1-3",
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {"type": "text", "text": "LAYERS 4-5"},
    ]


async def test_system_is_one_string_when_caching_is_disabled(monkeypatch):
    monkeypatch.setattr("adapters.llm.settings.PROMPT_CACHE_ENABLED", False)
    kwargs = await _sent_kwargs(
        "claude-sonnet-5-5", system="LAYERS 4-5", cache_system_prefix="LAYERS 1-3"
    )
    # Joined exactly as build_system_prompt joins its layers.
    assert kwargs["system"] == "LAYERS 1-3\n\nLAYERS 4-5"


async def test_cache_usage_is_carried_through_and_recorded(monkeypatch, usage_rows):
    monkeypatch.setattr("adapters.llm.settings.PROMPT_CACHE_ENABLED", True)
    monkeypatch.setattr("adapters.llm.settings.PROMPT_CACHE_TTL", "1h")
    client = _mock_client(
        response=_blocks_response(
            _text("hi"),
            input_tokens=40,
            output_tokens=12,
            cache_creation_input_tokens=0,
            cache_read_input_tokens=1500,
        )
    )
    with patch("adapters.llm._client", client):
        result = await chat(
            [Message(role="user", content="hi")],
            system="per turn",
            cache_system_prefix="stable",
            model="claude-sonnet-5-5",
            purpose="dialogue",
        )
    assert result.model == "claude-sonnet-5-5"
    assert result.cache_read_input_tokens == 1500
    assert result.cache_creation_input_tokens == 0
    (row,) = usage_rows
    assert row["model"] == "claude-sonnet-5-5"
    assert row["units"]["cache_read_input_tokens"] == 1500
    # Writes are priced by TTL, so the ledger has to say which.
    assert row["units"]["cache_ttl"] == "1h"


def test_new_model_kwargs_are_accepted_by_the_installed_sdk():
    """The 5.5 path's fields bind to the pinned SDK's signature too."""
    import asyncio
    import inspect

    from anthropic import AsyncAnthropic

    sig = inspect.signature(AsyncAnthropic(api_key="test-key").messages.create)
    kwargs = asyncio.new_event_loop().run_until_complete(
        _sent_kwargs(
            "claude-sonnet-5-5",
            effort="medium",
            system="b",
            cache_system_prefix="a",
        )
    )
    unknown = [k for k in kwargs if k not in sig.parameters]
    assert not unknown, unknown
