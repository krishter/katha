"""scripts/consolidate_stories.py: a family's run reports counts only."""

from __future__ import annotations

from config import settings
from consolidation.pipeline import Thread, Usage
from scripts import consolidate_stories as cs


def test_only_self_trial_and_eval_accounts_may_show_content(monkeypatch):
    monkeypatch.setattr(settings, "METRICS_EXCLUDED_USER_IDS", "user_selftrial, user_b")
    assert cs.may_show_content("user_selftrial")
    assert cs.may_show_content("eval-tc41")
    assert not cs.may_show_content("user_family_one")


def test_counts_hold_no_words():
    story = Thread(id="t1", title="The red door at Vellayani", people=["Kunjamma"])
    story.status = "story"
    open_thread = Thread(id="t2", title="The bus to Kottayam", places=["Kottayam"])
    sessions = [{"empty": False}, {"empty": True}]

    class R:
        incidents_found = 2
        errors = ["a note quoting her"]

    summary = cs.counts(
        "user_x", sessions, {"t1": story, "t2": open_thread}, [R()], Usage()
    )

    assert summary["incidents"] == 2
    assert summary["stories"] == 1
    assert summary["complete_arc_share"] == 0.5
    assert summary["open_threads"] == 1
    assert set(summary["open_threads_missing"]) == set(cs.ARC_PARTS)
    assert summary["errors"] == 1
    text = repr(summary)
    for words in ("red door", "Vellayani", "Kunjamma", "Kottayam", "quoting her"):
        assert words not in text
