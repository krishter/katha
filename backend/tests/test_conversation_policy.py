from types import SimpleNamespace

from core.conversation_policy import check_post_turn, check_pre_turn

_SESSION = SimpleNamespace(
    session_id="s1",
    user_id="u1",
    session_number=1,
    domain="childhood",
    exchange_count=2,
    energy_signal="high",
    goal_met=False,
    session_end_suggested=False,
)

_WELL_FORMED = (
    "<response>Good morning! Tell me about your childhood home.</response>\n"
    '<extraction>{"story_atoms":[],"named_entities":{},"themes":[],'
    '"energy_signal":"high","gaps_remaining":[],"session_end_suggested":false}'
    "</extraction>"
)


# ── pre-turn: crisis detection ────────────────────────────────────────────────


def test_crisis_keyword_english_triggers_detection():
    result = check_pre_turn("I want to end my life", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False
    assert result.override_response is not None
    assert "9152987821" in result.override_response


def test_crisis_keyword_hindi_triggers_detection():
    result = check_pre_turn("mujhe jeena nahi chahta ab", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False


def test_crisis_keyword_suicide_triggers_detection():
    result = check_pre_turn("I have been thinking about suicide", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False


def test_normal_transcript_is_allowed():
    result = check_pre_turn(
        "I grew up in a small house near the river in Madurai.", _SESSION
    )
    assert result.allowed is True
    assert result.crisis_detected is False
    assert result.override_response is None


def test_empty_transcript_is_allowed():
    result = check_pre_turn("", _SESSION)
    assert result.allowed is True
    assert result.crisis_detected is False


# ── post-turn: format validation ──────────────────────────────────────────────


def test_well_formed_response_is_allowed():
    result = check_post_turn(_WELL_FORMED, _SESSION)
    assert result.allowed is True
    assert result.override_response is None


def test_missing_response_tag_is_blocked():
    malformed = (
        "Here is my answer without the proper tags.\n"
        '<extraction>{"story_atoms":[]}</extraction>'
    )
    result = check_post_turn(malformed, _SESSION)
    assert result.allowed is False
    assert result.override_response is not None


def test_missing_extraction_tag_is_blocked():
    malformed = "<response>Good morning!</response>\nNo extraction here."
    result = check_post_turn(malformed, _SESSION)
    assert result.allowed is False
    assert result.override_response is not None


def test_completely_malformed_response_is_blocked():
    result = check_post_turn("Just some plain text with no tags.", _SESSION)
    assert result.allowed is False
