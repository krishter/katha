from __future__ import annotations

import logging
from dataclasses import dataclass

from anthropic import APIError, AsyncAnthropic

from config import settings

logger = logging.getLogger(__name__)

_MODEL = "claude-sonnet-4-6"


@dataclass
class Message:
    role: str
    content: str


@dataclass
class LLMResponse:
    content: str
    input_tokens: int
    output_tokens: int


async def chat(
    messages: list[Message],
    system: str | None = None,
) -> LLMResponse:
    """Send messages to Claude Sonnet 4.6 and return the response."""
    client = AsyncAnthropic(api_key=settings.ANTHROPIC_API_KEY)

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
        "max_tokens": 500,
        "temperature": 0.7,
        "messages": user_messages,
    }
    if system_content is not None:
        kwargs["system"] = system_content

    try:
        response = await client.messages.create(**kwargs)
    except APIError as exc:
        raise RuntimeError(f"Anthropic API error: {exc}") from exc

    return LLMResponse(
        content=response.content[0].text,
        input_tokens=response.usage.input_tokens,
        output_tokens=response.usage.output_tokens,
    )
