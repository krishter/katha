"""scripts/cards.py argument rules (CONVERSATION_CARDS.md §15 Step 3)."""

from __future__ import annotations

import argparse

import pytest

from scripts.cards import parse, parse_slot


def test_slots_parse_to_day_and_time():
    assert parse_slot("sun 11:00") == {"dow": 6, "time": "11:00"}
    assert parse_slot("Wednesday 9:05") == {"dow": 2, "time": "09:05"}


@pytest.mark.parametrize("bad", ["sunday", "xyz 10:00", "sun 25:00", "sun 10"])
def test_bad_slots_are_refused(bad):
    with pytest.raises(argparse.ArgumentTypeError):
        parse_slot(bad)


def test_enabling_needs_the_names_cards_are_written_with():
    with pytest.raises(SystemExit):
        parse(["--user-id", "u1", "--enable"])
    args = parse(
        [
            "--user-id",
            "u1",
            "--enable",
            "--child-name",
            "Priya",
            "--parent-title",
            "Amma",
        ]
    )
    assert args.enable and not args.apply


def test_a_bad_timezone_is_refused():
    with pytest.raises(SystemExit):
        parse(["--user-id", "u1", "--timezone", "Mars/Olympus"])
