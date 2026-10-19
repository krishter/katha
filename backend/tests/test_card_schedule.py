"""
When an approved card is sent (CONVERSATION_CARDS.md §8.2, §12 "scheduling
across timezones, slot rules, the no-slot fallback").
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from cards.schedule import send_time

IST = ZoneInfo("Asia/Kolkata")
NY = ZoneInfo("America/New_York")
# Monday 2026-10-19
MON = 0
SUN = 6
WED = 2


def at(zone, y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=zone).astimezone(timezone.utc)


def test_three_hours_before_the_next_call():
    # Approved Monday 09:00 IST; calls Wednesday 19:00 IST.
    approved = at(IST, 2026, 10, 19, 9)
    when = send_time(approved, [{"dow": WED, "time": "19:00"}], "Asia/Kolkata")
    assert when == at(IST, 2026, 10, 21, 16)


def test_a_call_less_than_six_hours_away_is_skipped_for_the_next_one():
    # Approved Wednesday 15:00 IST; the 19:00 call is only 4 hours away.
    approved = at(IST, 2026, 10, 21, 15)
    slots = [{"dow": WED, "time": "19:00"}, {"dow": SUN, "time": "11:00"}]
    when = send_time(approved, slots, "Asia/Kolkata")
    assert when == at(IST, 2026, 10, 25, 8)  # Sunday 11:00 - 3h


def test_the_childs_timezone_not_the_parents():
    # An NRI child in New York calls Sundays at 09:00 their time.
    approved = at(IST, 2026, 10, 22, 10)  # Thursday morning in India
    when = send_time(approved, [{"dow": SUN, "time": "09:00"}], "America/New_York")
    assert when == at(NY, 2026, 10, 25, 6)
    assert when.astimezone(NY).hour == 6


def test_no_slot_within_four_days_sends_the_next_evening():
    approved = at(IST, 2026, 10, 19, 9)  # Monday
    when = send_time(approved, [{"dow": SUN, "time": "11:00"}], "Asia/Kolkata")
    # Sunday is six days off: 18:00 child time on Tuesday instead.
    assert when == at(IST, 2026, 10, 20, 18)


def test_no_slots_on_file_sends_the_next_evening():
    approved = at(NY, 2026, 10, 19, 22)
    assert send_time(approved, [], "America/New_York") == at(NY, 2026, 10, 20, 18)


def test_one_card_per_slot():
    approved = at(IST, 2026, 10, 19, 9)
    slots = [{"dow": WED, "time": "19:00"}, {"dow": 3, "time": "19:00"}]
    first = send_time(approved, slots, "Asia/Kolkata")
    second = send_time(approved, slots, "Asia/Kolkata", taken=[first])
    assert first == at(IST, 2026, 10, 21, 16)
    assert second == at(IST, 2026, 10, 22, 16)


def test_never_more_than_two_in_seven_days():
    approved = at(IST, 2026, 10, 19, 9)
    daily = [{"dow": d, "time": "19:00"} for d in range(7)]
    taken = [at(IST, 2026, 10, 19, 16) - timedelta(days=1), at(IST, 2026, 10, 19, 16)]
    when = send_time(approved, daily, "Asia/Kolkata", taken=taken)
    window = sorted([*taken, when])
    for start in window:
        assert sum(start <= t < start + timedelta(days=7) for t in window) <= 2
    # The earliest that keeps the rule: a week after the first of the two.
    assert when >= taken[0] + timedelta(days=7)


def test_bad_slots_and_timezones_fall_back_safely():
    approved = at(IST, 2026, 10, 19, 9)
    junk = [{"dow": 9, "time": "19:00"}, {"time": "19:00"}, {"dow": "x"}, "nope"]
    assert send_time(approved, junk, "Mars/Olympus") == at(IST, 2026, 10, 20, 18)
