"""SH-09: per-client limits on the public endpoints that cost something, and
the daily spend alert."""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from core import alerts, ratelimit
from main import app
from models.db import get_db

client = TestClient(app)


# ── the limiter ──────────────────────────────────────────────────────────────


def test_allows_up_to_the_count_then_refuses_until_the_window_passes():
    lim = ratelimit.Limiter()
    rule = ratelimit.Limit("t", count=3, seconds=60)
    assert [lim.allow(rule, "a", now=t) for t in (0, 1, 2, 3)] == [
        True,
        True,
        True,
        False,
    ]
    assert lim.allow(rule, "b", now=3), "another client has its own count"
    assert lim.allow(rule, "a", now=60.5), "the first hit has left the window"


def _request(xff=None, real_ip=None, peer="172.18.0.5"):
    headers = {}
    if xff is not None:
        headers["x-forwarded-for"] = xff
    if real_ip is not None:
        headers["x-real-ip"] = real_ip
    return SimpleNamespace(
        headers=headers, client=SimpleNamespace(host=peer) if peer else None
    )


def test_client_ip_is_the_last_forwarded_address_and_never_x_real_ip():
    assert ratelimit.client_ip(_request(xff="203.0.113.7")) == "203.0.113.7"
    assert ratelimit.client_ip(_request(xff="1.1.1.1, 203.0.113.7")) == "203.0.113.7"
    assert (
        ratelimit.client_ip(_request(xff="203.0.113.7", real_ip="9.9.9.9"))
        == "203.0.113.7"
    )
    assert ratelimit.client_ip(_request(real_ip="9.9.9.9")) == "172.18.0.5"
    assert ratelimit.client_ip(_request(peer=None)) == "-"


# ── the routes ───────────────────────────────────────────────────────────────


@pytest.fixture
def no_db():
    mock_db = AsyncMock()
    mock_db.add = MagicMock()  # Session.add is synchronous

    async def _override():
        yield mock_db

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        yield mock_db
    finally:
        # Put back whatever was there: other modules set this override at
        # import time, and removing it breaks every test that runs after.
        if prev is not None:
            app.dependency_overrides[get_db] = prev
        else:
            app.dependency_overrides.pop(get_db, None)


@pytest.mark.parametrize("path", ["/onboarding/start", "/auth/magic-link"])
def test_sign_in_emails_are_limited_per_client(no_db, monkeypatch, path):
    from core import auth

    monkeypatch.setattr(auth, "find_account_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(auth, "send_magic_link", AsyncMock(return_value=True))
    me = {"X-Forwarded-For": "203.0.113.7"}
    codes = [
        client.post(path, data={"email": f"x{i}@example.com"}, headers=me).status_code
        for i in range(ratelimit.EMAIL_LINK.count + 1)
    ]
    assert codes[:-1] == [200] * ratelimit.EMAIL_LINK.count
    assert codes[-1] == 429
    other = client.post(
        path,
        data={"email": "y@example.com"},
        headers={"X-Forwarded-For": "198.51.100.2"},
    )
    assert other.status_code == 200, "another client is unaffected"


def test_the_two_sign_in_routes_share_one_count(no_db, monkeypatch):
    from core import auth

    monkeypatch.setattr(auth, "find_account_by_email", AsyncMock(return_value=None))
    monkeypatch.setattr(auth, "send_magic_link", AsyncMock(return_value=True))
    me = {"X-Forwarded-For": "203.0.113.8"}
    for i in range(ratelimit.EMAIL_LINK.count):
        path = "/onboarding/start" if i % 2 else "/auth/magic-link"
        assert (
            client.post(path, data={"email": "a@example.com"}, headers=me).status_code
            == 200
        )
    assert (
        client.post(
            "/auth/magic-link", data={"email": "a@example.com"}, headers=me
        ).status_code
        == 429
    )


@pytest.fixture
def card_page(no_db, monkeypatch):
    """A card link that resolves, with cards on and the inbox stubbed, so the
    test sees the limit and not the screening."""
    from api.routes import card_page as page
    from cards import family_inbox

    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    monkeypatch.setattr(
        page, "_card_for", AsyncMock(return_value=(MagicMock(), MagicMock(), None))
    )
    receive = AsyncMock(return_value="Thank you, it is on its way.")
    monkeypatch.setattr(family_inbox, "receive", receive)
    monkeypatch.setattr(family_inbox, "react", AsyncMock(return_value="Noted."))
    return receive


def test_card_questions_are_limited_per_link_before_any_screening(card_page):
    receive = card_page
    n = ratelimit.CARD_ASK_PER_LINK.count
    codes = [
        client.post("/c/tok-1/ask", data={"question": f"q{i}"}).status_code
        for i in range(n + 1)
    ]
    assert codes == [200] * n + [429]
    assert receive.await_count == n, "the refused question reached the LLM screen"
    assert client.post("/c/tok-2/ask", data={"question": "q"}).status_code == 200


def test_card_questions_are_limited_per_client_across_links(card_page):
    me = {"X-Forwarded-For": "203.0.113.9"}
    n = ratelimit.CARD_ASK_PER_CLIENT.count
    codes = [
        client.post(f"/c/tok-{i}/ask", data={"question": "q"}, headers=me).status_code
        for i in range(n + 1)
    ]
    assert codes == [200] * n + [429]


def test_card_reactions_are_limited_per_link(card_page):
    n = ratelimit.CARD_REACT_PER_LINK.count
    codes = [client.post("/c/tok-r/react").status_code for _ in range(n + 1)]
    assert codes == [200] * n + [429]


# ── the daily spend alert ────────────────────────────────────────────────────


def _usage(user_id, at):
    return SimpleNamespace(
        user_id=user_id,
        vendor="anthropic",
        operation="dialogue",
        model="m",
        units={},
        created_at=at,
    )


async def test_spend_alert_names_only_families_over_the_line(monkeypatch):
    now = datetime(2026, 11, 21, 12, 0, tzinfo=timezone.utc)  # 17:30 IST
    rows = [_usage("user_a", now)] * 4 + [_usage("user_b", now)]
    db = AsyncMock()
    db.execute = AsyncMock(return_value=MagicMock(all=lambda: rows))
    monkeypatch.setattr("config.settings.DAILY_SPEND_ALERT_INR", 150.0)
    monkeypatch.setattr(
        "core.pricing.cost_inr", lambda row: Decimal("50")
    )  # a: ₹200, b: ₹50
    send = AsyncMock(return_value=True)
    monkeypatch.setattr(alerts, "send", send)

    assert await alerts.check_spend(now=now, db=db) == ["user_a"]
    (subject, body), kwargs = send.await_args
    assert "user_a" in body and "₹200.00" in body and "not been paused" in body
    assert kwargs["key"] == "spend:user_a:2026-11-21"
    assert kwargs["every"] == alerts.SPEND_ALERT_EVERY


async def test_spend_alert_is_off_at_zero(monkeypatch):
    monkeypatch.setattr("config.settings.DAILY_SPEND_ALERT_INR", 0)
    send = AsyncMock()
    monkeypatch.setattr(alerts, "send", send)
    assert await alerts.check_spend(db=AsyncMock()) == []
    send.assert_not_awaited()


def test_the_spend_day_starts_at_midnight_in_india():
    # 20:00 UTC on the 21st is 01:30 IST on the 22nd.
    start = alerts._ist_day_start(datetime(2026, 11, 21, 20, 0, tzinfo=timezone.utc))
    assert start.isoformat() == "2026-11-22T00:00:00+05:30"
