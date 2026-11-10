"""Stories Stage 1, without a database (docs/specs/STORIES.md §2.1)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

import stories
from consolidation.prompts import build_grouping_prompt
from stories import readback


def _profile(on: bool):
    return SimpleNamespace(stories_enabled=on)


@pytest.mark.parametrize(
    "global_on, family_on, expected",
    [(False, True, False), (True, False, False), (True, True, True)],
)
def test_stories_need_both_switches(monkeypatch, global_on, family_on, expected):
    monkeypatch.setattr("config.settings.STORIES_ENABLED", global_on)
    assert stories.enabled_for(_profile(family_on)) is expected
    assert stories.enabled_for(None) is False


def test_private_topics_reach_the_grouping_prompt_only_when_there_are_some():
    args = dict(turns=[], atoms=[], open_threads=[], significant_people=[])
    plain = build_grouping_prompt(**args)
    assert build_grouping_prompt(**args, private_topics=[]) == plain
    guarded = build_grouping_prompt(**args, private_topics=["her brother's debts"])
    assert "her brother's debts" in guarded
    assert "unassigned_atom_ids" in guarded.split("her brother's debts")[0]


def test_a_readback_problem_is_any_added_or_missing_sentence():
    assert readback.problems_in({}) == ["the check returned no sentences"]
    clean = {
        "sentences": [
            {"text": "This is your story.", "verdict": "framing"},
            {"text": "I was ten.", "verdict": "supported"},
        ],
        "missing_from_readback": [],
    }
    assert readback.problems_in(clean) == []
    added = {
        "sentences": [{"text": "I was scared.", "verdict": "unsupported"}],
        "missing_from_readback": ["Appa carried my grandmother out."],
    }
    problems = readback.problems_in(added)
    assert len(problems) == 2
    assert "I was scared." in problems[0]
    assert "Appa carried" in problems[1]


class Script:
    def __init__(self, replies):
        self.replies = list(replies)
        self.prompts = []

    async def __call__(self, prompt, step, max_tokens):
        self.prompts.append((step, prompt))
        return self.replies.pop(0)


_PASSED = {"sentences": [{"text": "x", "verdict": "supported"}]}
_FAILED = {
    "sentences": [{"text": "I was scared.", "verdict": "unsupported", "reason": "r"}]
}


async def _write(script):
    return await readback.write_readback(
        title="The flood",
        body="I was ten. The water came in.",
        quotes=[{"original": "വെള്ളം കയറി"}],
        language="ml-IN",
        parent_title="Amma",
        child_name="Anand",
        ask=script,
    )


async def test_a_readback_that_passes_first_time_is_kept():
    script = Script([{"readback_text": "ഒന്ന്"}, _PASSED])
    text, check = await _write(script)
    assert text == "ഒന്ന്" and check["result"] == "passed"
    write_prompt = script.prompts[0][1]
    assert "Malayalam" in write_prompt and "വെള്ളം കയറി" in write_prompt


async def test_a_failed_readback_is_rewritten_once_with_its_problems():
    script = Script(
        [{"readback_text": "ഒന്ന്"}, _FAILED, {"readback_text": "രണ്ട്"}, _PASSED]
    )
    text, check = await _write(script)
    assert text == "രണ്ട്" and check["result"] == "passed"
    rewrite = script.prompts[2][1]
    assert "I was scared." in rewrite and "ഒന്ന്" in rewrite


async def test_a_readback_still_failing_after_the_rewrite_stays_failed():
    script = Script(
        [{"readback_text": "ഒന്ന്"}, _FAILED, {"readback_text": "രണ്ട്"}, _FAILED]
    )
    text, check = await _write(script)
    assert check["result"] == "failed" and len(check["passes"]) == 2
