from types import SimpleNamespace

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


def test_prompt_length_under_4500_chars():
    prompt = _build()
    assert len(prompt) < 4500, f"Prompt too long: {len(prompt)} chars"


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
