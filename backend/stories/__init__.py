"""
Stories: docs/specs/STORIES.md.

After each session the consolidation pass (consolidation/) groups what she
told by incident. An incident she has only part-told is a thread; one with
a complete arc becomes a Story, written in her own first-person voice. A
Story reaches the family only after the operator has passed it and she has
heard it read back and said yes.
"""

from __future__ import annotations

from config import settings


def enabled_for(profile) -> bool:
    """
    Stories run for a parent only when the global switch is on and an
    operator has turned them on for her family. Either off, and nothing
    about her sessions changes.
    """
    return bool(
        settings.STORIES_ENABLED
        and profile is not None
        and getattr(profile, "stories_enabled", False)
    )
