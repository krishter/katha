"""
After a session: the memory card or a conversation card, never both
(CONVERSATION_CARDS.md D2, §17 "with the flag off, behaviour matches
today's, including the memory card").
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from core import orchestrator


@pytest.fixture
def paths():
    with (
        patch.object(orchestrator, "run_post_session", new=AsyncMock()) as post,
        patch.object(
            orchestrator, "_generate_and_deliver_memory_card", new=AsyncMock()
        ) as memory_card,
        patch.object(
            orchestrator.card_writer, "draft_for_session", new=AsyncMock()
        ) as draft,
        patch.object(orchestrator, "get_user_profile", new=AsyncMock()) as profile,
        # She has agreed: P4's guard lets the outputs through.
        patch.object(
            orchestrator.parent_consent,
            "get_status",
            new=AsyncMock(
                return_value=SimpleNamespace(
                    status=orchestrator.parent_consent.ConsentStatus.GRANTED
                )
            ),
        ),
    ):
        yield SimpleNamespace(
            post=post, memory_card=memory_card, draft=draft, profile=profile
        )


async def test_with_the_switch_off_it_is_todays_path_untouched(paths, monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", False)
    await orchestrator._produce_session_outputs("s1", "u1", db=object())
    paths.post.assert_awaited_once()
    paths.memory_card.assert_awaited_once()
    paths.draft.assert_not_awaited()
    # Not even a profile read.
    paths.profile.assert_not_awaited()


async def test_a_family_without_cards_keeps_the_memory_card(paths, monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    paths.profile.return_value = SimpleNamespace(conversation_cards_enabled=False)
    await orchestrator._produce_session_outputs("s1", "u1", db=object())
    paths.memory_card.assert_awaited_once()
    paths.draft.assert_not_awaited()


async def test_a_family_with_cards_gets_a_draft_instead(paths, monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    paths.profile.return_value = SimpleNamespace(conversation_cards_enabled=True)
    await orchestrator._produce_session_outputs("s1", "u1", db=object())
    paths.draft.assert_awaited_once()
    paths.memory_card.assert_not_awaited()
    # Entity extraction still runs first.
    paths.post.assert_awaited_once()


async def test_not_knowing_sends_nothing(paths, monkeypatch):
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    paths.profile.side_effect = RuntimeError("db down")
    await orchestrator._produce_session_outputs("s1", "u1", db=object())
    paths.memory_card.assert_not_awaited()
    paths.draft.assert_not_awaited()


def test_shape_problems_catch_what_code_can():
    from cards.write import shape_problems

    good = {
        "title": "t",
        "hook": "Did you know Amma walked to the talkies?",
        "excerpt": "We walked.",
        "call_prompt": "What film was it?",
        "readback_text": "…",
    }
    assert shape_problems(good) == []
    assert shape_problems(dict(good, hook="Amma walked to the talkies.")) == [
        'hook must start "Did you know"'
    ]
    assert shape_problems(dict(good, hook="You asked Amma which film it was.")) == []
    assert any("excerpt is" in p for p in shape_problems(dict(good, excerpt="w " * 61)))
    assert any(
        "call_prompt is" in p for p in shape_problems(dict(good, call_prompt="w " * 16))
    )
    assert shape_problems(dict(good, original_quote="யாருக்கும்")) == [
        "original_quote and its gloss go together"
    ]
    assert "readback_text is empty" in shape_problems(dict(good, readback_text=""))


def test_a_readback_that_leaves_the_english_hook_untranslated_is_a_problem():
    from cards.write import shape_problems

    fields = {
        "title": "t",
        "hook": "Did you know Appa named the mango tree Raja?",
        "excerpt": "I named it Raja.",
        "call_prompt": "What was Raja like?",
        "readback_text": "प्रिया साठी. Did you know Appa named the mango tree Raja?",
    }
    assert any("untranslated" in p for p in shape_problems(fields, "mr-IN"))
    # English for an English speaker is the point.
    assert shape_problems(fields, "en-IN") == []
    translated = dict(
        fields, readback_text="प्रिया साठी. अप्पांनी आंब्याच्या झाडाला राजा नाव दिलं."
    )
    assert shape_problems(translated, "mr-IN") == []


@pytest.mark.parametrize("status", ["WITHDRAWN", "DECLINED"])
async def test_nothing_is_made_once_she_has_said_stop(paths, monkeypatch, status):
    """P4: an earlier turn's extraction can close the session after she
    withdrew. Her family must not get a card from it."""
    monkeypatch.setattr("config.settings.CONVERSATION_CARDS_ENABLED", True)
    consent = orchestrator.parent_consent
    with patch.object(
        consent,
        "get_status",
        new=AsyncMock(
            return_value=SimpleNamespace(status=getattr(consent.ConsentStatus, status))
        ),
    ):
        await orchestrator._produce_session_outputs("s1", "u1", db=object())
    paths.post.assert_not_awaited()
    paths.memory_card.assert_not_awaited()
    paths.draft.assert_not_awaited()
