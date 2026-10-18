"""
What conversation cards add to the prompts (CONVERSATION_CARDS.md §9.1),
and that with cards off for her, nothing is added at all.
"""

from __future__ import annotations

import dataclasses

from core.session_manager import SessionState
from prompts.system_prompt import (
    CardsContext,
    PriorContext,
    UserProfile,
    build_extraction_prompt,
    build_system_prompt,
)

CARD_WORDS = (
    "share_restriction",
    "topic_label",
    "share_preference_change",
    "between us",
)

_PROFILE = UserProfile(
    name="Kamala", preferred_language="ta-IN", onboarding_context="Grew up in Madurai"
)
_CARDS = CardsContext(child_name="Priya", parent_title="Amma")


def _state(**overrides) -> SessionState:
    base = dict(
        session_id="s1",
        user_id="u1",
        session_number=2,
        domain="childhood",
        exchange_count=3,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )
    return SessionState(**{**base, **overrides})


def _prompts(profile: UserProfile) -> str:
    return "\n".join(
        [
            build_extraction_prompt(
                profile, _state(), PriorContext(), "I walked to the talkies", "Oh?"
            ),
            build_system_prompt(profile, _state(), PriorContext()),
            build_system_prompt(profile, _state(goal_met=True), PriorContext()),
        ]
    )


def test_with_cards_off_no_prompt_mentions_them():
    text = _prompts(_PROFILE)
    for word in CARD_WORDS:
        assert word not in text


def test_with_cards_on_extraction_asks_for_the_share_fields():
    profile = dataclasses.replace(_PROFILE, cards=_CARDS)
    extraction = build_extraction_prompt(
        profile, _state(), PriorContext(), "Don't tell my son this", "Of course."
    )
    for field in ("share_restriction", "topic_label", "share_preference_change"):
        assert f'"{field}"' in extraction
    # Still one JSON object, and still closed off the same way.
    assert extraction.rstrip().endswith("no other text.")


def test_cards_on_changes_only_what_it_adds():
    """Removing the added block gives back today's extraction prompt."""
    on = build_extraction_prompt(
        dataclasses.replace(_PROFILE, cards=_CARDS),
        _state(),
        PriorContext(),
        "t",
        "r",
    )
    off = build_extraction_prompt(_PROFILE, _state(), PriorContext(), "t", "r")
    start = on.index("\n\nThe JSON object must also include")
    end = on.index("share_restriction, not this.") + len("share_restriction, not this.")
    assert on[:start] + on[end:] == off
