"""
Unit tests for the consolidation pipeline's own logic.

These deliberately do NOT test whether the model groups or composes well —
that is the model's judgement, it needs real calls, and it lives in
evals/run_consolidation_eval.py with the TC-12..TC-18 fixtures. What is
tested here is the machinery around it: the arc bar, carrying a thread
across sessions, and what happens to a draft when the fidelity check
rejects a sentence. A mocked LLM can answer those exactly.
"""

from __future__ import annotations

import json
import pathlib

import pytest

from consolidation.arc import Arc, ArcElement
from consolidation.pipeline import (
    Thread,
    Usage,
    arc_still_holds,
    compose_and_check,
    consolidate_session,
    parse_json_object,
    select_source_turns,
)

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "consolidation"


def load_fixture(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _el(present: bool, *turn_ids: str) -> ArcElement:
    return ArcElement(present=present, summary="x", turn_ids=list(turn_ids))


def scripted_llm(replies: list[dict]):
    """An LLM that returns queued replies in order, recording the prompts."""
    queue = list(replies)
    seen: list[tuple[str, str]] = []

    async def call(prompt: str, *, step: str, max_tokens: int):
        seen.append((step, prompt))
        reply = queue.pop(0) if queue else {}
        return json.dumps(reply), 100, 50

    call.seen = seen  # type: ignore[attr-defined]
    return call


# ── the arc bar (§4, D1) ─────────────────────────────────────────────────────


def test_setting_event_turn_is_a_story():
    arc = Arc(setting=_el(True, "t1"), event=_el(True, "t2"), turn=_el(True, "t3"))
    assert arc.is_complete is True


def test_resolution_is_not_required():
    arc = Arc(setting=_el(True, "t1"), event=_el(True, "t2"), turn=_el(True, "t3"))
    assert "resolution" in arc.missing
    assert arc.is_complete is True


def test_missing_turn_is_not_a_story():
    arc = Arc(setting=_el(True, "t1"), event=_el(True, "t2"))
    assert arc.is_complete is False
    assert arc.missing == ["turn", "resolution"]


def test_element_claimed_present_without_a_turn_does_not_count():
    """§4: an element the model inferred but the person did not say does
    not count. A `present` flag with no turn behind it is the model
    filling in a gap."""
    arc = Arc(setting=_el(True, "t1"), event=_el(True, "t2"), turn=_el(True))
    assert arc.turn.is_evidenced is False
    assert arc.is_complete is False


def test_source_turn_ids_are_deduped_in_telling_order():
    arc = Arc(
        setting=_el(True, "t1"), event=_el(True, "t2", "t1"), turn=_el(True, "t3")
    )
    assert arc.source_turn_ids == ["t1", "t2", "t3"]


# ── carrying a thread across sessions (§6.2) ─────────────────────────────────


def test_merge_keeps_the_earlier_telling_and_accumulates_turns():
    first = Arc(setting=_el(True, "t1"), event=_el(True, "t2"))
    first.setting.summary = "near the river"
    later = Arc(setting=_el(True, "t9"), turn=_el(True, "t10"))
    later.setting.summary = "a house by water"

    merged = first.merged_with(later)

    assert merged.setting.summary == "near the river"
    assert merged.setting.turn_ids == ["t1", "t9"]
    assert merged.turn.is_evidenced is True
    assert merged.is_complete is True


def test_merge_fills_a_gap_from_the_later_session():
    first = Arc(setting=_el(True, "t1"), event=_el(True, "t2"))
    later = Arc(turn=_el(True, "t3"), resolution=_el(True, "t4"))
    assert first.merged_with(later).is_complete is True


# ── what survives the fidelity cut (§5.3) ────────────────────────────────────


def test_arc_still_holds_requires_every_required_element_to_survive():
    arc = Arc(setting=_el(True, "t1"), event=_el(True, "t2"), turn=_el(True, "t3"))
    assert arc_still_holds(arc, {"t1", "t2", "t3"}) is True
    # The only sentence carrying the peak was cut.
    assert arc_still_holds(arc, {"t1", "t2"}) is False


def test_resolution_may_be_cut_without_demoting_the_story():
    arc = Arc(
        setting=_el(True, "t1"),
        event=_el(True, "t2"),
        turn=_el(True, "t3"),
        resolution=_el(True, "t4"),
    )
    assert arc_still_holds(arc, {"t1", "t2", "t3"}) is True


# ── parsing model replies ────────────────────────────────────────────────────


def test_parse_json_object_tolerates_a_code_fence():
    assert parse_json_object('```json\n{"a": 1}\n```') == {"a": 1}


def test_parse_json_object_tolerates_surrounding_prose():
    assert parse_json_object('Sure!\n{"a": 1}\nHope that helps.') == {"a": 1}


def test_parse_json_object_returns_empty_on_garbage():
    """Never raises: in Phase B this runs off the critical path and must
    not be able to take down post-session processing."""
    assert parse_json_object("no json here") == {}
    assert parse_json_object('{"unclosed": ') == {}


def test_select_source_turns_returns_spoken_order():
    arc = Arc(setting=_el(True, "t3"), event=_el(True, "t1"))
    turns = [{"id": "t1"}, {"id": "t2"}, {"id": "t3"}]
    assert [t["id"] for t in select_source_turns(arc, turns)] == ["t1", "t3"]


# ── compose + fidelity behaviour ─────────────────────────────────────────────


_TURNS = [
    {"id": "t1", "transcript": "We were near the river.", "detected_language": "en-IN"},
    {"id": "t2", "transcript": "The water came in.", "detected_language": "en-IN"},
    {"id": "t3", "transcript": "Appa carried her out.", "detected_language": "en-IN"},
]
_ARC = Arc(setting=_el(True, "t1"), event=_el(True, "t2"), turn=_el(True, "t3"))


async def _run_compose(fidelity_replies: list[dict]) -> Thread:
    thread = Thread(id="x", title="draft", arc=_ARC)
    call = scripted_llm(
        [
            {
                "title": "The night the river came in",
                "body_first_person": "A. B. C.",
                "quotes": [],
            },
            *fidelity_replies,
        ]
    )
    await compose_and_check(
        incident={}, thread=thread, all_turns=_TURNS, call_llm=call, usage=Usage()
    )
    return thread


async def test_clean_draft_passes_and_becomes_a_story():
    thread = await _run_compose(
        [
            {
                "sentences": [
                    {"text": "A.", "verdict": "supported", "turn_ids": ["t1"]},
                    {"text": "B.", "verdict": "supported", "turn_ids": ["t2"]},
                    {"text": "C.", "verdict": "supported", "turn_ids": ["t3"]},
                ]
            }
        ]
    )
    assert thread.status == "story"
    assert thread.fidelity_status == "passed"
    assert thread.body_first_person == "A. B. C."


async def test_unsupported_sentence_is_cut_and_the_draft_is_rechecked():
    thread = await _run_compose(
        [
            {
                "sentences": [
                    {"text": "A.", "verdict": "supported", "turn_ids": ["t1"]},
                    {"text": "B.", "verdict": "supported", "turn_ids": ["t2"]},
                    {"text": "C.", "verdict": "supported", "turn_ids": ["t3"]},
                    {
                        "text": "I was terrified.",
                        "verdict": "unsupported",
                        "turn_ids": [],
                        "reason": "no emotion stated",
                    },
                ]
            },
            {
                "sentences": [
                    {"text": "A.", "verdict": "supported", "turn_ids": ["t1"]},
                    {"text": "B.", "verdict": "supported", "turn_ids": ["t2"]},
                    {"text": "C.", "verdict": "supported", "turn_ids": ["t3"]},
                ]
            },
        ]
    )
    assert thread.status == "story"
    assert thread.fidelity_status == "revised"
    assert "I was terrified." not in (thread.body_first_person or "")
    assert thread.removed_sentences[0]["text"] == "I was terrified."


async def test_story_is_demoted_when_the_cut_removes_the_peak():
    """§5.3: if the arc no longer holds after removal, it goes back to
    Thread. Here the only sentence carrying the `turn` was invented."""
    thread = await _run_compose(
        [
            {
                "sentences": [
                    {"text": "A.", "verdict": "supported", "turn_ids": ["t1"]},
                    {"text": "B.", "verdict": "supported", "turn_ids": ["t2"]},
                    {"text": "C.", "verdict": "unsupported", "turn_ids": []},
                ]
            },
            {
                "sentences": [
                    {"text": "A.", "verdict": "supported", "turn_ids": ["t1"]},
                    {"text": "B.", "verdict": "supported", "turn_ids": ["t2"]},
                ]
            },
        ]
    )
    assert thread.status == "thread"
    assert thread.fidelity_status == "failed"
    assert thread.body_first_person is None


async def test_connective_sentences_are_kept_but_carry_no_arc():
    thread = await _run_compose(
        [
            {
                "sentences": [
                    {"text": "A.", "verdict": "supported", "turn_ids": ["t1"]},
                    {"text": "So.", "verdict": "connective", "turn_ids": []},
                    {"text": "B.", "verdict": "supported", "turn_ids": ["t2"]},
                    {"text": "C.", "verdict": "supported", "turn_ids": ["t3"]},
                ]
            }
        ]
    )
    assert thread.status == "story"
    assert "So." in (thread.body_first_person or "")


# ── session-level orchestration ──────────────────────────────────────────────


async def test_incomplete_arc_stays_a_thread_and_never_composes():
    """TC-15 shape: no compose call should be made at all."""
    fixture = load_fixture("tc15")
    session = fixture["sessions"][0]
    call = scripted_llm(
        [
            {
                "incidents": [
                    {
                        "working_title": "the year the well went dry",
                        "atom_ids": ["a-1", "a-2"],
                        "extends_thread_id": None,
                        "arc": {
                            "setting": {
                                "present": True,
                                "summary": "Palakkad quarters",
                                "turn_ids": ["t1-1"],
                            },
                            "event": {
                                "present": True,
                                "summary": "monsoon failed",
                                "turn_ids": ["t1-2"],
                            },
                            "turn": {"present": False, "summary": "", "turn_ids": []},
                            "resolution": {
                                "present": False,
                                "summary": "",
                                "turn_ids": [],
                            },
                        },
                    }
                ],
                "unassigned_atom_ids": [],
            }
        ]
    )
    threads: dict[str, Thread] = {}
    result = await consolidate_session(
        session_id="s1",
        session_number=1,
        turns=session["turns"],
        atoms=session["atoms"],
        threads=threads,
        significant_people=[],
        call_llm=call,
    )
    assert result.promoted_thread_ids == []
    assert len(threads) == 1
    thread = next(iter(threads.values()))
    assert thread.status == "thread"
    assert "turn" in thread.arc.missing
    assert [step for step, _ in call.seen] == ["group"]


async def test_hallucinated_turn_ids_are_dropped_before_the_arc_test():
    """A turn id the model invented must not prop up an arc element."""
    call = scripted_llm(
        [
            {
                "incidents": [
                    {
                        "working_title": "x",
                        "atom_ids": [],
                        "extends_thread_id": None,
                        "arc": {
                            "setting": {
                                "present": True,
                                "summary": "",
                                "turn_ids": ["t1-1"],
                            },
                            "event": {
                                "present": True,
                                "summary": "",
                                "turn_ids": ["t1-2"],
                            },
                            "turn": {
                                "present": True,
                                "summary": "",
                                "turn_ids": ["nope-999"],
                            },
                            "resolution": {
                                "present": False,
                                "summary": "",
                                "turn_ids": [],
                            },
                        },
                    }
                ]
            }
        ]
    )
    turns = [
        {"id": "t1-1", "transcript": "a", "detected_language": "en-IN"},
        {"id": "t1-2", "transcript": "b", "detected_language": "en-IN"},
    ]
    threads: dict[str, Thread] = {}
    await consolidate_session(
        session_id="s1",
        session_number=1,
        turns=turns,
        atoms=[],
        threads=threads,
        significant_people=[],
        call_llm=call,
    )
    thread = next(iter(threads.values()))
    assert thread.arc.turn.turn_ids == []
    assert thread.status == "thread"


async def test_token_usage_is_recorded_per_step():
    call = scripted_llm([{"incidents": [], "unassigned_atom_ids": []}])
    result = await consolidate_session(
        session_id="s1",
        session_number=1,
        turns=[{"id": "t1", "transcript": "a", "detected_language": "en-IN"}],
        atoms=[],
        threads={},
        significant_people=[],
        call_llm=call,
    )
    assert result.usage.by_step["group"].calls == 1
    assert result.usage.totals.input_tokens == 100
    assert result.usage.totals.output_tokens == 50


@pytest.mark.parametrize(
    "name", ["tc12", "tc13", "tc14", "tc15", "tc16", "tc17", "tc18"]
)
def test_fixture_is_well_formed(name):
    """The fixtures drive the live eval; a malformed one would fail there
    as a scoring artifact rather than a real result."""
    fixture = load_fixture(name)
    assert fixture["id"].startswith("TC-")
    assert fixture["sessions"], "needs at least one session"
    turn_ids = {t["id"] for s in fixture["sessions"] for t in s["turns"]}
    for session in fixture["sessions"]:
        for atom in session["atoms"]:
            assert atom["turn_id"] in turn_ids, f"{name}: atom cites unknown turn"
        for turn in session["turns"]:
            assert turn["transcript"].strip()
