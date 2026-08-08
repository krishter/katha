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


def test_prompt_forces_recall_when_context_known_and_exchange_count_high_enough():
    """
    Regression guard (WS5.4 eval finding): a soft "use what you know"
    principle in a 7-item list scored 0/3 on live proactive-recall runs.
    Layer 4 now carries a concrete, state-driven instruction instead.
    """
    prior = PriorContext(facts={"sister_name": "Kamala"})
    late_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 2})
    prompt = build_system_prompt(_PROFILE, late_session, prior)
    assert "have not yet referenced" in prompt.lower()


def test_prompt_omits_recall_forcing_when_nothing_known():
    late_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 2})
    prompt = build_system_prompt(_PROFILE, late_session, PriorContext())
    assert "have not yet referenced" not in prompt.lower()


def test_prompt_omits_recall_forcing_before_exchange_count_threshold():
    """Exchange 0-1 are naturally about the domain's own entry question —
    the forcing instruction should only kick in from exchange 2 onward."""
    prior = PriorContext(facts={"sister_name": "Kamala"})
    early_session = SimpleNamespace(**{**_SESSION.__dict__, "exchange_count": 1})
    prompt = build_system_prompt(_PROFILE, early_session, prior)
    assert "have not yet referenced" not in prompt.lower()
