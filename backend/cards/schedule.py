"""
When an approved card goes to the child (docs/specs/CONVERSATION_CARDS.md §8.2).

- 3 hours before the child's next usual call that is at least 6 hours
  after her approval, in the child's own timezone.
- No call slot within 4 days, or none on file: 18:00 child time on the day
  after approval.
- One card per slot; a second approved card takes the next one.
- Never more than 2 sends in any rolling 7 days (D4): a time that would
  break that moves later.

Pure functions over datetimes, so the rules can be tested without a
database; schedule_card applies them to a card.
"""

from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone
from typing import Iterable, Optional
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import state
from cards.context import family_of
from models.conversation_card import ConversationCard
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

LEAD = timedelta(hours=3)
MIN_AFTER_APPROVAL = timedelta(hours=6)
SLOT_HORIZON = timedelta(days=4)
FALLBACK_TIME = time(18, 0)
CADENCE_WINDOW = timedelta(days=7)
CADENCE_LIMIT = 2
_SEARCH_DAYS = 60
DEFAULT_TZ = "Asia/Kolkata"


def _zone(name: Optional[str]) -> ZoneInfo:
    try:
        return ZoneInfo(name or DEFAULT_TZ)
    except Exception:
        return ZoneInfo(DEFAULT_TZ)


def _parse_slots(call_slots) -> list[tuple[int, time]]:
    slots = []
    for slot in call_slots or []:
        try:
            dow = int(slot["dow"])
            hour, minute = (int(x) for x in str(slot["time"]).split(":")[:2])
            if 0 <= dow <= 6:
                slots.append((dow, time(hour, minute)))
        except (KeyError, TypeError, ValueError):
            continue
    return slots


def _slot_sends(
    approved_at: datetime, slots: list[tuple[int, time]], zone: ZoneInfo, days: int
) -> list[datetime]:
    """Send times (slot minus the lead) for every slot occurrence at least
    6 hours after approval, in order, over `days` days."""
    local = approved_at.astimezone(zone)
    sends = []
    for offset in range(days + 1):
        day = (local + timedelta(days=offset)).date()
        for dow, at in slots:
            if day.weekday() != dow:
                continue
            call = datetime.combine(day, at, tzinfo=zone)
            if call >= approved_at + MIN_AFTER_APPROVAL:
                sends.append((call - LEAD).astimezone(timezone.utc))
    return sorted(set(sends))


def _fallback_sends(approved_at: datetime, zone: ZoneInfo, days: int) -> list[datetime]:
    local = approved_at.astimezone(zone)
    return [
        datetime.combine(
            (local + timedelta(days=offset)).date(), FALLBACK_TIME, tzinfo=zone
        ).astimezone(timezone.utc)
        for offset in range(1, days + 1)
    ]


def _cadence_ok(when: datetime, taken: list[datetime]) -> bool:
    """Adding a send at `when` keeps every 7-day window at 2 or fewer."""
    times = sorted([*taken, when])
    for start in times:
        in_window = [t for t in times if start <= t < start + CADENCE_WINDOW]
        if len(in_window) > CADENCE_LIMIT:
            return False
    return True


def send_time(
    approved_at: datetime,
    call_slots,
    timezone_name: Optional[str],
    taken: Iterable[datetime] = (),
) -> datetime:
    """The UTC moment to send a card approved at `approved_at`, given the
    send times other cards already hold."""
    zone = _zone(timezone_name)
    taken = sorted(taken)
    slots = _parse_slots(call_slots)
    horizon = approved_at + SLOT_HORIZON

    candidates: list[datetime] = []
    if slots:
        candidates = _slot_sends(approved_at, slots, zone, _SEARCH_DAYS)
    if not candidates or candidates[0] > horizon:
        # No usual call soon enough: the evening after she approved.
        candidates = _fallback_sends(approved_at, zone, _SEARCH_DAYS)

    for when in candidates:
        if when in taken:  # one card per slot
            continue
        if _cadence_ok(when, taken):
            return when
    # Sixty days of slots all taken is not a real family; send the evening
    # after the last one rather than never.
    return _fallback_sends(max(taken or [approved_at]), zone, 1)[0]


async def taken_send_times(
    user_id: str, db: AsyncSession, exclude=None
) -> list[datetime]:
    """Send times held by this family's scheduled and sent cards."""
    rows = await db.execute(
        select(ConversationCard.id, ConversationCard.scheduled_for)
        .where(ConversationCard.user_id == user_id)
        .where(ConversationCard.status.in_(("scheduled", "sent")))
        .where(ConversationCard.scheduled_for.is_not(None))
    )
    return [when for card_id, when in rows.all() if card_id != exclude]


async def schedule_card(card: ConversationCard, db: AsyncSession) -> datetime:
    """approved -> scheduled, with its send time. Does not commit.

    state.transition refuses unless she approved this exact text."""
    family = await family_of(card.user_id, db)
    zone_name = family.timezone if family and family.timezone else None
    if zone_name is None:
        zone_name = (
            await db.execute(
                select(UserProfileModel.timezone).where(
                    UserProfileModel.user_id == card.user_id
                )
            )
        ).scalar_one_or_none()
    approved_at = card.parent_answered_at or datetime.now(timezone.utc)
    when = send_time(
        approved_at,
        family.call_slots if family else [],
        zone_name,
        await taken_send_times(card.user_id, db, exclude=card.id),
    )
    state.transition(card, "scheduled")
    card.scheduled_for = when
    logger.info("Card %s scheduled for %s", card.id, when.isoformat())
    return when
