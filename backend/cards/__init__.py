"""
Conversation cards ("Did you know?"): docs/specs/CONVERSATION_CARDS.md.

A card is one surprising, true detail from her life, a few lines in her
words and one question for her child's next call. It reaches the child
only after she has heard it read and said yes (§4.1).
"""

from __future__ import annotations

from config import settings


def enabled_for(profile) -> bool:
    """
    Cards run for a parent only when the global switch is on and an
    operator has turned them on for her family. Either off, and nothing
    about her sessions changes, the per-session memory card included.
    """
    return bool(
        settings.CONVERSATION_CARDS_ENABLED
        and profile is not None
        and getattr(profile, "conversation_cards_enabled", False)
    )
