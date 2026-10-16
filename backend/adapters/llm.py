from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from anthropic import APIError, AsyncAnthropic

from config import settings
from core import usage

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"

# One client for the process, not one per call — reuses the connection pool
# and gets a consistent timeout/retry policy everywhere.
_client = AsyncAnthropic(
    api_key=settings.ANTHROPIC_API_KEY, timeout=20.0, max_retries=2
)


@dataclass
class Message:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int
    # Why the model stopped. "max_tokens" means the reply was cut off
    # mid-generation, which callers must know rather than infer from the
    # shape of the text: a reply truncated before its closing </response>
    # used to be indistinguishable from a reply the model never tagged,
    # and the two need opposite handling (see conversation_policy).
    stop_reason: str = "end_turn"


def _count(value) -> int:
    """A token count from the SDK's usage object; absent or None is 0."""
    return value if isinstance(value, int) else 0


async def chat(
    messages: list[Message],
    system: str | None = None,
    max_tokens: int = 500,
    *,
    purpose: str,
) -> LLMResponse:
    """
    Send messages to Claude Sonnet 4.6 and return the response.

    `purpose` is what the call is for — dialogue, extraction,
    extraction_retry, entities, consolidation or eval — and becomes the
    operation on its api_usage row. Required, so no call site can be
    unaccounted for.
    """
    # Separate any system-role message; explicit system= param takes precedence
    system_content = system
    user_messages = []
    for msg in messages:
        if msg.role == "system":
            if system_content is None:
                system_content = msg.content
        else:
            user_messages.append({"role": msg.role, "content": msg.content})

    kwargs: dict = {
        "model": _MODEL,
        "max_tokens": max_tokens,
        "temperature": 0.7,
        "messages": user_messages,
    }
    if system_content is not None:
        kwargs["system"] = system_content

    started = time.monotonic()
    try:
        response = await _client.messages.create(**kwargs)
    except APIError as exc:
        raise RuntimeError(f"Anthropic API error: {exc}") from exc
    latency_ms = int((time.monotonic() - started) * 1000)

    await usage.record(
        "anthropic",
        purpose,
        {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "cache_creation_input_tokens": _count(
                getattr(response.usage, "cache_creation_input_tokens", 0)
            ),
            "cache_read_input_tokens": _count(
                getattr(response.usage, "cache_read_input_tokens", 0)
            ),
            "latency_ms": latency_ms,
            "stop_reason": response.stop_reason,
        },
        model=_MODEL,
    )

    if response.stop_reason == "max_tokens":
        logger.warning(
            "LLM reply hit max_tokens=%d (output_tokens=%d) — the text is "
            "cut off mid-generation",
            max_tokens,
            response.usage.output_tokens,
        )

    return LLMResponse(
        content=response.content[0].text,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
        stop_reason=response.stop_reason or "end_turn",
    )
