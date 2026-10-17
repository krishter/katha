"""
core/pricing.py (docs/specs/COST_TO_SERVE.md §12.1): each vendor's cost
from known units, cache reads and writes priced separately, and unpriced
calls returned as None rather than zero.
"""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from core import pricing

_FX = Decimal("95.06")


@pytest.fixture(autouse=True)
def _fixed_rate(monkeypatch):
    monkeypatch.setattr("config.settings.USD_INR_RATE", 95.06)


def _row(vendor, operation, units, model=None):
    return SimpleNamespace(vendor=vendor, operation=operation, model=model, units=units)


def _inr(value: Decimal) -> Decimal:
    return value.quantize(Decimal("0.0001"))


def test_sonnet_46_input_and_output():
    row = _row(
        "anthropic",
        "dialogue",
        {"input_tokens": 1_000_000, "output_tokens": 100_000},
        "claude-sonnet-4-6",
    )
    # $3 + $1.50 = $4.50
    assert pricing.cost_inr(row) == Decimal("4.5") * _FX
    assert pricing.cost_breakdown(row) == {"claude_dialogue": Decimal("4.5") * _FX}


def test_cache_reads_and_writes_are_priced_separately():
    units = {
        "input_tokens": 200_000,
        "output_tokens": 0,
        "cache_creation_input_tokens": 400_000,
        "cache_read_input_tokens": 1_000_000,
    }
    five_min = pricing.cost_inr(
        _row("anthropic", "dialogue", units, "claude-sonnet-5-5")
    )
    # 0.2 x $2 + 0.4 x $2.50 + 1.0 x $0.10 = $1.50
    assert five_min == Decimal("1.5") * _FX

    one_hour = pricing.cost_inr(
        _row("anthropic", "dialogue", {**units, "cache_ttl": "1h"}, "claude-sonnet-5-5")
    )
    # The write moves to the 1h price: 0.2 x $2 + 0.4 x $4 + 1.0 x $0.10 = $2.10
    assert one_hour == Decimal("2.1") * _FX


def test_haiku_55_and_its_long_prompt_tier():
    short = _row(
        "anthropic",
        "extraction",
        {"input_tokens": 100_000, "output_tokens": 10_000},
        "claude-haiku-5-5",
    )
    # At the threshold, not over it: 0.1 x $0.10 + 0.01 x $0.50 = $0.015
    assert pricing.cost_inr(short) == Decimal("0.015") * _FX

    # Over 100,000 prompt tokens — counting cache reads — the whole request,
    # output included, moves to the higher prices.
    long = _row(
        "anthropic",
        "extraction",
        {
            "input_tokens": 60_000,
            "cache_read_input_tokens": 40_001,
            "output_tokens": 10_000,
        },
        "claude-haiku-5-5",
    )
    expected_usd = (
        Decimal(60_000) * Decimal("0.50")
        + Decimal(40_001) * Decimal("0.05")
        + Decimal(10_000) * Decimal("2.50")
    ) / Decimal(1_000_000)
    assert pricing.cost_inr(long) == expected_usd * _FX


@pytest.mark.parametrize(
    "operation,component",
    [
        ("dialogue", "claude_dialogue"),
        ("extraction", "claude_extraction"),
        ("extraction_retry", "claude_extraction"),
        ("entities", "claude_entities"),
        ("consolidation", "claude_other"),
        ("eval", "claude_other"),
    ],
)
def test_claude_operations_map_to_components(operation, component):
    row = _row("anthropic", operation, {"input_tokens": 1}, "claude-sonnet-4-6")
    assert list(pricing.cost_breakdown(row)) == [component]


def test_stt_is_priced_per_billed_second():
    row = _row(
        "sarvam", "stt", {"seconds_received": 600, "seconds_billed": 360}, "saaras:v3"
    )
    # ₹29.40 an hour, on the 6 minutes actually sent.
    assert pricing.cost_inr(row) == Decimal("2.94")


def test_stt_falls_back_to_seconds_received():
    row = _row("sarvam", "stt", {"seconds_received": 3600}, "saaras:v3")
    assert pricing.cost_inr(row) == Decimal("29.40")


@pytest.mark.parametrize(
    "model,expected", [("bulbul:v3", "3.00"), ("bulbul:v4-flash", "2.00")]
)
def test_tts_is_priced_per_character(model, expected):
    row = _row("sarvam", "tts_reply", {"characters": 1000}, model)
    assert _inr(pricing.cost_inr(row)) == Decimal(expected)


def test_twilio_message_fee_in_rupees():
    row = _row("twilio", "inbound", {"messages": 1})
    assert pricing.cost_breakdown(row) == {"twilio": Decimal("0.005") * _FX}


@pytest.mark.parametrize(
    "category,meta_fee", [("utility", "0.115"), ("marketing", "0.8631")]
)
def test_template_sends_add_metas_fee(category, meta_fee):
    row = _row("twilio", "outbound_card", {"messages": 1, "meta_category": category})
    assert pricing.cost_breakdown(row) == {
        "twilio": Decimal("0.005") * _FX,
        "meta": Decimal(meta_fee),
    }


def test_free_form_reply_has_no_meta_fee():
    row = _row("twilio", "outbound_voice", {"messages": 1})
    assert "meta" not in pricing.cost_breakdown(row)


@pytest.mark.parametrize(
    "row",
    [
        _row("anthropic", "dialogue", {"input_tokens": 10}, "claude-opus-9"),
        _row("sarvam", "tts_reply", {"characters": 10}, "bulbul:v9"),
        _row("sarvam", "stt", {"chunks": 1}, "saaras:v3"),  # duration unknown
        _row("twilio", "outbound_card", {"messages": 1, "meta_category": "unknown"}),
        _row("twilio", "outbound_template", {"messages": 1}),
        _row("elevenlabs", "tts_reply", {"characters": 10}, "v1"),
    ],
)
def test_unpriced_calls_return_none_not_zero(row):
    assert pricing.cost_inr(row) is None
    assert pricing.cost_breakdown(row) is None


def test_is_priced():
    assert pricing.is_priced("anthropic", "claude-haiku-5-5")
    assert not pricing.is_priced("anthropic", "claude-haiku-9")
    assert pricing.is_priced("sarvam", "bulbul:v4-flash")
    assert pricing.is_priced("twilio", None)


def test_prices_are_dated():
    assert pricing.PRICES_AS_OF == "2026-10-16"


def test_eval_sarvam_rows_are_priced_by_their_units():
    """The listening kit records Sarvam calls as eval; they must not read
    as unpriced."""
    tts = _row("sarvam", "eval", {"characters": 1000}, "bulbul:v4-flash")
    stt = _row(
        "sarvam",
        "eval",
        {"seconds_received": 3600, "seconds_billed": 3600},
        "saaras:v3",
    )
    assert pricing.cost_breakdown(tts) == {"sarvam_tts": Decimal("2")}
    assert pricing.cost_breakdown(stt) == {"sarvam_stt": Decimal("29.40")}
