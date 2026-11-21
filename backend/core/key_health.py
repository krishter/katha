"""
Is each API key still working, and when does it need renewing?

None of Katha's keys (Anthropic, Sarvam, Gemini, Twilio, AWS) expire on
their own, so there is no expiry date to read from the providers. They
stop working when a balance or a free tier runs out, or when a key is
revoked. In August the OpenAI account ran out of credits and every turn
failed unseen. This module puts three things on the metrics page for each
key, and emails the operator when one goes wrong:

  - **Last success**: the newest paid call in the usage ledger. It is kept
    in the database, so a restart does not lose it.
  - **Last failure**: the newest refusal with an HTTP status, noted by the
    adapters through `failed()`. Kept in memory: one worker process, reset
    on restart.
  - **A live check**: a free, authenticated request to the provider:
    listing models (Anthropic, Gemini), reading the balance (Twilio), or
    asking who the key is (AWS STS). Sarvam has no free endpoint to ask, so
    it shows only the ledger.

Plus the **renew-by date** the operator writes in KEY_RENEW_BY, for a
balance, a free tier or a rotation that runs out on a known day.

Nothing here shows a key: only its last four characters. Nothing here is
about a family.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

RENEW_WARNING = timedelta(days=14)
LEDGER_LOOKBACK = timedelta(days=60)
PROBE_TIMEOUT_SECONDS = 8.0
# A provider says the key itself, or the account behind it, is refused.
REFUSED_STATUSES = frozenset({401, 402, 403})

OK, WARN, BAD = "ok", "warn", "bad"


@dataclass(frozen=True)
class Key:
    name: str
    label: str
    # Settings that must all be set; the last one is fingerprinted.
    settings_names: tuple[str, ...]
    # How its successful calls appear in api_usage.
    ledger_vendor: str
    ledger_operation_prefix: str = ""


KEYS: tuple[Key, ...] = (
    Key("anthropic", "Anthropic (conversation)", ("ANTHROPIC_API_KEY",), "anthropic"),
    Key("sarvam", "Sarvam (listening, backup voice)", ("SARVAM_API_KEY",), "sarvam"),
    Key("gemini", "Google Gemini (voice)", ("GEMINI_API_KEY",), "google"),
    # Inbound rows are Twilio calling us, not a use of the token.
    Key(
        "twilio",
        "Twilio (WhatsApp)",
        ("TWILIO_ACCOUNT_SID", "TWILIO_AUTH_TOKEN"),
        "twilio",
        "outbound",
    ),
    # S3 calls are not in the ledger; SES emails are.
    Key(
        "aws",
        "AWS (storage, email)",
        ("AWS_SECRET_ACCESS_KEY", "AWS_ACCESS_KEY_ID"),
        "ses",
    ),
)
NAMES = tuple(k.name for k in KEYS)


@dataclass
class Failure:
    at: datetime
    status: str

    @property
    def refused(self) -> bool:
        return self.status.isdigit() and int(self.status) in REFUSED_STATUSES


@dataclass
class Probe:
    ok: Optional[bool]  # None: not checked
    status: str = ""
    detail: str = ""

    @property
    def refused(self) -> bool:
        return self.status.isdigit() and int(self.status) in REFUSED_STATUSES


@dataclass
class KeyReport:
    name: str
    label: str
    configured: bool
    fingerprint: str
    last_success: Optional[datetime]
    last_failure: Optional[Failure]
    probe: Probe
    renew_by: Optional[date]
    renew_by_unreadable: bool
    state: str = OK
    reasons: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "name": self.name,
            "label": self.label,
            "configured": self.configured,
            "fingerprint": self.fingerprint,
            "last_success": self.last_success.isoformat()
            if self.last_success
            else None,
            "last_failure": (
                {
                    "at": self.last_failure.at.isoformat(),
                    "status": self.last_failure.status,
                }
                if self.last_failure
                else None
            ),
            "check": {
                "ok": self.probe.ok,
                "status": self.probe.status,
                "detail": self.probe.detail,
            },
            "renew_by": self.renew_by.isoformat() if self.renew_by else None,
            "state": self.state,
            "reasons": list(self.reasons),
        }


# ── Failures noted by the adapters ─────────────────────────────────────────

_failures: dict[str, Failure] = {}


def failed(name: str, status) -> None:
    """
    Note that a provider refused a call with this HTTP status. Called by the
    adapters on a non-2xx response; never raises, never blocks. Only the
    status is kept, never the response body (it can echo her words).
    """
    status = str(status)
    if not status.isdigit():
        return  # a network problem, not the provider answering
    _failures[name] = Failure(datetime.now(timezone.utc), status)


def status_of(exc: BaseException) -> str:
    """The HTTP status an SDK exception carries, or its class name."""
    for attr in ("status_code", "status"):
        value = getattr(exc, attr, None)
        if isinstance(value, int):
            return str(value)
    response = getattr(exc, "response", None)
    if isinstance(response, dict):  # botocore ClientError
        code = response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        if isinstance(code, int):
            return str(code)
    return type(exc).__name__


def reset_failures() -> None:
    """For tests."""
    _failures.clear()


# ── Renew-by dates ─────────────────────────────────────────────────────────


def parse_renew_by(raw: str) -> tuple[dict[str, date], set[str]]:
    """
    "anthropic=2027-01-15, twilio=2026-12-31" -> ({name: date}, unreadable).
    An entry with an unknown name or a bad date is reported, not fatal: a
    typo here must not stop the backend booting.
    """
    dates: dict[str, date] = {}
    unreadable: set[str] = set()
    for part in (raw or "").split(","):
        part = part.strip()
        if not part:
            continue
        name, _, value = part.partition("=")
        name = name.strip().lower()
        try:
            when = date.fromisoformat(value.strip())
        except ValueError:
            when = None
        if name not in NAMES or when is None:
            unreadable.add(name if name in NAMES else part)
            logger.warning("KEY_RENEW_BY: could not read %r", part)
            continue
        dates[name] = when
    return dates, unreadable


# ── Live checks ────────────────────────────────────────────────────────────


async def _probe_anthropic(client: httpx.AsyncClient, s) -> Probe:
    r = await client.get(
        "https://api.anthropic.com/v1/models",
        params={"limit": 1},
        headers={"x-api-key": s.ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01"},
    )
    return Probe(r.status_code == 200, str(r.status_code))


async def _probe_gemini(client: httpx.AsyncClient, s) -> Probe:
    r = await client.get(
        "https://generativelanguage.googleapis.com/v1beta/models",
        params={"pageSize": 1},
        headers={"x-goog-api-key": s.GEMINI_API_KEY},
    )
    return Probe(r.status_code == 200, str(r.status_code))


async def _probe_twilio(client: httpx.AsyncClient, s) -> Probe:
    r = await client.get(
        f"https://api.twilio.com/2010-04-01/Accounts/{s.TWILIO_ACCOUNT_SID}/Balance.json",
        auth=(s.TWILIO_ACCOUNT_SID, s.TWILIO_AUTH_TOKEN),
    )
    if r.status_code != 200:
        return Probe(False, str(r.status_code))
    detail = ""
    try:
        body = r.json()
        detail = (
            f"balance {float(body['balance']):.2f} {body.get('currency', '')}".strip()
        )
    except Exception:
        pass
    return Probe(True, "200", detail)


async def _probe_aws(client: httpx.AsyncClient, s) -> Probe:
    import boto3

    def _ask() -> None:
        boto3.client(
            "sts",
            region_name=s.AWS_S3_REGION,
            aws_access_key_id=s.AWS_ACCESS_KEY_ID,
            aws_secret_access_key=s.AWS_SECRET_ACCESS_KEY,
        ).get_caller_identity()

    try:
        await asyncio.wait_for(asyncio.to_thread(_ask), PROBE_TIMEOUT_SECONDS)
    except Exception as exc:
        return Probe(False, status_of(exc))
    return Probe(True, "200")


_PROBES = {
    "anthropic": _probe_anthropic,
    "gemini": _probe_gemini,
    "twilio": _probe_twilio,
    "aws": _probe_aws,
}


async def _probe_all(s, names: list[str]) -> dict[str, Probe]:
    """Run the free checks for the configured keys, at once. Patched in tests."""

    async def one(client, name) -> Probe:
        try:
            return await _PROBES[name](client, s)
        except Exception as exc:
            # A network problem says nothing about the key.
            return Probe(False, type(exc).__name__)

    wanted = [n for n in names if n in _PROBES]
    async with httpx.AsyncClient(timeout=PROBE_TIMEOUT_SECONDS) as client:
        results = await asyncio.gather(*(one(client, n) for n in wanted))
    return dict(zip(wanted, results))


# ── The report ─────────────────────────────────────────────────────────────


async def _last_successes(db, now: datetime) -> dict[str, datetime]:
    from sqlalchemy import func, select

    from models.api_usage import ApiUsage

    out: dict[str, datetime] = {}
    for key in KEYS:
        q = (
            select(func.max(ApiUsage.created_at))
            .where(ApiUsage.vendor == key.ledger_vendor)
            .where(ApiUsage.created_at >= now - LEDGER_LOOKBACK)
        )
        if key.ledger_operation_prefix:
            q = q.where(ApiUsage.operation.like(f"{key.ledger_operation_prefix}%"))
        last = (await db.execute(q)).scalar_one_or_none()
        if isinstance(last, datetime):
            out[key.name] = last
    return out


def _fingerprint(value: str) -> str:
    return f"…{value[-4:]}" if len(value) >= 8 else ("set" if value else "")


def _judge(r: KeyReport, today: date) -> None:
    if not r.configured:
        r.state, r.reasons = BAD, ["not set in .env"]
        return
    bad: list[str] = []
    warn: list[str] = []
    if r.probe.ok is False:
        if r.probe.refused:
            bad.append(f"the provider refused the key just now (HTTP {r.probe.status})")
        else:
            warn.append(f"the live check did not complete ({r.probe.status})")
    f = r.last_failure
    if f and (r.last_success is None or f.at > r.last_success):
        if f.refused:
            bad.append(
                f"its last call was refused (HTTP {f.status}) and none has worked since"
            )
        else:
            warn.append(f"its last call failed ({f.status}) and none has worked since")
    if r.renew_by_unreadable:
        warn.append("its KEY_RENEW_BY entry could not be read")
    if r.renew_by:
        days = (r.renew_by - today).days
        if days < 0:
            bad.append(f"its renew-by date passed {-days} day(s) ago")
        elif days <= RENEW_WARNING.days:
            warn.append(f"renew within {days} day(s)")
    r.reasons = bad + warn
    r.state = BAD if bad else WARN if warn else OK


async def report(
    db, *, now: Optional[datetime] = None, probe: bool = True
) -> list[KeyReport]:
    """One row per key, judged. Reads the ledger; with `probe`, asks each
    provider. Never raises for a provider's sake."""
    from config import settings

    now = now or datetime.now(timezone.utc)
    today = now.astimezone(timezone(timedelta(hours=5, minutes=30))).date()
    dates, unreadable = parse_renew_by(settings.KEY_RENEW_BY)
    try:
        successes = await _last_successes(db, now)
    except Exception:
        logger.exception("key health: could not read the usage ledger")
        successes = {}
    configured = {
        k.name: all(getattr(settings, n, "") for n in k.settings_names) for k in KEYS
    }
    probes: dict[str, Probe] = {}
    if probe:
        try:
            probes = await _probe_all(
                settings, [n for n, ok in configured.items() if ok]
            )
        except Exception:
            logger.exception("key health: the live checks failed")
    rows = []
    for k in KEYS:
        r = KeyReport(
            name=k.name,
            label=k.label,
            configured=configured[k.name],
            fingerprint=_fingerprint(getattr(settings, k.settings_names[-1], "") or ""),
            last_success=successes.get(k.name),
            last_failure=_failures.get(k.name),
            probe=probes.get(k.name, Probe(None)),
            renew_by=dates.get(k.name),
            renew_by_unreadable=k.name in unreadable,
        )
        _judge(r, today)
        rows.append(r)
    return rows


# ── Alerts ─────────────────────────────────────────────────────────────────

KEY_ALERT_EVERY = timedelta(hours=24)


async def check_keys(db, *, now: Optional[datetime] = None) -> list[str]:
    """
    Email the operator, once a day per key, about a key that is refused,
    failing with nothing working since, or near or past its renew-by date.
    A transient failure (a 500, a timeout) is left to the turn-failure
    alert. Returns the names alerted.
    """
    from core import alerts

    rows = await report(db, now=now)
    alerted = []
    for r in rows:
        if r.state == OK:
            continue
        transient_only = (
            r.state == WARN and not r.renew_by and not r.renew_by_unreadable
        )
        if transient_only:
            continue
        subject = (
            f"API key needs attention now: {r.label}"
            if r.state == BAD
            else f"API key to renew soon: {r.label}"
        )
        body = (
            f"{r.label} (key ending {r.fingerprint or '—'}): "
            + "; ".join(r.reasons)
            + ".\n\nThe API keys section at the top of /admin/metrics/view "
            "(through the tunnel) shows every key. Renew-by dates are set in "
            "KEY_RENEW_BY in backend/.env."
        )
        if await alerts.send(
            subject, body, key=f"key:{r.name}:{r.state}", every=KEY_ALERT_EVERY
        ):
            alerted.append(r.name)
    return alerted
