"""
Memory card delivery: the template path and the status callback.

Both exist because of the same production failure. The adult child never
messages Katha's number, so no 24-hour window is ever open for them; every
free-form card was accepted by Twilio, rejected by Meta with 63016, and
recorded in the database as delivered because nothing was listening for
the rejection.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from core.delivery import record_card_delivery
from main import app
from models.db import get_db

client = TestClient(app)


# ── send_image: the template path (#4) ───────────────────────────────────────


def _adapter():
    from adapters.whatsapp import TwilioWhatsAppAdapter

    with patch("adapters.whatsapp.TwilioClient"):
        adapter = TwilioWhatsAppAdapter("AC", "token", "whatsapp:+1", "")
    adapter._client = MagicMock()
    adapter._client.messages.create = MagicMock(return_value=MagicMock(sid="SM1"))
    return adapter


async def test_send_image_uses_content_sid_when_a_template_is_given():
    adapter = _adapter()
    with patch(
        "adapters.whatsapp.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://s3/card.png"),
    ):
        await adapter.send_image(
            "+919876543210",
            "cards/x.png",
            caption="ignored when templated",
            template_sid="HXabc",
            template_variables={"2": "Lakshmi", "3": "Childhood"},
        )
    kwargs = adapter._client.messages.create.call_args.kwargs
    assert kwargs["content_sid"] == "HXabc"
    # Free-form fields must NOT be set alongside a template.
    assert "media_url" not in kwargs
    assert "body" not in kwargs


async def test_the_card_image_rides_in_as_a_template_variable():
    """The card differs every day, so the media cannot be baked into the
    approved template — it has to be a variable."""
    adapter = _adapter()
    with patch(
        "adapters.whatsapp.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://s3/card.png"),
    ):
        await adapter.send_image(
            "+919876543210",
            "cards/x.png",
            template_sid="HXabc",
            template_variables={"2": "Lakshmi"},
        )
    variables = json.loads(
        adapter._client.messages.create.call_args.kwargs["content_variables"]
    )
    assert variables["1"] == "https://s3/card.png"
    assert variables["2"] == "Lakshmi"


async def test_content_variables_are_json_not_a_python_repr():
    """str() on a dict gives {'1': 'x'}, which Twilio does not parse and
    silently substitutes nothing — the same trap send_text documents."""
    adapter = _adapter()
    with patch(
        "adapters.whatsapp.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://s3/card.png"),
    ):
        await adapter.send_image(
            "+91", "k", template_sid="HX", template_variables={"2": "Lakshmi"}
        )
    raw = adapter._client.messages.create.call_args.kwargs["content_variables"]
    assert "'" not in raw
    assert json.loads(raw)


async def test_send_image_without_a_template_still_sends_free_form():
    """Dev and sandbox have no approved template; that path must keep
    working, even though Meta rejects it in production."""
    adapter = _adapter()
    with patch(
        "adapters.whatsapp.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://s3/card.png"),
    ):
        await adapter.send_image("+91", "k", caption="hello")
    kwargs = adapter._client.messages.create.call_args.kwargs
    assert kwargs["media_url"] == ["https://s3/card.png"]
    assert kwargs["body"] == "hello"
    assert "content_sid" not in kwargs


async def test_every_send_carries_a_status_callback():
    adapter = _adapter()
    with patch(
        "adapters.whatsapp.storage.generate_presigned_url",
        new=AsyncMock(return_value="https://s3/card.png"),
    ):
        await adapter.send_image("+91", "k")
    callback = adapter._client.messages.create.call_args.kwargs["status_callback"]
    assert callback.endswith("/webhook/twilio/status")


# ── the status callback route (#6) ───────────────────────────────────────────


@pytest.fixture
def db():
    """Override get_db for one test, then put back whatever was there.

    Not `dependency_overrides.clear()`: test_webhook.py installs its own
    get_db override at module import time, so clearing the dict destroys
    it for every test that runs afterwards in the same session.
    """
    mock = AsyncMock()
    previous = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = lambda: mock
    try:
        yield mock
    finally:
        if previous is not None:
            app.dependency_overrides[get_db] = previous
        else:
            app.dependency_overrides.pop(get_db, None)


def _post_status(**params):
    from adapters.whatsapp_stub import get_whatsapp_adapter

    adapter = MagicMock()
    adapter.validate_signature = MagicMock(return_value=True)
    app.dependency_overrides[get_whatsapp_adapter] = lambda: adapter
    try:
        return client.post("/webhook/twilio/status", data=params)
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)


def test_status_callback_acknowledges_and_records(db):
    with patch(
        "api.routes.webhook.record_card_delivery", new=AsyncMock(return_value=True)
    ) as recorder:
        response = _post_status(
            MessageSid="SM1", MessageStatus="delivered", ErrorCode=""
        )
    assert response.status_code == 200
    recorder.assert_awaited_once()
    assert recorder.await_args.args[:3] == ("SM1", "delivered", None)


def test_status_callback_passes_the_error_code_through(db):
    with patch(
        "api.routes.webhook.record_card_delivery", new=AsyncMock(return_value=True)
    ) as recorder:
        _post_status(MessageSid="SM1", MessageStatus="failed", ErrorCode="63016")
    assert recorder.await_args.args[2] == "63016"


def test_status_callback_rejects_a_bad_signature(db):
    from adapters.whatsapp_stub import get_whatsapp_adapter

    adapter = MagicMock()
    adapter.validate_signature = MagicMock(return_value=False)
    app.dependency_overrides[get_whatsapp_adapter] = lambda: adapter
    try:
        response = client.post(
            "/webhook/twilio/status", data={"MessageSid": "SM1", "MessageStatus": "x"}
        )
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)
    assert response.status_code == 403


def test_status_callback_returns_200_even_when_recording_raises(db):
    """Twilio retries non-200, and a retry would hit the same error."""
    with patch(
        "api.routes.webhook.record_card_delivery",
        new=AsyncMock(side_effect=RuntimeError("boom")),
    ):
        response = _post_status(MessageSid="SM1", MessageStatus="delivered")
    assert response.status_code == 200


# ── what gets written (#6) ───────────────────────────────────────────────────


def _card(**kw):
    card = MagicMock()
    card.delivered_at = kw.get("delivered_at")
    card.delivery_status = kw.get("delivery_status")
    card.delivery_error_code = None
    return card


def _db_returning(card):
    db = AsyncMock()
    result = MagicMock()
    result.scalar_one_or_none.return_value = card
    db.execute = AsyncMock(return_value=result)
    db.add = MagicMock()  # Session.add is sync; AsyncMock would leak a coroutine
    return db


async def test_delivered_sets_the_timestamp():
    card = _card()
    db = _db_returning(card)
    assert await record_card_delivery("SM1", "delivered", None, db) is True
    assert card.delivery_status == "delivered"
    assert card.delivered_at is not None


async def test_sent_does_not_set_the_timestamp():
    """`sent` only means Meta accepted it from Twilio — it is exactly the
    status that used to be mistaken for delivery."""
    card = _card()
    db = _db_returning(card)
    await record_card_delivery("SM1", "sent", None, db)
    assert card.delivery_status == "sent"
    assert card.delivered_at is None


async def test_failure_records_the_error_code_and_no_timestamp():
    card = _card()
    db = _db_returning(card)
    await record_card_delivery("SM1", "failed", "63016", db)
    assert card.delivery_status == "failed"
    assert card.delivery_error_code == "63016"
    assert card.delivered_at is None


async def test_a_late_status_does_not_unset_an_earlier_delivery():
    """Statuses are not guaranteed to arrive in order."""
    from datetime import datetime, timezone

    stamp = datetime(2026, 10, 1, tzinfo=timezone.utc)
    card = _card(delivered_at=stamp, delivery_status="delivered")
    db = _db_returning(card)
    await record_card_delivery("SM1", "sent", None, db)
    assert card.delivered_at == stamp


async def test_a_status_for_something_that_is_not_a_card_is_not_an_error():
    """Most callbacks are for voice notes; no match is the normal case."""
    db = _db_returning(None)
    assert await record_card_delivery("SM9", "delivered", None, db) is False
