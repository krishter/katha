"""
The usage ledger: one api_usage row per paid call
(docs/specs/COST_TO_SERVE.md §5).

The adapters record their own calls, so any future call site is covered
without remembering to. Who the call was for comes from a scope the entry
points set — the webhook, the scheduler, and the background tasks — which
the adapters never need to be told about:

    with usage_scope(user_id=..., session_id=...):
        ...                       # every paid call in here is attributed
        with usage_scope(turn_id=turn.id):
            ...                   # widened: user and session are kept

Two rules this module exists to keep:

- **Recording usage can never cost a turn.** `record` catches everything,
  logs at warning, and returns. It writes through its own short-lived DB
  session, so a failed ledger write cannot roll back or poison the
  request's transaction.
- **The ledger holds no personal data.** Units are counts. Anything that
  is not a number is dropped unless its key is on a short allow-list of
  enum-like labels, so a transcript or a phone number can never be
  recorded by accident.
"""

from __future__ import annotations

import logging
import uuid
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator, Mapping

logger = logging.getLogger(__name__)

# Keys whose values may be short strings. Everything else in `units` must be
# a number. These are labels from a fixed vocabulary, never free text.
_LABEL_KEYS = frozenset({"cache_ttl", "stop_reason", "meta_category"})
_MAX_LABEL_LENGTH = 32

_scope: ContextVar[Mapping[str, Any]] = ContextVar("usage_scope", default={})


@contextmanager
def usage_scope(
    user_id: str | None = None,
    session_id: Any = None,
    turn_id: Any = None,
) -> Iterator[None]:
    """
    Attribute every paid call inside the block. Nested scopes widen the
    outer one: a value given here replaces the outer value, and anything
    left as None is inherited.
    """
    token = enter_scope(user_id, session_id, turn_id)
    try:
        yield
    finally:
        exit_scope(token)


def enter_scope(
    user_id: str | None = None,
    session_id: Any = None,
    turn_id: Any = None,
):
    """
    `usage_scope` without the `with` block, for a long handler where wrapping
    the body would mean re-indenting it. Pair every call with `exit_scope`
    in a `finally`.
    """
    current = dict(_scope.get())
    for key, value in (
        ("user_id", user_id),
        ("session_id", session_id),
        ("turn_id", turn_id),
    ):
        if value is not None:
            current[key] = value
    return _scope.set(current)


def exit_scope(token) -> None:
    _scope.reset(token)


def current_scope() -> dict[str, Any]:
    return dict(_scope.get())


def _as_uuid(value: Any) -> uuid.UUID | None:
    if value is None:
        return None
    if isinstance(value, uuid.UUID):
        return value
    try:
        return uuid.UUID(str(value))
    except (ValueError, TypeError):
        return None


def clean_units(units: Mapping[str, Any]) -> dict[str, Any]:
    """
    Keep counts and allow-listed labels; drop everything else.

    This is the guard behind the no-personal-data rule. A caller that
    passes text by mistake loses that field and gets a warning, rather than
    writing someone's words into a table that is kept for cost reporting.
    """
    clean: dict[str, Any] = {}
    for key, value in units.items():
        if value is None:
            continue
        if isinstance(value, bool):
            clean[key] = value
        elif isinstance(value, (int, float)):
            clean[key] = value
        elif (
            key in _LABEL_KEYS
            and isinstance(value, str)
            and len(value) <= _MAX_LABEL_LENGTH
        ):
            clean[key] = value
        else:
            logger.warning(
                "usage ledger: dropped non-count field %r (%s) from units",
                key,
                type(value).__name__,
            )
    return clean


async def _write(row: dict[str, Any]) -> None:
    """Insert one ledger row through its own session. Patched in tests."""
    from models.api_usage import ApiUsage
    from models.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        db.add(ApiUsage(**row))
        await db.commit()


async def record(
    vendor: str,
    operation: str,
    units: Mapping[str, Any],
    *,
    model: str | None = None,
    message_sid: str | None = None,
    error_code: str | None = None,
) -> None:
    """Write one ledger row for the current scope. Never raises."""
    try:
        scope = _scope.get()
        row = {
            "user_id": scope.get("user_id"),
            "session_id": _as_uuid(scope.get("session_id")),
            "turn_id": _as_uuid(scope.get("turn_id")),
            "vendor": vendor,
            "operation": operation,
            "model": model,
            "units": clean_units(units),
            "message_sid": message_sid,
            "error_code": error_code,
        }
        await _write(row)
    except Exception:
        logger.warning(
            "usage ledger: could not record %s/%s — the call itself went ahead",
            vendor,
            operation,
            exc_info=True,
        )


async def _inbound_already_recorded(message_sid: str) -> bool:
    """Patched in tests alongside _write."""
    from sqlalchemy import select

    from models.api_usage import ApiUsage
    from models.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        found = await db.execute(
            select(ApiUsage.id)
            .where(ApiUsage.message_sid == message_sid)
            .where(ApiUsage.operation == "inbound")
            .limit(1)
        )
        return found.scalar_one_or_none() is not None


async def record_inbound(message_sid: str | None) -> None:
    """
    Record one inbound WhatsApp message, once.

    Twilio retries a webhook that did not get a fast 200, and only voice
    notes that became turns are caught by the turn-level idempotency check.
    A retried consent reply or text message would otherwise be billed twice
    in the ledger, so this checks by SID first. Never raises.
    """
    try:
        if message_sid and await _inbound_already_recorded(message_sid):
            return
    except Exception:
        logger.warning(
            "usage ledger: could not check for a prior inbound row for %s",
            message_sid,
            exc_info=True,
        )
        return
    await record("twilio", "inbound", {"messages": 1}, message_sid=message_sid)


# Twilio reports a message's progress as several callbacks, and they can
# arrive out of order. A later callback only replaces the recorded status
# if it is at least as far along, so "sent" arriving after "delivered"
# cannot make a delivered message look undelivered.
_STATUS_RANK = {
    "accepted": 0,
    "queued": 1,
    "sending": 2,
    "sent": 3,
    "delivered": 4,
    "read": 5,
    "undelivered": 6,
    "failed": 6,
}


async def _set_status(message_sid: str, status: str, error_code: str | None) -> int:
    """Patched in tests alongside _write. Returns rows updated."""
    from sqlalchemy import select

    from models.api_usage import ApiUsage
    from models.db import AsyncSessionLocal

    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(ApiUsage)
            .where(ApiUsage.message_sid == message_sid)
            .where(ApiUsage.operation != "inbound")
        )
        updated = 0
        for row in result.scalars().all():
            if _STATUS_RANK.get(status, 0) < _STATUS_RANK.get(row.status or "", -1):
                continue
            row.status = status
            row.error_code = error_code
            updated += 1
        await db.commit()
        return updated


async def record_status(
    message_sid: str, status: str, error_code: str | None = None
) -> None:
    """
    Stamp Twilio's delivery status on the outbound row for this SID, from
    the status callback. A message Meta rejected was still paid for at
    Twilio, which is what the admin page's wasted-outbound figure counts.
    Never raises.
    """
    try:
        await _set_status(message_sid, status, error_code)
    except Exception:
        logger.warning(
            "usage ledger: could not record status %s for %s",
            status,
            message_sid,
            exc_info=True,
        )


def template_category(template_sid: str | None) -> str | None:
    """
    Meta's category for an approved template, from META_TEMPLATE_CATEGORIES.
    None for a free-form send; "unknown" for a template nobody mapped, which
    the pricing then refuses to price rather than call free.
    """
    if not template_sid:
        return None
    import json

    from config import settings

    try:
        mapping = json.loads(settings.META_TEMPLATE_CATEGORIES or "{}")
    except ValueError:
        logger.warning("META_TEMPLATE_CATEGORIES is not valid JSON — ignoring it")
        mapping = {}
    category = mapping.get(template_sid) if isinstance(mapping, dict) else None
    return category if category in {"utility", "marketing"} else "unknown"


async def record_outbound(
    operation: str, message_sid: str | None, template_sid: str | None = None
) -> None:
    """One ledger row for one outbound WhatsApp message. Never raises."""
    units: dict[str, Any] = {"messages": 1}
    category = template_category(template_sid)
    if category is not None:
        units["meta_category"] = category
    await record("twilio", operation, units, message_sid=message_sid)
