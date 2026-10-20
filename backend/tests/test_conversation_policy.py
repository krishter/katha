from types import SimpleNamespace

from core.conversation_policy import (
    check_extraction_response,
    check_post_turn,
    check_pre_turn,
    check_response_for_crisis,
    find_crisis_keyword,
    salvage_untagged_response,
)
from speech import catalogue

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


def test_untagged_prose_is_salvaged_not_discarded():
    """The model drops the <response> wrapper on roughly half of short,
    low-engagement turns. The reply itself is fine, so sending it beats
    answering a disengaging user with "could you tell me that again?"."""
    bare = (
        "That's completely fine, sir. Even just a small memory is enough.\n\n"
        "Was there one student who has stayed in your mind all these years?"
    )
    result = check_post_turn(bare, _SESSION)
    assert result.allowed is True
    assert result.override_response is None
    assert result.salvaged_untagged is True


def test_well_formed_response_is_not_marked_salvaged():
    result = check_post_turn(_WELL_FORMED, _SESSION)
    assert result.salvaged_untagged is False


def test_truncated_response_tag_is_blocked():
    """An opening tag with no closing tag means max_tokens cut the reply
    off mid-sentence — that is not safe to send."""
    truncated = "<response>I remember you telling me about the shop, and the"
    result = check_post_turn(truncated, _SESSION)
    assert result.allowed is False
    assert result.override_response is not None


def test_bare_json_is_blocked():
    result = check_post_turn('{"story_atoms": []}', _SESSION)
    assert result.allowed is False


def test_empty_response_is_blocked():
    result = check_post_turn("   \n  ", _SESSION)
    assert result.allowed is False


def test_too_short_response_is_blocked():
    result = check_post_turn("Okay.", _SESSION)
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


# ── post-turn: reasoning blocks and truncation ────────────────────────────────

_THINKING = (
    "<thinking>\nThe user is sharing beautiful memories of their childhood in "
    "Madurai. Let me analyze what they've shared:\n\n1. Home in Madurai\n2. "
    "Temple bells at 4 AM\n\nLayer 4 says I must pivot to Mr. Iyer now.\n"
    "</thinking>\n\n"
)


def test_complete_reasoning_block_is_stripped_from_the_spoken_text():
    """The model thinks out loud before replying on roughly a third of long
    turns. The reply is fine; its preamble must never reach the parent."""
    result = check_post_turn(
        _THINKING + "<response>Woh gali kaisi dikhti thi?</response>", _SESSION
    )
    assert result.allowed is True
    assert result.response_text == "Woh gali kaisi dikhti thi?"
    assert "thinking" not in result.response_text
    assert "analyze" not in result.response_text


def test_truncated_reasoning_block_is_never_spoken():
    """Cut off inside the reasoning, there is no reply behind it — and the
    reasoning itself is Katha narrating her analysis of the user, in
    English. Before this it was salvaged as untagged prose and spoken."""
    cut_inside_thinking = _THINKING[:200]
    result = check_post_turn(cut_inside_thinking, _SESSION, truncated=True)
    assert result.allowed is False
    assert result.override_response is not None
    assert salvage_untagged_response(cut_inside_thinking, truncated=True) is None


def test_truncated_response_is_trimmed_to_its_last_sentence():
    """Two warm sentences and a question are a complete turn. Discarding
    them because max_tokens ate the third asks an 80-year-old to repeat a
    five-minute story."""
    truncated = (
        "<response>Kitna sundar yaad hai yeh. Mandir ki ghanti aur filter "
        "coffee ki mehak — main almost sun sakti hoon. Aapke pitaji ki dukaan "
        "mein kya"
    )
    result = check_post_turn(truncated, _SESSION, truncated=True)
    assert result.allowed is True
    assert result.salvaged_truncated is True
    assert result.response_text.endswith("main almost sun sakti hoon.")
    assert "Aapke pitaji" not in result.response_text
    assert "<response>" not in result.response_text


def test_truncated_devanagari_response_is_trimmed_at_the_danda():
    truncated = (
        "<response>कितनी सुंदर याद है यह। मंदिर की घंटी और फ़िल्टर कॉफ़ी की "
        "महक। आपके पिताजी की दुकान में क्या"
    )
    result = check_post_turn(truncated, _SESSION, truncated=True)
    assert result.allowed is True
    assert result.response_text.endswith("महक।")


def test_truncated_reply_with_no_sentence_boundary_is_blocked():
    """Cut off before the first sentence finished — nothing to recover."""
    result = check_post_turn(
        "<response>Kitna sundar yaad hai yeh aur mandir ki", _SESSION, truncated=True
    )
    assert result.allowed is False
    assert result.override_response is not None


def test_unclosed_response_tag_is_still_blocked_when_not_truncated():
    """Without stop_reason == max_tokens an unclosed tag is a malformed
    reply, not a cut-off one, so the old behaviour stands."""
    result = check_post_turn(
        "<response>I remember you telling me about the shop, and the", _SESSION
    )
    assert result.allowed is False


def test_well_formed_response_text_is_returned_for_the_caller():
    """process_voice_turn speaks response_text — a caller re-parsing the
    raw output itself would undo the stripping and trimming above."""
    result = check_post_turn("<response>Good morning!</response>", _SESSION)
    assert result.response_text == "Good morning!"
    assert result.salvaged_truncated is False


# ── her language (M-14) ──────────────────────────────────────────────────────


def test_crisis_override_is_in_her_language_and_keeps_icall():
    result = check_pre_turn("எனக்கு தற்கொலை எண்ணம் வருகிறது", _SESSION, language="ta-IN")
    assert result.crisis_detected
    assert result.override_response == catalogue.text("crisis", "ta-IN")
    assert "9152987821" in result.override_response


def test_crisis_override_defaults_to_english_without_a_parent():
    result = check_response_for_crisis("I want to end my life")
    assert result.override_response == catalogue.text("crisis", "en-IN")


def test_malformed_reply_apology_is_in_her_language_and_persona():
    result = check_post_turn("", _SESSION, language="hi-IN", gender="male")
    assert not result.allowed
    assert result.override_response == catalogue.text(
        "malformed", "hi-IN", gender="male"
    )
