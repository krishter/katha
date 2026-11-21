"""API key health on the metrics page (core/key_health.py)."""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from config import settings
from core import key_health, metrics_page
from core.key_health import Probe

NOW = datetime(2026, 11, 21, 6, 0, tzinfo=timezone.utc)  # 11:30 IST


@pytest.fixture
def keys(monkeypatch):
    """Every key set to an invented value, no renew-by dates."""
    for name, value in {
        "ANTHROPIC_API_KEY": "sk-ant-test-0000wxyz",
        "SARVAM_API_KEY": "sarvam-test-1234",
        "GEMINI_API_KEY": "gem-test-abcd",
        "TWILIO_ACCOUNT_SID": "AC00000000",
        "TWILIO_AUTH_TOKEN": "tok-test-9876",
        "AWS_ACCESS_KEY_ID": "AKIATEST5555",
        "AWS_SECRET_ACCESS_KEY": "secret-test-7777",
        "KEY_RENEW_BY": "",
    }.items():
        monkeypatch.setattr(settings, name, value)


def _db(successes: dict[str, datetime] | None = None):
    """A session whose ledger query returns the given last success per
    ledger vendor, in KEYS order."""
    successes = successes or {}
    results = []
    for k in key_health.KEYS:
        r = MagicMock()
        r.scalar_one_or_none.return_value = successes.get(k.name)
        results.append(r)
    db = MagicMock()
    db.execute = AsyncMock(side_effect=results)
    return db


def _probes(monkeypatch, probes: dict[str, Probe]):
    async def fake(_s, names):
        return {n: p for n, p in probes.items() if n in names}

    monkeypatch.setattr(key_health, "_probe_all", fake)


def _by_name(rows):
    return {r.name: r for r in rows}


async def test_all_working_shows_last_four_only(keys, monkeypatch):
    _probes(
        monkeypatch,
        {n: Probe(True, "200") for n in ("anthropic", "gemini", "twilio", "aws")},
    )
    rows = _by_name(await key_health.report(_db({"anthropic": NOW}), now=NOW))
    assert all(r.state == "ok" for r in rows.values()), {
        n: r.reasons for n, r in rows.items()
    }
    assert rows["anthropic"].fingerprint == "…wxyz"
    assert rows["anthropic"].last_success == NOW
    assert rows["sarvam"].probe.ok is None  # no free check
    blob = repr([r.as_dict() for r in rows.values()])
    for secret in ("sk-ant-test-0000", "secret-test", "tok-test", "sarvam-test"):
        assert secret not in blob


async def test_a_missing_key_is_red(keys, monkeypatch):
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "")
    rows = _by_name(await key_health.report(_db(), now=NOW))
    assert rows["gemini"].state == "bad"
    assert rows["gemini"].reasons == ["not set in .env"]


async def test_a_refused_live_check_is_red_and_a_timeout_amber(keys, monkeypatch):
    _probes(
        monkeypatch,
        {"anthropic": Probe(False, "401"), "gemini": Probe(False, "ConnectTimeout")},
    )
    rows = _by_name(await key_health.report(_db(), now=NOW))
    assert rows["anthropic"].state == "bad"
    assert rows["gemini"].state == "warn"


async def test_a_refused_call_with_nothing_working_since_is_red(keys):
    key_health.failed("sarvam", 403)
    rows = _by_name(
        await key_health.report(_db({"sarvam": NOW - timedelta(hours=1)}), probe=False)
    )
    assert rows["sarvam"].state == "bad"
    assert "403" in rows["sarvam"].reasons[0]


async def test_a_failure_followed_by_a_success_is_fine(keys):
    key_health.failed("sarvam", 403)
    later = datetime.now(timezone.utc) + timedelta(seconds=5)
    rows = _by_name(await key_health.report(_db({"sarvam": later}), probe=False))
    assert rows["sarvam"].state == "ok"


async def test_a_429_is_amber_and_network_errors_are_not_noted(keys):
    key_health.failed("gemini", 429)
    key_health.failed("anthropic", "APIConnectionError")
    rows = _by_name(await key_health.report(_db(), probe=False))
    assert rows["gemini"].state == "warn"
    assert rows["anthropic"].last_failure is None


@pytest.mark.parametrize(
    "renew_by, state",
    [("2027-02-01", "ok"), ("2026-12-01", "warn"), ("2026-11-20", "bad")],
)
async def test_renew_by_dates(keys, monkeypatch, renew_by, state):
    monkeypatch.setattr(settings, "KEY_RENEW_BY", f"twilio={renew_by}")
    rows = _by_name(await key_health.report(_db(), now=NOW, probe=False))
    assert rows["twilio"].renew_by == date.fromisoformat(renew_by)
    assert rows["twilio"].state == state


def test_parse_renew_by_reports_typos_without_failing():
    dates, unreadable = key_health.parse_renew_by(
        " anthropic=2027-01-15 , sarvam=soon, openai=2027-01-01,"
    )
    assert dates == {"anthropic": date(2027, 1, 15)}
    assert unreadable == {"sarvam", "openai=2027-01-01"}


def test_status_of_reads_sdk_exceptions():
    class Twilioish(Exception):
        status = 401

    class Botoish(Exception):
        response = {"ResponseMetadata": {"HTTPStatusCode": 403}}

    assert key_health.status_of(Twilioish()) == "401"
    assert key_health.status_of(Botoish()) == "403"
    assert key_health.status_of(ValueError()) == "ValueError"


async def test_twilio_check_reads_the_balance(keys):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/Accounts/AC00000000/Balance.json")
        return httpx.Response(200, json={"balance": "12.5", "currency": "USD"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        probe = await key_health._probe_twilio(client, settings)
    assert probe.ok and probe.detail == "balance 12.50 USD"


async def test_anthropic_check_is_refused_on_401(keys):
    transport = httpx.MockTransport(lambda r: httpx.Response(401, json={}))
    async with httpx.AsyncClient(transport=transport) as client:
        probe = await key_health._probe_anthropic(client, settings)
    assert probe.ok is False and probe.refused


async def test_alerts_red_and_renew_soon_but_not_transient(keys, monkeypatch):
    monkeypatch.setattr(settings, "KEY_RENEW_BY", "twilio=2026-11-30")
    _probes(
        monkeypatch,
        {"anthropic": Probe(False, "401"), "gemini": Probe(False, "ReadTimeout")},
    )
    sent = []

    async def fake_send(subject, body, *, key=None, every=None):
        sent.append((subject, body, key))
        return True

    from core import alerts

    monkeypatch.setattr(alerts, "send", fake_send)
    assert await key_health.check_keys(_db(), now=NOW) == ["anthropic", "twilio"]
    assert sent[0][2] == "key:anthropic:bad"
    assert "…wxyz" in sent[0][1] and "sk-ant" not in sent[0][1]
    assert sent[1][2] == "key:twilio:warn"


def test_page_shows_the_keys_section():
    row = {
        "name": "twilio",
        "label": "Twilio (WhatsApp)",
        "configured": True,
        "fingerprint": "…9876",
        "last_success": NOW.isoformat(),
        "last_failure": None,
        "check": {"ok": True, "status": "200", "detail": "balance 12.50 USD"},
        "renew_by": "2026-11-30",
        "state": "warn",
        "reasons": ["renew within 9 day(s)"],
    }
    html = metrics_page._api_keys([row])
    assert "Check soon" in html and "…9876" in html and "balance 12.50 USD" in html
    assert "2026-11-30" in html
