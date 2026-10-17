from types import SimpleNamespace

from core.session_manager import SessionState
from prompts.system_prompt import (
    PriorContext,
    UserProfile,
    build_extraction_prompt,
    build_system_prompt,
)

# Minimal SessionState stand-in (real class defined in session_manager)
_SESSION = SimpleNamespace(
    session_id="test-session-id",
    user_id="user-1",
    session_number=1,
    domain="childhood",
    exchange_count=0,
    energy_signal="high",
    goal_met=False,
    session_end_suggested=False,
)

_PROFILE = UserProfile(
    name="Subramaniam",
    preferred_language="ta-IN",
    onboarding_context="Grew up in Madurai. Retired schoolteacher.",
)

_PRIOR = PriorContext()


def _build() -> str:
    return build_system_prompt(_PROFILE, _SESSION, _PRIOR)


def test_prompt_contains_user_name():
    prompt = _build()
    assert "Subramaniam" in prompt


def test_prompt_contains_domain_name():
    prompt = _build()
    assert "Childhood" in prompt


def test_prompt_contains_response_tag_only():
    """The dialogue prompt returns <response> only — extraction is a
    separate, off-critical-path call (see build_extraction_prompt)."""
    prompt = _build()
    assert "<response>" in prompt
    assert "<extraction>" not in prompt


def test_prompt_contains_icall_crisis_number():
    prompt = _build()
    assert "9152987821" in prompt


def test_prompt_length_stays_bounded():
    """
    A tripwire against unbounded prompt growth, not a limit derived from
    any model constraint — the fixture below is a fixed yardstick, and a
    real prompt with a long family note and many facts runs longer.

    Raised from 4500 to 5000 when pronouns moved into Layer 1 and the
    family's onboarding note stopped being dropped after the first turn.
    Both are instructions the prompt is now wrong without: the first stops
    Katha guessing an elderly woman's gender from her name, and the second
    is the only reason anything the family wrote survives past turn one.
    Compressing them further would have been trimming the fix to fit the
    measurement.

    Raised from 5000 to 6000 (4968 -> 5692) on 2026-10-17 for the Hindi
    listening review: check in with a tired parent before asking anything,
    exactly one question per reply, vary the opening, and no emoji in
    speech. Each was a fault heard in real replies.

    Raise it again only for something equally load-bearing, and say what
    in here.
    """
    prompt = _build()
    assert len(prompt) < 6000, f"Prompt too long: {len(prompt)} chars"


def test_prompt_contains_language_name_not_code():
    prompt = _build()
    # Should say "Tamil" not "ta-IN"
    assert "Tamil" in prompt
    assert "ta-IN" not in prompt


def test_prompt_contains_sixth_principle_unforgettable_people():
    prompt = _build()
    assert "unforgettable people" in prompt.lower()


def test_prompt_layer3_includes_significant_people_when_present():
    prior = PriorContext(
        significant_people=[
            {
                "name": "Mr. Iyer",
                "relationship": "school teacher",
                "why_significant": "Inspired teaching career",
            }
        ]
    )
    prompt = build_system_prompt(_PROFILE, _SESSION, prior)
    assert "Mr. Iyer" in prompt
    assert "Not yet fully explored" in prompt


def test_prompt_layer3_omits_significant_people_block_when_empty():
    prior = PriorContext(significant_people=[])
    prompt = build_system_prompt(_PROFILE, _SESSION, prior)
    assert "Not yet fully explored" not in prompt


def test_prompt_layer5_does_not_include_extraction_schema():
    """Extraction fields moved to build_extraction_prompt — the dialogue
    prompt should no longer describe the extraction JSON schema at all."""
    prompt = _build()
    assert "significant_people" not in prompt


def test_prompt_instructs_closing_when_goal_already_met():
    """
    Regression guard (WS2.1 eval): since session_end_suggested is now
    decided by a separate, deferred extraction call, the dialogue call
    needs its own synchronous signal that the domain goal is already met
    so it can close warmly and preview tomorrow, instead of the two calls
    disagreeing about whether this is the last exchange.
    """
    met_session = SimpleNamespace(**{**_SESSION.__dict__, "goal_met": True})
    prompt = build_system_prompt(_PROFILE, met_session, _PRIOR)
    assert "closing exchange" in prompt.lower()


def test_prompt_omits_closing_instruction_when_goal_not_met():
    prompt = _build()
    assert "closing exchange" not in prompt.lower()


# ── build_extraction_prompt ───────────────────────────────────────────────────


def _build_extraction(prior=None) -> str:
    return build_extraction_prompt(
        _PROFILE,
        _SESSION,
        prior or _PRIOR,
        user_transcript="I grew up in a small house near the river in Madurai.",
        assistant_response="That sounds lovely — tell me more about the river.",
    )


def test_extraction_prompt_contains_extraction_tag_only():
    prompt = _build_extraction()
    assert "<extraction>" in prompt
    assert "<response>" not in prompt


def test_extraction_prompt_contains_user_transcript():
    prompt = _build_extraction()
    assert "Madurai" in prompt


def test_extraction_prompt_contains_domain_name():
    prompt = _build_extraction()
    assert "Childhood" in prompt


def test_extraction_prompt_contains_significant_people_field():
    prompt = _build_extraction()
    assert "significant_people" in prompt


def test_extraction_prompt_lists_known_significant_people():
    prior = PriorContext(
        significant_people=[
            {
                "name": "Mr. Iyer",
                "relationship": "school teacher",
                "why_significant": "Inspired teaching career",
            }
        ]
    )
    prompt = _build_extraction(prior=prior)
    assert "Mr. Iyer" in prompt


def test_extraction_prompt_notes_goal_already_met():
    """
    Regression guard: the extraction call must know goal_met independently
    of the dialogue call's closing instruction, so session_end_suggested
    isn't decided by two calls reasoning about the same fact separately.
    """
    met_session = SimpleNamespace(**{**_SESSION.__dict__, "goal_met": True})
    prompt = build_extraction_prompt(
        _PROFILE,
        met_session,
        _PRIOR,
        user_transcript="I grew up in a small house near the river in Madurai.",
        assistant_response="That sounds lovely — tell me more about the river.",
    )
    assert "already met" in prompt.lower()


def test_extraction_prompt_omits_goal_met_note_when_not_met():
    prompt = _build_extraction()
    assert "already met" not in prompt.lower()


def test_extraction_prompt_gives_story_atoms_a_schema():
    """
    Regression guard (WS5.4 eval finding): significant_people had a full
    example object but story_atoms was just `[]`, and live eval runs
    showed the LLM returning atoms as plain strings — which crashes
    process_extraction (compute_completeness calls atom.get(...)) and
    silently drops the whole turn's extraction. story_atoms must show a
    concrete per-item shape, not an empty list.
    """
    prompt = _build_extraction()
    assert '"story_atoms": [\n' in prompt or '"story_atoms": [' in prompt
    assert "narrative" in prompt
    assert "not a plain string" in prompt.lower()


# ── Layer 3 "early session" branch ────────────────────────────────────────


def test_prompt_layer3_not_early_session_when_only_significant_people_known():
    """
    Regression guard (WS5.4 eval finding): the early-session claim only
    checked prior_context.facts, so a session with significant_people or
    open_threads but no structured facts yet got told "you don't yet know
    much" right above a block naming a specific person it does know about
    — an internally contradictory prompt.
    """
    prior = PriorContext(
        significant_people=[
            {
                "name": "Mr. Iyer",
                "relationship": "school teacher",
                "why_significant": "Inspired teaching career",
            }
        ]
    )
    prompt = build_system_prompt(_PROFILE, _SESSION, prior)
    assert "early session" not in prompt.lower()


def test_prompt_layer3_not_early_session_when_only_open_threads_known():
    prior = PriorContext(open_threads=["name of father's shop"])
    prompt = build_system_prompt(_PROFILE, _SESSION, prior)
    assert "early session" not in prompt.lower()


def test_prompt_layer3_is_early_session_when_nothing_known():
    prompt = build_system_prompt(_PROFILE, _SESSION, PriorContext())
    assert "early session" in prompt.lower()


# ── Layer 2 principle 8: progress through incomplete stories ──────────────


def test_prompt_contains_eighth_principle_progress_not_repeat():
    prompt = _build()
    assert "don't repeat" in prompt.lower()


# ── Layer 4 recall-forcing instruction ──────────────────────────────────────


def test_prompt_forces_recall_names_the_significant_person():
    """
    Regression guard (WS5.4 eval, pass 2): a generic "you have known facts
    ... listed in Layer 3 above" pointer scored 0/3 on live proactive-recall
    runs. Layer 4 now names the actual person/fact so the model has a
    concrete anchor to reach for, not just a pointer to go re-read.
    """
    prior = PriorContext(
        significant_people=[{"name": "Mr. Iyer", "relationship": "school teacher"}]
    )
    late_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 2})
    prompt = build_system_prompt(_PROFILE, late_session, prior)
    assert "Mr. Iyer" in prompt
    assert "you know about" in prompt.lower()


def test_prompt_forces_recall_names_a_fact_when_no_significant_person():
    prior = PriorContext(facts={"sister_name": "Kamala"})
    late_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 2})
    prompt = build_system_prompt(_PROFILE, late_session, prior)
    assert "Kamala" in prompt
    assert "you know about" in prompt.lower()


def test_prompt_omits_recall_forcing_when_nothing_known():
    late_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 2})
    prompt = build_system_prompt(_PROFILE, late_session, PriorContext())
    assert "you know about" not in prompt.lower()


def test_prompt_omits_recall_forcing_before_exchange_count_threshold():
    """Exchange 0-1 are naturally about the domain's own entry question —
    the forcing instruction should only kick in from exchange 2 onward."""
    prior = PriorContext(facts={"sister_name": "Kamala"})
    early_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 1})
    prompt = build_system_prompt(_PROFILE, early_session, prior)
    assert "you know about" not in prompt.lower()


# ── Layer 3 entry question gating ────────────────────────────────────────────


def test_prompt_includes_entry_question_on_first_exchange():
    prompt = _build()  # _SESSION has exchange_count=0
    assert "Domain entry question" in prompt


def test_prompt_omits_entry_question_after_first_exchange():
    """
    Regression guard (WS5.4 eval, pass 2): the entry question was injected
    every turn, unconditionally, and kept pulling the model back to it as
    if it were the mandatory topic — crowding out Layer 4's recall
    instruction. It should only appear on the domain's opening exchange.
    """
    later_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 1})
    prompt = build_system_prompt(_PROFILE, later_session, _PRIOR)
    assert "Domain entry question" not in prompt


# ── PriorContext render coverage (S1.5.5) ─────────────────────────────────────
#
# Three defects in two weeks shared one shape: data assembled correctly and
# then silently not consumed, invisible to mocked tests because the mocks
# supply pre-parsed structures. `recent_stories` was the clearest case — it
# was populated on every turn and rendered nowhere, for the life of the
# project. This test fails if a field is added to PriorContext without a
# corresponding render, so the next one is caught at authoring time.


def test_every_prior_context_field_is_rendered():
    import dataclasses

    populated = PriorContext(
        facts={"birth_year": "1948-SENTINEL"},
        open_threads=["THREAD-SENTINEL: what did the shop sell?"],
        significant_people=[
            {
                "name": "PERSON-SENTINEL",
                "relationship": "sister",
                "why_significant": "mentioned with unusual warmth",
            }
        ],
    )

    prompt = build_system_prompt(_PROFILE, _SESSION, populated)

    # Every field must be non-empty in the fixture, or this proves nothing.
    for f in dataclasses.fields(populated):
        value = getattr(populated, f.name)
        assert value, (
            f"PriorContext.{f.name} is empty in this fixture — populate it, "
            f"otherwise the render assertion below is vacuous for that field."
        )

    missing = []
    if "1948-SENTINEL" not in prompt:
        missing.append("facts")
    if "THREAD-SENTINEL" not in prompt:
        missing.append("open_threads")
    if "PERSON-SENTINEL" not in prompt:
        missing.append("significant_people")

    assert not missing, (
        f"PriorContext fields populated but never rendered into the prompt: "
        f"{missing}. Either render them in _layer3_life_context or remove "
        f"them from PriorContext — a field that is assembled and dropped is "
        f"work the product pays for and never receives."
    )


def test_render_coverage_test_knows_about_every_field():
    """Guards the guard: if someone adds a field to PriorContext, the
    assertions above will not cover it, so fail loudly here instead of
    passing a test that silently checks less than it claims."""
    import dataclasses

    covered = {"facts", "open_threads", "significant_people"}
    actual = {f.name for f in dataclasses.fields(PriorContext)}
    assert actual == covered, (
        f"PriorContext fields changed: {actual ^ covered}. Update "
        f"test_every_prior_context_field_is_rendered to assert the new "
        f"field reaches the prompt."
    )


# ── recall anchor priority ───────────────────────────────────────────────────
#
# The anchor is the single name Layer 4 tells the model to raise. It used to
# be significant_people[0] unconditionally, so a person the extractor flagged
# during the current session's own first turn outranked someone known for
# weeks — and Layer 4 then claimed to know them "from a past session", which
# was false. A live TC-03 run had Katha ignore a sister recorded in the fact
# store to ask about grandparents mentioned thirty seconds earlier.


def _state(session_number: int):
    return SimpleNamespace(**{**_SESSION.__dict__, "session_number": session_number})


def test_anchor_prefers_prior_session_person_over_one_met_today():
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        significant_people=[
            {"name": "Grandparents", "relationship": "family", "first_seen_session": 5},
            {"name": "Mr. Iyer", "relationship": "teacher", "first_seen_session": 2},
        ],
    )
    anchor = _pick_recall_anchor(prior, session_number=5)

    assert "Mr. Iyer" in anchor
    assert "Grandparents" not in anchor


def test_anchor_falls_back_to_facts_before_a_person_met_today():
    """Someone met this session must lose even to the fact store — that is
    where TC-03's Kamala lives, and she was never reachable while
    significant_people won unconditionally."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        facts={"people": [{"name": "Kamala", "relationship": "sister"}]},
        significant_people=[
            {"name": "Grandparents", "relationship": "family", "first_seen_session": 5},
        ],
    )
    anchor = _pick_recall_anchor(prior, session_number=5)

    assert "Kamala" in anchor


def test_anchor_uses_a_person_met_today_as_a_last_resort():
    """Better than no anchor at all — it just must not outrank memory."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        significant_people=[
            {"name": "Grandparents", "relationship": "family", "first_seen_session": 5},
        ],
    )
    assert "Grandparents" in _pick_recall_anchor(prior, session_number=5)


def test_anchor_treats_entries_without_provenance_as_old():
    """Entries written before first_seen_session existed lack the key. They
    predate the change, so 'old' is the correct reading."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(significant_people=[{"name": "Mr. Iyer"}])
    assert "Mr. Iyer" in _pick_recall_anchor(prior, session_number=9)


def test_layer3_shows_prior_session_people_first():
    """Layer 3's 2-person cap must order the same way the anchor does, or
    the prompt names two people the recall instruction does not."""
    prior = PriorContext(
        significant_people=[
            {"name": "MetToday", "relationship": "x", "first_seen_session": 7},
            {"name": "MetToday2", "relationship": "y", "first_seen_session": 7},
            {"name": "LongKnown", "relationship": "z", "first_seen_session": 1},
        ],
    )
    prompt = build_system_prompt(_PROFILE, _state(7), prior)

    assert "LongKnown" in prompt
    assert "MetToday2" not in prompt


# ── anchor: named people, and no starvation ──────────────────────────────────


def test_anchor_prefers_a_real_name_over_a_role_label():
    """Layer 4 asks the model to raise this person by name. "ask about
    Father" restates the vague pointer the anchor exists to replace, while
    "ask about Kamala (sister)" gives it a real target. A live TC-03 run
    anchored on "Father" and never reached the sister in the fact store."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        facts={"people": [{"name": "Kamala", "relationship": "sister"}]},
        significant_people=[
            {"name": "Father", "relationship": "Father", "first_seen_session": 1},
            {
                "name": "Paternal grandparents",
                "relationship": "Grandparents (father's side)",
                "first_seen_session": 1,
            },
        ],
    )
    anchor = _pick_recall_anchor(prior, session_number=2)

    assert "Kamala" in anchor


def test_anchor_falls_back_to_role_labels_when_no_name_is_known():
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        significant_people=[
            {"name": "Father", "relationship": "Father", "first_seen_session": 1},
        ],
    )
    assert "Father" in _pick_recall_anchor(prior, session_number=3)


def test_anchor_rotates_across_sessions_so_nobody_is_starved():
    """The old precedence never varied, so whoever came first held the
    anchor every session until resolved — weeks, or never — and everyone
    behind them was unreachable. Same starvation the retrieval window had."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        significant_people=[
            {"name": "Kamala", "relationship": "sister", "first_seen_session": 1},
            {"name": "Mr. Iyer", "relationship": "teacher", "first_seen_session": 1},
            {"name": "Ravi", "relationship": "friend", "first_seen_session": 1},
        ],
    )
    anchors = {_pick_recall_anchor(prior, session_number=n) for n in range(2, 8)}

    assert len(anchors) == 3, f"every remembered person should get a turn: {anchors}"


def test_anchor_is_stable_within_a_session():
    """It varies across sessions, not within one — Katha should not jump
    between people turn to turn in the same conversation."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        significant_people=[
            {"name": "Kamala", "relationship": "sister", "first_seen_session": 1},
            {"name": "Mr. Iyer", "relationship": "teacher", "first_seen_session": 1},
        ],
    )
    assert _pick_recall_anchor(prior, session_number=4) == _pick_recall_anchor(
        prior, session_number=4
    )


def test_anchor_does_not_offer_the_same_person_twice():
    """Someone can be both a curated significant person and a fact-store
    entry; rotation must not hand them two slots."""
    from prompts.system_prompt import _pick_recall_anchor

    prior = PriorContext(
        facts={"people": [{"name": "Kamala", "relationship": "sister"}]},
        significant_people=[
            {"name": "Kamala", "relationship": "sister", "first_seen_session": 1},
        ],
    )
    anchors = {_pick_recall_anchor(prior, session_number=n) for n in range(2, 6)}
    assert len(anchors) == 1


def test_is_named_person_distinguishes_names_from_roles():
    from prompts.system_prompt import _is_named_person

    assert _is_named_person("Kamala")
    assert _is_named_person("Mr. Iyer")
    assert _is_named_person("Vellai anna")
    assert not _is_named_person("Father")
    assert not _is_named_person("Paternal grandparents")
    assert not _is_named_person("Grandfather (name unknown)")
    assert not _is_named_person("my elder sister")
    assert not _is_named_person("")


def test_extraction_does_not_offer_bare_mention_as_a_significance_signal():
    """Layer 2 principle 6 defines significance as "unusual warmth,
    repetition, or emotional weight". The extraction prompt had drifted to
    also accept "unprompted mention" as a standalone signal — and in a
    reminiscence interview nearly every person is volunteered unprompted, so
    that qualified almost anyone. Live runs flagged a father and a set of
    grandparents whose stated justification was only that they came up."""
    prompt = build_extraction_prompt(
        _PROFILE, _SESSION, PriorContext(), "I had a sister.", "Tell me about her."
    )

    assert "significant_people" in prompt
    # The guidance must say mention alone is insufficient.
    lowered = prompt.lower()
    assert "not by itself a signal" in lowered
    assert "most turns should add nobody" in lowered


# ── pronouns (Layer 1) ───────────────────────────────────────────────────────


def _profile(
    pronouns: str = "", context: str = "Amma. Grew up in Thrissur."
) -> UserProfile:
    return UserProfile(
        name="Lakshmi",
        preferred_language="ml-IN",
        onboarding_context=context,
        pronouns=pronouns,
    )


def _pronoun_state(session_number: int, exchange_count: int) -> SessionState:
    return SessionState(
        session_id="s",
        user_id="u",
        session_number=session_number,
        domain="childhood",
        exchange_count=exchange_count,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )


_LATER_CONTEXT = PriorContext(
    facts={"birth_place": "Thrissur"}, open_threads=["the name of her school"]
)


def test_stated_pronouns_reach_the_prompt():
    prompt = build_system_prompt(
        _profile("she/her"), _pronoun_state(1, 0), PriorContext()
    )
    assert "she/her" in prompt


def test_pronouns_survive_once_facts_exist():
    """The actual bug. Pronouns written into the family's free-text note
    reached the model for exactly one turn, because Layer 3 renders that
    note only while nothing else is known. The moment the first fact or
    open thread was extracted it vanished for good, and Katha was left
    inferring gender — which it got wrong, every session, for a real
    person telling it her life story."""
    for state in (_pronoun_state(1, 2), _pronoun_state(7, 3), _pronoun_state(40, 1)):
        prompt = build_system_prompt(_profile("she/her"), state, _LATER_CONTEXT)
        assert "she/her" in prompt, f"lost by session {state.session_number}"


def test_pronouns_are_in_layer_1_not_layer_3():
    """Layer 3 is conditional on what Katha has learned; pronouns must not
    be. Keeping them in the persona is what makes the test above hold."""
    prompt = build_system_prompt(
        _profile("she/her"), _pronoun_state(7, 3), _LATER_CONTEXT
    )
    layer1 = prompt.split("LAYER 2")[0]
    assert "she/her" in layer1


def test_unstated_pronouns_tell_the_model_not_to_guess():
    """A blank is not an invitation to infer. The name is the signal the
    model would otherwise reach for, and it is the wrong one."""
    prompt = build_system_prompt(_profile(""), _pronoun_state(3, 2), _LATER_CONTEXT)
    lowered = prompt.lower()
    assert "not recorded" in lowered
    assert "never infer" in lowered
    assert "their name" in lowered


def test_free_text_pronouns_are_passed_through_verbatim():
    assert "she/they" in build_system_prompt(
        _profile("she/they"), _pronoun_state(1, 0), PriorContext()
    )


# ── the family's note (Layer 3) ──────────────────────────────────────────────


def test_the_family_note_survives_once_facts_exist():
    """Same defect, quieter half: everything the family took the trouble to
    write about their parent also lasted one turn."""
    for state in (_pronoun_state(1, 0), _pronoun_state(1, 2), _pronoun_state(12, 4)):
        prompt = build_system_prompt(_profile("she/her"), state, _LATER_CONTEXT)
        assert "Thrissur" in prompt, f"lost by session {state.session_number}"


def test_no_family_block_when_nothing_was_provided():
    prompt = build_system_prompt(
        _profile("she/her", context=""), _pronoun_state(5, 2), _LATER_CONTEXT
    )
    assert "family told us" not in prompt


# ── C2: compact, capped facts (docs/specs/COST_TO_SERVE.md §7) ────────────────


def _session_100_prior() -> PriorContext:
    """A synthetic fact store after a hundred sessions."""
    return PriorContext(
        facts={
            "people": [
                {"name": f"Person{i:02d}", "relationship": f"relation{i:02d}"}
                for i in range(70)
            ],
            "places": [f"Place{i:02d}" for i in range(50)],
            "dates": [f"19{i:02d}" for i in range(50)],
            "institutions": [f"Institution{i:02d}" for i in range(20)],
        },
        open_threads=[f"an open thread about subject number {i}" for i in range(40)],
        significant_people=[
            {
                "name": f"Significant{i}",
                "relationship": "teacher",
                "why_significant": "spoke of him with unusual warmth",
                "first_seen_session": 3,
            }
            for i in range(6)
        ],
    )


def test_facts_render_as_compact_lines_not_python_reprs():
    from prompts.system_prompt import render_facts

    lines = render_facts(
        {
            "people": [
                {"name": "Kamala", "relationship": "sister"},
                {"name": "Mr. Iyer", "relationship": "teacher"},
            ],
            "places": ["Madurai"],
            "birth_year": "1948",
        }
    )
    assert lines == [
        "People: Kamala (sister); Mr. Iyer (teacher)",
        "Places: Madurai",
        "Birth year: 1948",
    ]
    prompt = build_system_prompt(
        _PROFILE, _SESSION, PriorContext(facts={"people": [{"name": "Kamala"}]})
    )
    assert "People: Kamala" in prompt
    assert "{'name'" not in prompt


def test_facts_are_capped_per_category():
    from prompts.system_prompt import render_facts

    lines = dict(
        line.split(": ", 1) for line in render_facts(_session_100_prior().facts)
    )
    people = lines["People"].split("; ")
    # The first ten (closest family, mentioned first) and the latest five.
    assert people[:10] == [f"Person{i:02d} (relation{i:02d})" for i in range(10)]
    assert people[10:] == [f"Person{i:02d} (relation{i:02d})" for i in range(65, 70)]
    assert lines["Places"].split("; ") == [f"Place{i:02d}" for i in (0, 1, 2, 3, 4)] + [
        f"Place{i:02d}" for i in range(45, 50)
    ]
    assert lines["Dates"].split("; ") == [f"19{i:02d}" for i in range(42, 50)]
    assert lines["Institutions"].split("; ") == [
        f"Institution{i:02d}" for i in range(5)
    ]


def test_other_fact_keys_keep_at_most_five_values():
    from prompts.system_prompt import render_facts

    (line,) = render_facts({"hobbies": [f"hobby{i}" for i in range(9)]})
    assert line == "Hobbies: hobby0; hobby1; hobby2; hobby3; hobby4"


def test_facts_are_deduplicated_case_insensitively():
    from prompts.system_prompt import render_facts

    (line,) = render_facts({"places": ["Madurai", "madurai", " ", "Chennai"]})
    assert line == "Places: Madurai; Chennai"


def test_a_short_list_is_shown_whole():
    from prompts.system_prompt import render_facts

    people = [{"name": f"P{i}", "relationship": "cousin"} for i in range(15)]
    (line,) = render_facts({"people": people})
    assert line.count(";") == 14


def test_session_100_prompt_stays_under_8000_characters():
    """
    Before the caps the same fact store rendered a dialogue prompt of about
    18,300 characters, and it kept growing with every session.
    """
    session = SimpleNamespace(**{**vars(_SESSION), "session_number": 100})
    session.exchange_count = 3  # recall anchor and full Layer 4 in play
    prompt = build_system_prompt(_PROFILE, session, _session_100_prior())
    assert len(prompt) < 8000, len(prompt)


# ── C3: the prompt in a cacheable half and a per-turn half ────────────────────


def test_stable_and_per_turn_join_to_the_whole_prompt():
    from prompts.system_prompt import build_system_prompt_parts

    prior = _session_100_prior()
    session = SimpleNamespace(**{**vars(_SESSION), "exchange_count": 3})
    parts = build_system_prompt_parts(_PROFILE, session, prior)
    assert parts.joined() == build_system_prompt(_PROFILE, session, prior)
    # Layers 1-3 are cached; 4-5 change every turn and must come after.
    assert "LAYER 3" in parts.stable and "LAYER 4" not in parts.stable
    assert parts.per_turn.lstrip().startswith("LAYER 4")


def test_the_stable_half_does_not_change_between_turns_of_a_session():
    """What makes it cacheable: with Layer 3 frozen, only Layers 4-5 move."""
    from prompts.system_prompt import build_system_prompt_parts

    prior = _session_100_prior()
    turn_2 = SimpleNamespace(**{**vars(_SESSION), "exchange_count": 1})
    turn_5 = SimpleNamespace(
        **{**vars(_SESSION), "exchange_count": 4, "goal_met": True}
    )
    first = build_system_prompt_parts(_PROFILE, turn_2, prior)
    later = build_system_prompt_parts(_PROFILE, turn_5, prior)
    assert first.stable == later.stable
    assert first.per_turn != later.per_turn
