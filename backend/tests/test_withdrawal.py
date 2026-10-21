"""
The fixed-phrase and prefilter rules for withdrawal (core/withdrawal.py,
P4). The classifier's judgement is tested on recorded replies in
tests/integration/test_withdrawal.py and by `run_eval.py withdraw`.
"""

from unittest.mock import AsyncMock, patch

from core import withdrawal


def test_a_short_stop_is_a_stop():
    for said in [
        "Bas, band karo, mujhe nahi chahiye",
        "Please stop recording me.",
        "Don't call me again",
        "இனிமே வேண்டாம்",
        "बंद करो",
    ]:
        assert withdrawal.said_stop(said), said


def test_a_stop_phrase_opening_a_long_message_counts():
    said = (
        "Band karo, I have been thinking all week and I really do not want "
        "my voice kept anywhere any more, so please do this for me"
    )
    assert withdrawal.said_stop(said)


def test_a_stop_phrase_deep_inside_a_story_is_not_a_fixed_stop():
    said = (
        "When the rain came my mother would shout khidki band karo and we "
        "all ran to close them before the floor got wet"
    )
    assert not withdrawal.said_stop(said)
    # It still reaches the classifier, which decides.
    assert withdrawal.might_be_stop(said)


def test_most_of_what_she_says_never_reaches_the_classifier():
    for said in [
        "We lived near the river at Kumbakonam.",
        "My mother cooked for twenty people every Sunday.",
        "Aaj ke liye bas, thak gayi hoon.",
    ]:
        assert not withdrawal.might_be_stop(said), said


async def test_a_crisis_is_answered_by_the_crisis_protocol_not_withdrawn():
    chat = AsyncMock(side_effect=AssertionError("no model call for a crisis"))
    with patch("adapters.llm.chat", new=chat):
        assert not await withdrawal.is_withdrawal("I want to die, stop recording")


async def test_a_fixed_stop_needs_no_model():
    chat = AsyncMock(side_effect=AssertionError("a fixed stop needs no model"))
    with patch("adapters.llm.chat", new=chat):
        assert await withdrawal.is_withdrawal("Bas, band karo, mujhe nahi chahiye")


async def test_the_classifier_being_down_withdraws_no_one_by_itself():
    failing = AsyncMock(side_effect=RuntimeError("down"))
    with patch("adapters.llm.chat", new=failing):
        assert not await withdrawal.is_withdrawal(
            "My father told us to stop playing and come home"
        )
