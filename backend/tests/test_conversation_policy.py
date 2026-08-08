from types import SimpleNamespace

from core.conversation_policy import (
    check_extraction_response,
    check_post_turn,
    check_pre_turn,
    check_response_for_crisis,
    find_crisis_keyword,
)

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

_WELL_FORMED = "<response>Good morning! Tell me about your childhood home.</response>"


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


def test_crisis_keyword_devanagari_triggers_detection():
    result = check_pre_turn("मुझे अब जीना नहीं चाहता", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False
    assert result.matched_pattern == "जीना नहीं चाहता"


def test_crisis_keyword_tamil_triggers_detection():
    result = check_pre_turn("எனக்கு இப்போது தற்கொலை பற்றி நினைவு வருகிறது", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False


def test_crisis_keyword_telugu_triggers_detection():
    result = check_pre_turn("నాకు ఆత్మహత్య గురించి ఆలోచనలు వస్తున్నాయి", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False


def test_crisis_keyword_bengali_triggers_detection():
    result = check_pre_turn("আমার আত্মহত্যা করার কথা মনে হচ্ছে", _SESSION)
    assert result.crisis_detected is True
    assert result.allowed is False


def test_find_crisis_keyword_returns_none_for_normal_text():
    assert find_crisis_keyword("I grew up near a river in Madurai.") is None


def test_find_crisis_keyword_returns_matched_pattern():
    assert find_crisis_keyword("I have been thinking about suicide") == "suicide"


# ── response crisis check (safety net on Katha's own output) ─────────────────


def test_check_response_for_crisis_blocks_self_harm_language():
    result = check_response_for_crisis(
        "Maybe you'd be better off dead if things are this hard."
    )
    assert result.allowed is False
    assert result.crisis_detected is True
    assert "9152987821" in result.override_response


def test_check_response_for_crisis_allows_normal_reply():
    result = check_response_for_crisis(
        "That sounds like a wonderful memory — tell me more."
    )
    assert result.allowed is True
    assert result.crisis_detected is False


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


def test_response_only_is_allowed():
    """The dialogue call returns <response> only now — no <extraction>
    is expected here (extraction is a separate call)."""
    result = check_post_turn("<response>Good morning!</response>", _SESSION)
    assert result.allowed is True
    assert result.override_response is None


def test_completely_malformed_response_is_blocked():
    result = check_post_turn("Just some plain text with no tags.", _SESSION)
    assert result.allowed is False


# ── extraction call: format validation ────────────────────────────────────────


def test_check_extraction_response_valid():
    valid = '<extraction>{"story_atoms": [], "energy_signal": "high"}</extraction>'
    assert check_extraction_response(valid) is True


def test_check_extraction_response_missing_tag():
    assert check_extraction_response("no tags here at all") is False


def test_check_extraction_response_malformed_json():
    malformed = "<extraction>{not valid json}</extraction>"
    assert check_extraction_response(malformed) is False
