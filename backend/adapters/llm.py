from __future__ import annotations

import logging
import time
from dataclasses import dataclass

from anthropic import APIError, AsyncAnthropic

from config import settings
from core import usage

logger = logging.getLogger(__name__)

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
    model: str = ""
    cache_creation_input_tokens: int = 0
    cache_read_input_tokens: int = 0


def _count(value) -> int:
    """A token count from the SDK's usage object; absent or None is 0."""
    return value if isinstance(value, int) else 0


def _legacy_sampling(model: str) -> bool:
    """
    Whether this model takes the sampling parameters the code always sent.

    The 4.x models accept `temperature`; the 5.5 models return a 400 for any
    non-default value. Kept as one rule so the rollback path
    (LLM_*_MODEL=claude-sonnet-4-6) sends exactly what it sent before.
    """
    return model.startswith(("claude-sonnet-4", "claude-haiku-4"))


def _thinking_and_effort(model: str, effort: str | None) -> dict:
    """
    The request fields that control thinking, per model
    (docs/specs/COST_TO_SERVE.md §6).

    - Sonnet 5.5 thinks up front by default. `between_tools` turns that off,
      which keeps dialogue latency and output cost down; it is accepted at
      effort `high` or below, and `disabled` is a 400 on this model.
    - Haiku 5.5 thinks adaptively by default; effort is the lever.
    - The 4.x models get neither field, exactly as before.

    `between_tools` is not in the pinned SDK's typed thinking options, so it
    travels in `extra_body`; `output_config` is a typed parameter.
    """
    if _legacy_sampling(model):
        return {}
    fields: dict = {}
    if effort:
        fields["output_config"] = {"effort": effort}
    if model.startswith("claude-sonnet-5-5"):
        fields["extra_body"] = {"thinking": {"type": "between_tools"}}
    return fields


def _system_field(system: str | None, cache_system_prefix: str | None):
    """
    The `system` request field.

    With a cacheable prefix and caching on, it is two text blocks and the
    first carries the cache breakpoint: the prefix (Layers 1-3, stable for a
    whole session once Layer 3 is frozen) is written once and read on every
    later turn, while the per-turn rest stays uncached. Otherwise it is the
    single string it always was, prefix and rest joined exactly as
    build_system_prompt joins its layers.
    """
    if cache_system_prefix is None:
        return system
    if settings.PROMPT_CACHE_ENABLED:
        blocks: list[dict] = [
            {
                "type": "text",
                "text": cache_system_prefix,
                "cache_control": {
                    "type": "ephemeral",
                    "ttl": settings.PROMPT_CACHE_TTL,
                },
            }
        ]
        if system:
            blocks.append({"type": "text", "text": system})
        return blocks
    return "\n\n".join(part for part in (cache_system_prefix, system) if part)


def _response_text(response) -> str:
    """
    The reply: every text block, in order.

    Responses from thinking models can open with thinking blocks, so the
    first block is no longer reliably the text — `content[0].text` would
    raise on them or return the wrong thing.
    """
    return "".join(
        getattr(block, "text", "") or ""
        for block in response.content or []
        if getattr(block, "type", "text") == "text"
    )


async def chat(
    messages: list[Message],
    system: str | None = None,
    max_tokens: int = 500,
    *,
    model: str,
    purpose: str,
    effort: str | None = None,
    cache_system_prefix: str | None = None,
) -> LLMResponse:
    """
    Send messages to Claude and return the response.

    `model` is required so every call site names what it runs on; the
    choices come from settings (LLM_DIALOGUE_MODEL, LLM_EXTRACTION_MODEL),
    which is what lets a model switch roll back with an env change.

    `purpose` is what the call is for — dialogue, extraction,
    extraction_retry, entities, consolidation or eval — and becomes the
    operation on its api_usage row.

    `cache_system_prefix`, when given, is the stable start of the system
    prompt, and `system` is the per-turn rest; see _system_field.
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
        "model": model,
        "max_tokens": max_tokens,
        "messages": user_messages,
    }
    if _legacy_sampling(model):
        kwargs["temperature"] = 0.7
    kwargs.update(_thinking_and_effort(model, effort))
    system_field = _system_field(system_content, cache_system_prefix)
    if system_field is not None:
        kwargs["system"] = system_field
    cached = isinstance(system_field, list)

    started = time.monotonic()
    try:
        response = await _client.messages.create(**kwargs)
    except APIError as exc:
        raise RuntimeError(f"Anthropic API error: {exc}") from exc
    latency_ms = int((time.monotonic() - started) * 1000)

    input_tokens = _count(response.usage.input_tokens)
    output_tokens = _count(response.usage.output_tokens)
    cache_writes = _count(getattr(response.usage, "cache_creation_input_tokens", 0))
    cache_reads = _count(getattr(response.usage, "cache_read_input_tokens", 0))
    units = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "cache_creation_input_tokens": cache_writes,
        "cache_read_input_tokens": cache_reads,
        "latency_ms": latency_ms,
        "stop_reason": response.stop_reason,
    }
    if cached:
        # Writes are priced by TTL, so the ledger needs to know which.
        units["cache_ttl"] = settings.PROMPT_CACHE_TTL
    await usage.record("anthropic", purpose, units, model=model)

    text = _response_text(response)
    stop_reason = response.stop_reason or "end_turn"
    if not text:
        # Thinking used the whole budget and no text came back. Reported as
        # a truncation so conversation_policy takes the path it already has
        # for a reply cut off at max_tokens.
        stop_reason = "max_tokens"

    if stop_reason == "max_tokens":
        logger.warning(
            "LLM reply hit max_tokens=%d (model=%s, output_tokens=%d) — the "
            "text is cut off mid-generation%s",
            max_tokens,
            model,
            output_tokens,
            " (no text at all: thinking used the budget)" if not text else "",
        )

    return LLMResponse(
        content=text,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        stop_reason=stop_reason,
        model=model,
        cache_creation_input_tokens=cache_writes,
        cache_read_input_tokens=cache_reads,
    )
