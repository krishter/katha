from types import SimpleNamespace

from prompts.system_prompt import PriorContext, UserProfile, build_system_prompt

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


def test_prompt_contains_response_extraction_tags():
    prompt = _build()
    assert "<response>" in prompt
    assert "<extraction>" in prompt


def test_prompt_contains_icall_crisis_number():
    prompt = _build()
    assert "9152987821" in prompt


def test_prompt_length_under_4000_chars():
    prompt = _build()
    assert len(prompt) < 4000, f"Prompt too long: {len(prompt)} chars"


def test_prompt_contains_language_name_not_code():
    prompt = _build()
    # Should say "Tamil" not "ta-IN"
    assert "Tamil" in prompt
    assert "ta-IN" not in prompt
