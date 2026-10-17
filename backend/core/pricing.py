"""
What each paid call costs, in rupees (docs/specs/COST_TO_SERVE.md §12.1).

Costs are never stored. The ledger (api_usage) keeps counts only, and
everything that reports a cost multiplies those counts by the tables here
at read time — so a price change is an edit to this file, not a backfill,
and every report can say which table it used (PRICES_AS_OF).

A call this file has no price for returns None rather than 0. A model
switch that nobody priced must show up as "unpriced" on the admin page,
not quietly read as free.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any, Mapping

from config import settings

PRICES_AS_OF = "2026-10-16"

_MTOK = Decimal(1_000_000)

# ---------------------------------------------------------------------------
# Anthropic — USD per million tokens.
# Source: https://platform.claude.com/docs/en/about-claude/pricing
# (checked 2026-10-16). Cache reads on Sonnet 5.5 are 0.05x base input.
# ---------------------------------------------------------------------------
_ANTHROPIC_USD_PER_MTOK: dict[str, dict[str, Decimal]] = {
    "claude-sonnet-4-6": {
        "input": Decimal("3"),
        "output": Decimal("15"),
        "write_5m": Decimal("3.75"),
        "write_1h": Decimal("6"),
        "read": Decimal("0.30"),
    },
    "claude-sonnet-5-5": {
        "input": Decimal("2"),
        "output": Decimal("10"),
        "write_5m": Decimal("2.50"),
        "write_1h": Decimal("4"),
        "read": Decimal("0.10"),
    },
    "claude-haiku-5-5": {
        "input": Decimal("0.10"),
        "output": Decimal("0.50"),
        "write_5m": Decimal("0.125"),
        "write_1h": Decimal("0.20"),
        "read": Decimal("0.01"),
    },
}

# Haiku 5.5 is priced by prompt length. A request whose prompt — input plus
# cache reads plus cache writes — is over 100,000 tokens pays these prices
# for the whole request, including its output. Same source. Unreachable at
# Katha's prompt sizes, implemented so it can never be silently mispriced.
_LONG_PROMPT_THRESHOLD = 100_000
_ANTHROPIC_LONG_PROMPT_USD_PER_MTOK: dict[str, dict[str, Decimal]] = {
    "claude-haiku-5-5": {
        "input": Decimal("0.50"),
        "output": Decimal("2.50"),
        "write_5m": Decimal("0.625"),
        "write_1h": Decimal("1"),
        "read": Decimal("0.05"),
    },
}

# ---------------------------------------------------------------------------
# Sarvam — INR. Source: https://www.sarvam.ai/api-pricing (2026-10-16).
# ---------------------------------------------------------------------------
_SARVAM_STT_INR_PER_HOUR: dict[str, Decimal] = {"saaras:v3": Decimal("29.40")}
_SARVAM_TTS_INR_PER_10K_CHARS: dict[str, Decimal] = {
    "bulbul:v3": Decimal("30"),
    "bulbul:v4-flash": Decimal("20"),
}

# ---------------------------------------------------------------------------
# Twilio — USD per WhatsApp message, inbound or outbound, on top of Meta's
# fee. Source: https://www.twilio.com/en-us/whatsapp/pricing (2026-10-16).
# ---------------------------------------------------------------------------
_TWILIO_USD_PER_MESSAGE = Decimal("0.005")

# ---------------------------------------------------------------------------
# Meta — INR per delivered template message to an Indian number, by
# category. Free-form replies inside the 24-hour window ("service") are
# free. Source: https://developers.facebook.com/docs/whatsapp/pricing
# (2026-10-16).
# ---------------------------------------------------------------------------
_META_INR_PER_TEMPLATE: dict[str, Decimal] = {
    "utility": Decimal("0.115"),
    "marketing": Decimal("0.8631"),
    "service": Decimal("0"),
}

# Which admin-page component an Anthropic operation belongs to.
_CLAUDE_COMPONENT = {
    "dialogue": "claude_dialogue",
    "extraction": "claude_extraction",
    "extraction_retry": "claude_extraction",
    "entities": "claude_entities",
}

COMPONENTS = (
    "claude_dialogue",
    "claude_extraction",
    "claude_entities",
    "claude_other",
    "sarvam_stt",
    "sarvam_tts",
    "twilio",
    "meta",
)


def usd_inr_rate() -> Decimal:
    return Decimal(str(settings.USD_INR_RATE))


def _n(units: Mapping[str, Any], key: str) -> Decimal | None:
    value = units.get(key)
    if value is None or isinstance(value, bool):
        return None
    try:
        return Decimal(str(value))
    except Exception:
        return None


def _anthropic_usd(model: str | None, units: Mapping[str, Any]) -> Decimal | None:
    if model not in _ANTHROPIC_USD_PER_MTOK:
        return None
    input_tokens = _n(units, "input_tokens") or Decimal(0)
    output_tokens = _n(units, "output_tokens") or Decimal(0)
    writes = _n(units, "cache_creation_input_tokens") or Decimal(0)
    reads = _n(units, "cache_read_input_tokens") or Decimal(0)

    prices = _ANTHROPIC_USD_PER_MTOK[model]
    prompt_tokens = input_tokens + writes + reads
    if (
        model in _ANTHROPIC_LONG_PROMPT_USD_PER_MTOK
        and prompt_tokens > _LONG_PROMPT_THRESHOLD
    ):
        prices = _ANTHROPIC_LONG_PROMPT_USD_PER_MTOK[model]

    # The ledger records which TTL a write used. A write with no TTL on
    # record came from the default 5-minute cache.
    ttl_1h = units.get("cache_ttl") == "1h"
    write_price = prices["write_1h"] if ttl_1h else prices["write_5m"]
    return (
        input_tokens * prices["input"]
        + output_tokens * prices["output"]
        + writes * write_price
        + reads * prices["read"]
    ) / _MTOK


def cost_breakdown(row: Any) -> dict[str, Decimal] | None:
    """
    INR by component for one api_usage row, or None if any part of it is
    unpriced. `row` is anything with vendor, operation, model and units
    attributes (the ORM row, or a SimpleNamespace in tests).

    A Twilio template send is two charges — Twilio's per-message fee and
    Meta's per-template fee — so it comes back as two components.
    """
    vendor = row.vendor
    operation = row.operation
    model = row.model
    units = row.units or {}
    fx = usd_inr_rate()

    if vendor == "anthropic":
        usd = _anthropic_usd(model, units)
        if usd is None:
            return None
        return {_CLAUDE_COMPONENT.get(operation, "claude_other"): usd * fx}

    if vendor == "sarvam":
        # Operator tools record Sarvam calls as "eval"; whether one was speech
        # to text or text to speech is then read from its units.
        if operation == "eval":
            operation = "stt" if "seconds_received" in units else "tts_eval"
        if operation == "stt":
            per_hour = _SARVAM_STT_INR_PER_HOUR.get(model or "")
            # What Sarvam bills is what was sent. Before pause compression
            # (C7) the two are the same.
            seconds = _n(units, "seconds_billed")
            if seconds is None:
                seconds = _n(units, "seconds_received")
            if per_hour is None or seconds is None:
                return None
            return {"sarvam_stt": seconds * per_hour / Decimal(3600)}
        if operation.startswith("tts"):
            per_10k = _SARVAM_TTS_INR_PER_10K_CHARS.get(model or "")
            characters = _n(units, "characters")
            if per_10k is None or characters is None:
                return None
            return {"sarvam_tts": characters * per_10k / Decimal(10_000)}
        return None

    if vendor == "twilio":
        messages = _n(units, "messages") or Decimal(1)
        out = {"twilio": messages * _TWILIO_USD_PER_MESSAGE * fx}
        if operation == "outbound_template" or units.get("meta_category"):
            category = units.get("meta_category")
            fee = _META_INR_PER_TEMPLATE.get(category or "")
            if fee is None:
                return None
            out["meta"] = messages * fee
        return out

    return None


def cost_inr(row: Any) -> Decimal | None:
    """Total INR for one api_usage row, or None if it is unpriced."""
    parts = cost_breakdown(row)
    if parts is None:
        return None
    return sum(parts.values(), Decimal(0))


def is_priced(vendor: str, model: str | None) -> bool:
    """Whether a (vendor, model) pair has a price here at all."""
    if vendor == "anthropic":
        return model in _ANTHROPIC_USD_PER_MTOK
    if vendor == "sarvam":
        return (
            model in _SARVAM_STT_INR_PER_HOUR or model in _SARVAM_TTS_INR_PER_10K_CHARS
        )
    return vendor == "twilio"
