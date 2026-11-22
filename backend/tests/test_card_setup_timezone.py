"""Old timezone names browsers still send are accepted under the current name."""

import importlib.util
from zoneinfo import ZoneInfo

import pytest

from cards import setup


@pytest.mark.parametrize("old, current", sorted(setup._LEGACY_NAMES.items()))
def test_old_names_map_to_the_current_name(old, current):
    assert setup.parse_timezone(old) == current
    ZoneInfo(current)


def test_current_names_pass_through():
    assert setup.parse_timezone("Asia/Kolkata") == "Asia/Kolkata"
    assert setup.parse_timezone("America/New_York") == "America/New_York"


def test_an_unknown_name_is_refused():
    with pytest.raises(ValueError, match="timezone"):
        setup.parse_timezone("Mars/Olympus")


def test_the_tzdata_package_is_installed():
    # The slim image's system zone files lack the old names; zoneinfo falls
    # back to this package for any name the system files do not have.
    assert importlib.util.find_spec("tzdata") is not None
