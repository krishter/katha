"""
The usage ledger (docs/specs/COST_TO_SERVE.md §5): scope propagation, the
never-raises rule, the no-personal-data rule, and that every adapter
records its own calls.

The autouse `usage_rows` fixture in backend/conftest.py captures ledger
writes in memory; tests that care ask for it by name.
"""

from __future__ import annotations

import base64
import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core import usage

_SESSION = uuid.uuid4()
_TURN = uuid.uuid4()


# ── scope ─────────────────────────────────────────────────────────────────────


async def test_record_carries_the_current_scope(usage_rows):
    with usage.usage_scope(user_id="u1", session_id=_SESSION):
        await usage.record("anthropic", "dialogue", {"input_tokens": 5})

    assert usage_rows[0]["user_id"] == "u1"
    assert usage_rows[0]["session_id"] == _SESSION
    assert usage_rows[0]["turn_id"] is None


async def test_nested_scope_widens_without_losing_the_outer_ids(usage_rows):
    with usage.usage_scope(user_id="u1", session_id=_SESSION):
        with usage.usage_scope(turn_id=_TURN):
            await usage.record("sarvam", "tts_reply", {"characters": 40})
        await usage.record("twilio", "outbound_voice", {"messages": 1})

    inner, outer = usage_rows
    assert (inner["user_id"], inner["session_id"], inner["turn_id"]) == (
        "u1",
        _SESSION,
        _TURN,
    )
    # Leaving the inner block drops the turn but keeps the session.
    assert outer["turn_id"] is None and outer["session_id"] == _SESSION


async def test_scope_is_reset_after_the_block(usage_rows):
    with usage.usage_scope(user_id="u1"):
        pass
    await usage.record("twilio", "inbound", {"messages": 1})
    assert usage_rows[0]["user_id"] is None


async def test_string_session_ids_are_stored_as_uuids(usage_rows):
    with usage.usage_scope(session_id=str(_SESSION)):
        await usage.record("sarvam", "stt", {"seconds_received": 3.0})
    assert usage_rows[0]["session_id"] == _SESSION


async def test_background_extraction_sets_its_own_scope(usage_rows):
    """run_extraction_for_turn runs after the response, so it must set the
    scope itself rather than rely on the webhook's."""
    from core import orchestrator

    seen = {}

    async def _inner(*args, **kwargs):
        seen.update(usage.current_scope())

    state = SimpleNamespace(user_id="u9", goal_met=False)
    with patch.object(orchestrator, "_run_extraction_for_turn", new=_inner):
        await orchestrator.run_extraction_for_turn(
            _TURN, str(_SESSION), state, None, None, "t", "r", None
        )

    assert seen == {"user_id": "u9", "session_id": str(_SESSION), "turn_id": _TURN}


async def test_session_outputs_set_their_own_scope():
    from core import orchestrator

    seen = {}

    async def _inner(*args, **kwargs):
        seen.update(usage.current_scope())

    with patch.object(orchestrator, "_produce_session_outputs", new=_inner):
        await orchestrator.produce_session_outputs(str(_SESSION), "u7", None)

    assert seen == {"user_id": "u7", "session_id": str(_SESSION)}


# ── never raises, never stores text ───────────────────────────────────────────


async def test_record_swallows_database_errors(monkeypatch, caplog):
    async def _broken(row):
        raise RuntimeError("database is down")

    monkeypatch.setattr(usage, "_write", _broken)
    # Must not raise: recording usage can never cost a turn.
    await usage.record("anthropic", "dialogue", {"input_tokens": 1})
    assert "could not record anthropic/dialogue" in caplog.text


async def test_status_update_swallows_database_errors(monkeypatch):
    async def _broken(*args):
        raise RuntimeError("database is down")

    monkeypatch.setattr(usage, "_set_status", _broken)
    await usage.record_status("SM1", "failed", "63016")


async def test_inbound_check_failure_skips_rather_than_raises(monkeypatch, usage_rows):
    async def _broken(sid):
        raise RuntimeError("database is down")

    monkeypatch.setattr(usage, "_inbound_already_recorded", _broken)
    await usage.record_inbound("SMx")
    assert usage_rows == []


async def test_units_keep_counts_and_labels_and_drop_text(usage_rows):
    await usage.record(
        "anthropic",
        "dialogue",
        {
            "input_tokens": 1200,
            "latency_ms": 840.5,
            "stop_reason": "end_turn",
            "cache_ttl": "1h",
            # None of these may ever reach the ledger.
            "transcript": "I was born in Madurai in 1948.",
            "to": "+919876543210",
            "stop_reason_detail": "end_turn",
            "missing": None,
        },
    )
    assert usage_rows[0]["units"] == {
        "input_tokens": 1200,
        "latency_ms": 840.5,
        "stop_reason": "end_turn",
        "cache_ttl": "1h",
    }


def test_a_long_label_is_dropped_even_on_an_allowed_key():
    assert usage.clean_units({"stop_reason": "x" * 200}) == {}


def test_ledger_model_has_no_text_columns():
    """The table shape itself is part of the no-personal-data rule."""
    from models.api_usage import ApiUsage

    assert set(ApiUsage.__table__.columns.keys()) == {
        "id",
        "created_at",
        "user_id",
        "session_id",
        "turn_id",
        "vendor",
        "operation",
        "model",
        "units",
        "message_sid",
        "status",
        "error_code",
    }


# ── status callbacks and inbound idempotency ──────────────────────────────────


async def test_status_callback_updates_the_row_by_sid(usage_rows):
    await usage.record_outbound("outbound_voice", "SM1")
    await usage.record_outbound("outbound_text", "SM2")

    await usage.record_status("SM1", "failed", "63016")

    first, second = usage_rows
    assert (first["status"], first["error_code"]) == ("failed", "63016")
    assert second.get("status") is None


async def test_inbound_is_recorded_once_per_sid(usage_rows):
    await usage.record_inbound("SMin")
    await usage.record_inbound("SMin")
    await usage.record_inbound("SMother")
    assert [r["message_sid"] for r in usage_rows] == ["SMin", "SMother"]


async def test_template_category_comes_from_settings(monkeypatch, usage_rows):
    monkeypatch.setattr(
        "config.settings.META_TEMPLATE_CATEGORIES",
        '{"HXcard": "utility", "HXwelcome": "marketing"}',
    )
    await usage.record_outbound("outbound_card", "SM1", "HXcard")
    await usage.record_outbound("outbound_template", "SM2", "HXwelcome")
    await usage.record_outbound("outbound_template", "SM3", "HXnobody")
    await usage.record_outbound("outbound_text", "SM4")

    categories = [r["units"].get("meta_category") for r in usage_rows]
    assert categories == ["utility", "marketing", "unknown", None]


# ── every adapter records its own calls ───────────────────────────────────────


async def test_llm_records_tokens_cache_counts_and_latency(usage_rows):
    from adapters.llm import Message, chat

    response = MagicMock()
    response.content = [MagicMock(type="text", text="<response>Hello</response>")]
    response.usage = SimpleNamespace(
        input_tokens=1500,
        output_tokens=60,
        cache_creation_input_tokens=0,
        cache_read_input_tokens=None,
    )
    response.stop_reason = "end_turn"
    client = MagicMock()
    client.messages.create = AsyncMock(return_value=response)

    with (
        patch("adapters.llm._client", client),
        usage.usage_scope(user_id="u1", session_id=_SESSION),
    ):
        await chat(
            [Message(role="user", content="hi")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )

    (row,) = usage_rows
    assert (row["vendor"], row["operation"], row["model"]) == (
        "anthropic",
        "dialogue",
        "claude-sonnet-4-6",
    )
    units = row["units"]
    assert units["input_tokens"] == 1500
    assert units["output_tokens"] == 60
    assert units["cache_creation_input_tokens"] == 0
    assert units["cache_read_input_tokens"] == 0
    assert units["stop_reason"] == "end_turn"
    assert isinstance(units["latency_ms"], int)
    assert row["user_id"] == "u1"


async def test_llm_purpose_is_required():
    from adapters.llm import Message, chat

    with pytest.raises(TypeError):
        await chat([Message(role="user", content="hi")])


async def test_llm_records_nothing_when_the_call_fails(usage_rows):
    from anthropic import APIError

    from adapters.llm import Message, chat

    client = MagicMock()
    client.messages.create = AsyncMock(
        side_effect=APIError("boom", request=MagicMock(), body=None)
    )
    with patch("adapters.llm._client", client), pytest.raises(RuntimeError):
        await chat(
            [Message(role="user", content="hi")],
            model="claude-sonnet-4-6",
            purpose="dialogue",
        )
    assert usage_rows == []


async def test_stt_records_seconds_and_chunks(usage_rows):
    from adapters.sarvam_stt import transcribe

    ok = MagicMock(status_code=200)
    ok.json.return_value = {
        "transcript": "text",
        "language_code": "hi-IN",
        "language_probability": 0.9,
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with (
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt_measured",
            new=AsyncMock(return_value=([b"a", b"b", b"c"], 71.234)),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient", return_value=client),
        patch("adapters.sarvam_stt.post_with_retry", new=AsyncMock(return_value=ok)),
    ):
        await transcribe(b"audio")

    (row,) = usage_rows
    assert (row["vendor"], row["operation"], row["model"]) == (
        "sarvam",
        "stt",
        "saaras:v3",
    )
    assert row["units"] == {
        "seconds_received": 71.23,
        "seconds_billed": 71.23,
        "chunks": 3,
    }


async def test_stt_with_unreadable_duration_records_no_seconds(usage_rows):
    from adapters.sarvam_stt import transcribe

    ok = MagicMock(status_code=200)
    ok.json.return_value = {
        "transcript": "text",
        "language_code": "hi-IN",
        "language_probability": 0.9,
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    with (
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt_measured",
            new=AsyncMock(return_value=([b"a"], None)),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient", return_value=client),
        patch("adapters.sarvam_stt.post_with_retry", new=AsyncMock(return_value=ok)),
    ):
        await transcribe(b"audio")

    assert usage_rows[0]["units"] == {"chunks": 1}


async def test_tts_records_characters_actually_sent(usage_rows):
    from adapters.sarvam_tts import synthesize

    ok = MagicMock(status_code=200)
    ok.json.return_value = {"audios": [base64.b64encode(b"RIFF").decode()]}
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=None)
    client.post = AsyncMock(return_value=ok)
    with patch("adapters.sarvam_tts.httpx.AsyncClient", return_value=client):
        await synthesize("a" * 3000, "hi-IN", purpose="tts_reply")

    (row,) = usage_rows
    assert (row["operation"], row["model"]) == ("tts_reply", "bulbul:v4-flash")
    # Truncated to 2,500 before sending, and billed on what was sent.
    assert row["units"] == {"characters": 2500}


async def test_tts_purpose_is_required():
    from adapters.sarvam_tts import synthesize

    with pytest.raises(TypeError):
        await synthesize("hello", "hi-IN")


async def test_stub_whatsapp_records_each_send(usage_rows):
    from adapters.whatsapp_stub import StubWhatsAppAdapter

    stub = StubWhatsAppAdapter()
    voice_sid, _key = await stub.send_voice_note("+91", b"ogg")
    text_sid = await stub.send_text("+91", "hi")
    template_sid = await stub.send_text("+91", "", template_sid="HXwelcome")
    card_sid = await stub.send_image("+91", "cards/x.png", template_sid="HXcard")

    assert [(r["operation"], r["message_sid"]) for r in usage_rows] == [
        ("outbound_voice", voice_sid),
        ("outbound_text", text_sid),
        ("outbound_template", template_sid),
        ("outbound_card", card_sid),
    ]
    # A free-form send carries no Meta category; a template always does.
    assert "meta_category" not in usage_rows[1]["units"]
    assert usage_rows[2]["units"]["meta_category"] == "unknown"


async def test_twilio_adapter_records_the_real_sid(usage_rows):
    from adapters.whatsapp import TwilioWhatsAppAdapter

    adapter = TwilioWhatsAppAdapter("AC", "token", "whatsapp:+1")
    adapter._client = MagicMock()
    adapter._client.messages.create.return_value = MagicMock(sid="SMreal")

    await adapter.send_text("+91", "hello")

    (row,) = usage_rows
    assert (row["vendor"], row["operation"], row["message_sid"]) == (
        "twilio",
        "outbound_text",
        "SMreal",
    )


async def test_stt_bills_the_compressed_seconds_and_keeps_the_original(
    monkeypatch, usage_rows
):
    from adapters.sarvam_stt import transcribe

    ok = MagicMock(status_code=200)
    ok.json.return_value = {
        "transcript": "text",
        "language_code": "hi-IN",
        "language_probability": 0.9,
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("adapters.sarvam_stt.settings.STT_TRIM_PAUSES", True)
    with (
        patch(
            "adapters.sarvam_stt.audio_split.compress_pauses",
            new=AsyncMock(return_value=(b"shorter", 90.0, 61.5)),
        ) as compress,
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt_measured",
            new=AsyncMock(return_value=([b"a", b"b", b"c"], 61.5)),
        ) as split,
        patch("adapters.sarvam_stt.httpx.AsyncClient", return_value=client),
        patch("adapters.sarvam_stt.post_with_retry", new=AsyncMock(return_value=ok)),
    ):
        await transcribe(b"original")

    compress.assert_awaited_once_with(b"original", 0.5)
    split.assert_awaited_once_with(b"shorter")  # the shortened audio is sent
    assert usage_rows[0]["units"] == {
        "seconds_received": 90.0,
        "seconds_billed": 61.5,
        "chunks": 3,
    }


async def test_stt_without_trimming_sends_the_note_as_received(monkeypatch, usage_rows):
    from adapters.sarvam_stt import transcribe

    ok = MagicMock(status_code=200)
    ok.json.return_value = {
        "transcript": "text",
        "language_code": "hi-IN",
        "language_probability": 0.9,
    }
    client = AsyncMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    monkeypatch.setattr("adapters.sarvam_stt.settings.STT_TRIM_PAUSES", False)
    with (
        patch(
            "adapters.sarvam_stt.audio_split.compress_pauses", new=AsyncMock()
        ) as compress,
        patch(
            "adapters.sarvam_stt.audio_split.split_for_stt_measured",
            new=AsyncMock(return_value=([b"a"], 20.0)),
        ),
        patch("adapters.sarvam_stt.httpx.AsyncClient", return_value=client),
        patch("adapters.sarvam_stt.post_with_retry", new=AsyncMock(return_value=ok)),
    ):
        await transcribe(b"original")

    compress.assert_not_awaited()
    assert usage_rows[0]["units"]["seconds_received"] == 20.0
    assert usage_rows[0]["units"]["seconds_billed"] == 20.0
