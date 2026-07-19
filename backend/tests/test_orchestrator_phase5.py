from __future__ import annotations

import uuid
from contextlib import ExitStack, contextmanager
from unittest.mock import AsyncMock, MagicMock, patch

from core.orchestrator import close_and_process_session
from core.session_manager import SessionState
from memory_cards.generator import MemoryCardResult

_SESSION_ID = str(uuid.uuid4())
_USER_ID = "user-1"

_SESSION_STATE = SessionState(
    session_id=_SESSION_ID,
    user_id=_USER_ID,
    session_number=1,
    domain="childhood",
    exchange_count=6,
    energy_signal="high",
    goal_met=True,
    session_end_suggested=True,
)

_EXTRACTION_JSON = {
    "story_atoms": [{"narrative": "Test story", "domain": "childhood"}],
    "significant_people": [],
    "themes": ["childhood"],
    "energy_signal": "high",
    "gaps_remaining": [],
    "session_end_suggested": True,
}

_CARD_RESULT = MemoryCardResult(
    image_bytes=b"\x89PNG\r\n\x1a\nfakepngdata",
    verbatim_quote="The street smelled of jasmine.",
    domain="childhood",
    story_atom_id=str(uuid.uuid4()),
)


def _make_profile(family_number="+919876543210"):
    profile = MagicMock()
    profile.name = "Subramaniam"
    profile.family_whatsapp_number = family_number
    return profile


def _make_db():
    return AsyncMock()


@contextmanager
def _patched(profile, card_result):
    """Patch the collaborators close_and_process_session touches, yielding
    the whatsapp adapter mock and the save_memory_card mock for assertions."""
    mock_whatsapp = MagicMock()
    mock_whatsapp.send_image = AsyncMock(return_value="SM_CARD_123")

    with ExitStack() as stack:
        stack.enter_context(
            patch(
                "core.orchestrator.session_manager.get_session",
                new=AsyncMock(return_value=_SESSION_STATE),
            )
        )
        stack.enter_context(
            patch("core.orchestrator.run_post_session", new=AsyncMock())
        )
        stack.enter_context(
            patch(
                "core.orchestrator.get_user_profile",
                new=AsyncMock(return_value=profile),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.memory_card_generator.generate_memory_card",
                new=AsyncMock(return_value=card_result),
            )
        )
        stack.enter_context(
            patch(
                "core.orchestrator.storage.upload_media",
                new=AsyncMock(
                    return_value="https://s3.amazonaws.com/katha-media/cards/x.png"
                ),
            )
        )
        stack.enter_context(
            patch("core.orchestrator.get_whatsapp_adapter", return_value=mock_whatsapp)
        )
        mock_save = stack.enter_context(
            patch("core.orchestrator.save_memory_card", new=AsyncMock())
        )
        yield mock_whatsapp, mock_save


async def test_close_and_process_session_delivers_card_when_family_number_set():
    db = _make_db()
    profile = _make_profile()

    with _patched(profile, _CARD_RESULT) as (mock_whatsapp, mock_save):
        await close_and_process_session(_SESSION_ID, "transcript", _EXTRACTION_JSON, db)

    mock_whatsapp.send_image.assert_called_once()
    call_kwargs = mock_whatsapp.send_image.call_args.kwargs
    assert call_kwargs["to_number"] == "+919876543210"
    assert call_kwargs["image_bytes"] == _CARD_RESULT.image_bytes
    mock_save.assert_called_once()


async def test_close_and_process_session_skips_delivery_without_family_number():
    db = _make_db()
    profile = _make_profile(family_number=None)

    with _patched(profile, _CARD_RESULT) as (mock_whatsapp, mock_save):
        await close_and_process_session(_SESSION_ID, "transcript", _EXTRACTION_JSON, db)

    mock_whatsapp.send_image.assert_not_called()
    mock_save.assert_not_called()


async def test_close_and_process_session_skips_when_no_card_generated():
    db = _make_db()
    profile = _make_profile()

    with _patched(profile, None) as (mock_whatsapp, mock_save):
        await close_and_process_session(_SESSION_ID, "transcript", _EXTRACTION_JSON, db)

    mock_whatsapp.send_image.assert_not_called()
    mock_save.assert_not_called()


async def test_close_and_process_session_does_not_raise_when_card_step_fails():
    db = _make_db()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=_SESSION_STATE),
        ),
        patch("core.orchestrator.run_post_session", new=AsyncMock()),
        patch(
            "core.orchestrator.get_user_profile",
            new=AsyncMock(side_effect=RuntimeError("DB error")),
        ),
    ):
        # Should not raise even though memory-card generation blew up.
        await close_and_process_session(_SESSION_ID, "transcript", _EXTRACTION_JSON, db)


async def test_close_and_process_session_does_not_generate_card_when_extraction_fails():
    db = _make_db()

    mock_generate_card = AsyncMock()

    with (
        patch(
            "core.orchestrator.session_manager.get_session",
            new=AsyncMock(return_value=_SESSION_STATE),
        ),
        patch(
            "core.orchestrator.run_post_session",
            new=AsyncMock(side_effect=RuntimeError("boom")),
        ),
        patch(
            "core.orchestrator.memory_card_generator.generate_memory_card",
            mock_generate_card,
        ),
    ):
        await close_and_process_session(_SESSION_ID, "transcript", _EXTRACTION_JSON, db)

    mock_generate_card.assert_not_called()
