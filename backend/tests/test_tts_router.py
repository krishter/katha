"""
media/tts.py and the voice rules in media/voices.py
(docs/specs/GEMINI_TTS.md §4.2, §5). Every vendor call is mocked.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from media import tts, voices
from prompts.system_prompt import SUPPORTED_LANGUAGES


def profile(language="hi-IN", gender="female", pin=None):
    return SimpleNamespace(
        preferred_language=language, katha_gender=gender, tts_voice=pin
    )


@pytest.fixture
def vendors():
    sarvam = AsyncMock(return_value=b"SARVAM-WAV")
    gemini = AsyncMock(return_value=b"GEMINI-WAV")
    with (
        patch("adapters.sarvam_tts.synthesize", new=sarvam),
        patch("adapters.gemini_tts.synthesize", new=gemini),
    ):
        yield SimpleNamespace(sarvam=sarvam, gemini=gemini)


@pytest.fixture
def defaults(monkeypatch):
    """Production's defaults, whatever a developer's .env says."""
    monkeypatch.setattr("config.settings.TTS_VENDOR", "gemini")
    monkeypatch.setattr("config.settings.GEMINI_TTS_LANGUAGES", "all")
    monkeypatch.setattr("config.settings.TTS_MODEL", "bulbul:v4-flash")


# ── which voice ──────────────────────────────────────────────────────────────


def test_a_new_parent_gets_gemini_flash_lite_in_the_persona_voice(defaults):
    female = tts.default_voice("ml-IN", "female")
    male = tts.default_voice("hi-IN", "male")
    assert female.pin == "google/gemini-3.8-flash-lite-tts/Sulafat"
    assert male.pin == "google/gemini-3.8-flash-lite-tts/Achird"


def test_every_language_but_urdu_defaults_to_gemini(defaults):
    for language in SUPPORTED_LANGUAGES:
        for gender in voices.GENDERS:
            voice = tts.default_voice(language, gender)
            expected = voices.SARVAM if language == "ur-IN" else voices.GOOGLE
            assert voice.vendor == expected, (language, gender)


def test_urdu_keeps_todays_sarvam_voice(defaults):
    """D4: Gemini does not list Urdu, so it stays as it is."""
    assert tts.default_voice("ur-IN", "female") == voices.voice_for(
        "ur-IN", "female", "bulbul:v4-flash"
    )
    # Even if someone lists it.
    assert "ur-IN" not in voices.parse_languages("hi-IN,ur-IN")


def test_only_listed_languages_get_gemini(defaults, monkeypatch):
    monkeypatch.setattr("config.settings.GEMINI_TTS_LANGUAGES", "ta-IN, ml-IN")
    assert tts.default_voice("ta-IN", "female").vendor == voices.GOOGLE
    assert tts.default_voice("ml-IN", "male").vendor == voices.GOOGLE
    assert tts.default_voice("hi-IN", "female") == voices.voice_for(
        "hi-IN", "female", "bulbul:v4-flash"
    )


def test_tts_vendor_sarvam_is_todays_rule_for_every_voice(defaults, monkeypatch):
    monkeypatch.setattr("config.settings.TTS_VENDOR", "sarvam")
    for model in ("bulbul:v4-flash", "bulbul:v3"):
        monkeypatch.setattr("config.settings.TTS_MODEL", model)
        for language in SUPPORTED_LANGUAGES:
            for gender in voices.GENDERS:
                assert tts.default_voice(language, gender) == voices.voice_for(
                    language, gender, model
                )


def test_a_pinned_voice_wins_over_every_default(defaults, monkeypatch):
    pin = "sarvam/bulbul:v4-flash/ritu_hi_conversation"
    for vendor in ("gemini", "sarvam"):
        monkeypatch.setattr("config.settings.TTS_VENDOR", vendor)
        monkeypatch.setattr("config.settings.TTS_MODEL", "bulbul:v3")
        assert tts.voice_of(profile("hi-IN", "female", pin)).pin == pin

    gemini_pin = "google/gemini-3.8-flash-lite-tts/Achird"
    monkeypatch.setattr("config.settings.TTS_VENDOR", "sarvam")
    assert tts.voice_of(profile("ta-IN", "male", gemini_pin)).pin == gemini_pin


@pytest.mark.parametrize(
    "pin",
    [
        None,
        "",
        "garbage",
        "elevenlabs/v3/someone",
        # A Hindi speaker cannot say Tamil: the language changed since.
        "sarvam/bulbul:v4-flash/ritu_hi_conversation",
    ],
)
def test_an_unusable_pin_falls_back_to_the_default(defaults, pin):
    assert tts.voice_of(profile("ta-IN", "female", pin)) == tts.default_voice(
        "ta-IN", "female"
    )


def test_a_sarvam_pin_is_sent_with_sarvams_language_code():
    voice = voices.from_pin("sarvam/bulbul:v3/ritu", "or-IN")
    assert voice.language_code == "od-IN"


# ── speaking ─────────────────────────────────────────────────────────────────


async def test_a_gemini_parent_is_spoken_to_by_gemini(defaults, vendors):
    pin = "google/gemini-3.8-flash-lite-tts/Sulafat"
    wav = await tts.synthesize(
        "Namaste.", profile("ml-IN", "female", pin), purpose="tts_reply"
    )

    assert wav == b"GEMINI-WAV"
    vendors.sarvam.assert_not_awaited()
    call = vendors.gemini.await_args
    assert call.args == ("Namaste.", "ml-IN")
    assert call.kwargs == {
        "gender": "female",
        "purpose": "tts_reply",
        "model": "gemini-3.8-flash-lite-tts",
        "voice": "Sulafat",
    }


async def test_a_sarvam_parent_gets_exactly_todays_call(defaults, vendors):
    """TTS_VENDOR=sarvam, or a parent pinned before the switch: the call
    Sarvam receives is the one it received before media/tts.py existed."""
    voice = voices.voice_for("hi-IN", "female", "bulbul:v4-flash")
    await tts.synthesize(
        "Namaste.", profile("hi-IN", "female", voice.pin), purpose="tts_opener"
    )

    vendors.gemini.assert_not_awaited()
    call = vendors.sarvam.await_args
    assert call.args == ("Namaste.", "hi-IN")
    assert call.kwargs == {"gender": "female", "purpose": "tts_opener", "voice": voice}


async def test_sarvam_vendor_sends_byte_identical_requests(monkeypatch, usage_rows):
    """With TTS_VENDOR=sarvam a new parent's request body, and its ledger
    row, are what adapters/sarvam_tts.py sent before this change."""
    import base64
    import io
    import wave

    from adapters import sarvam_tts

    monkeypatch.setattr("config.settings.TTS_VENDOR", "sarvam")
    monkeypatch.setattr("config.settings.TTS_MODEL", "bulbul:v4-flash")

    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(22050)
        w.writeframes(b"\x10\x00" * 22050)
    response = SimpleNamespace(
        status_code=200,
        json=lambda: {"audios": [base64.b64encode(buffer.getvalue()).decode()]},
    )
    sent = []

    async def post(client, url, **kwargs):
        sent.append(kwargs["json"])
        return response

    with patch("adapters.sarvam_tts.post_with_retry", new=post):
        await tts.synthesize(
            "Namaste ji.", profile("hi-IN", "male"), purpose="tts_reply"
        )
        await sarvam_tts.synthesize(
            "Namaste ji.", "hi-IN", gender="male", purpose="tts_reply"
        )

    assert sent[0] == sent[1]
    assert sent[0]["speaker"] == "aditya_hi_conversational"
    assert usage_rows[0] == usage_rows[1]


async def test_gemini_failing_says_the_reply_in_the_persona_sarvam_voice(
    defaults, vendors
):
    """D3: a different voice beats none; the ledger marks it."""
    vendors.gemini.side_effect = RuntimeError("Gemini TTS error 503")
    pin = "google/gemini-3.8-flash-lite-tts/Achird"

    wav = await tts.synthesize(
        "Vanakkam.", profile("ta-IN", "male", pin), purpose="tts_reply"
    )

    assert wav == b"SARVAM-WAV"
    call = vendors.sarvam.await_args
    assert call.kwargs["purpose"] == "tts_fallback_vendor"
    assert call.kwargs["voice"] == voices.voice_for("ta-IN", "male", "bulbul:v4-flash")
    assert call.kwargs["gender"] == "male"


async def test_when_both_vendors_fail_the_caller_hears_about_it(defaults, vendors):
    """The caller then sends the reply as text, as it always has."""
    vendors.gemini.side_effect = RuntimeError("Gemini down")
    vendors.sarvam.side_effect = RuntimeError("Sarvam down")
    with pytest.raises(RuntimeError, match="Sarvam down"):
        await tts.synthesize("Hello.", profile("en-IN", "female"), purpose="tts_reply")


async def test_a_sarvam_failure_is_not_retried_on_gemini(defaults, vendors):
    vendors.sarvam.side_effect = RuntimeError("Sarvam down")
    pin = voices.voice_for("hi-IN", "female", "bulbul:v4-flash").pin
    with pytest.raises(RuntimeError):
        await tts.synthesize(
            "Hello.", profile("hi-IN", "female", pin), purpose="tts_reply"
        )
    vendors.gemini.assert_not_awaited()


async def test_speak_reports_the_voice_that_actually_spoke(defaults, vendors):
    gemini = voices.gemini_voice("ml-IN", "female")
    _, spoke = await tts.speak(
        "Hi.", gemini, language_code="ml-IN", gender="female", purpose="tts_system"
    )
    assert spoke == gemini

    vendors.gemini.side_effect = RuntimeError("down")
    _, spoke = await tts.speak(
        "Hi.", gemini, language_code="ml-IN", gender="female", purpose="tts_system"
    )
    assert spoke.vendor == voices.SARVAM


# ── every call site goes through media/tts.py ────────────────────────────────


def test_nothing_outside_media_tts_calls_a_vendor_adapter_directly():
    """§9: every synthesize call in backend/ goes through media/tts.py. The
    listening kit is the one deliberate exception: it compares vendors."""
    import pathlib
    import re

    backend = pathlib.Path(__file__).resolve().parents[1]
    allowed = {"media/tts.py", "scripts/listening_kit.py"}
    offenders = []
    for path in backend.rglob("*.py"):
        relative = path.relative_to(backend).as_posix()
        if (
            relative.startswith(("tests/", ".venv/", "adapters/"))
            or relative in allowed
        ):
            continue
        if re.search(r"(sarvam_tts|gemini_tts)\.synthesize\(", path.read_text()):
            offenders.append(relative)
    assert offenders == []
