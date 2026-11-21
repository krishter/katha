"""The Story review page with operator review off shows no family content
(STORIES.md §6c); with it on, the full review view."""

from __future__ import annotations

import uuid
from types import SimpleNamespace

from stories import review_page


def _story(**over):
    base = dict(
        id=uuid.uuid4(),
        user_id="user_x",
        title="The night the river came in",
        domain="childhood",
        when_approx="1958",
        sessions_seen=[2, 3],
        body_first_person="My uncle carried my grandmother to the temple steps.",
        quotes=[{"original": "the water came in", "gloss": "the flood"}],
        readback_language="ml-IN",
        readback_text="വെള്ളം കയറി",
        readback_check={"result": "passed", "passes": []},
        sentence_sources=[{"verdict": "supported", "text": "Uncle carried her."}],
        removed_sentences=[{"text": "Uncle woke us.", "reason": "never said"}],
        fidelity_status="passed",
        status="ready",
        review_status="waiting",
        approval_status=None,
        retired_reason=None,
        updated_at="2026-11-21",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _queue(story):
    turn = SimpleNamespace(
        id=uuid.uuid4(),
        turn_number=1,
        detected_language="ml-IN",
        transcript="Ammachi was on his back",
    )
    return {
        "waiting": [{"story": story, "turns": [turn], "parent_title": "Amma"}],
        "recent": [_story(title="A second story title")],
        "threads": 1,
    }


FAMILY_WORDS = (
    "The night the river came in",
    "temple steps",
    "the water came in",
    "വെള്ളം കയറി",
    "Uncle carried her.",
    "Uncle woke us.",
    "Ammachi was on his back",
    "A second story title",
)


def test_with_review_off_no_family_content_is_on_the_page():
    story = _story()
    html = review_page.render(_queue(story), "csrf", show_content=False)
    for words in FAMILY_WORDS:
        assert words not in html, f"{words!r} is on the page with review off"
    assert str(story.id) in html
    assert "Operator review is off" in html
    assert "/admin/stories/" not in html, "no review actions without content"


def test_with_review_on_the_page_is_the_full_review_view():
    html = review_page.render(_queue(_story()), "csrf", show_content=True)
    for words in FAMILY_WORDS:
        assert words in html
