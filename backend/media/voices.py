"""
Which Sarvam model and voice speak for Katha (docs/specs/COST_TO_SERVE.md C6).

The parent's profile language and Katha's persona (female or male, chosen by
the family at setup) decide both, once, for every voice note. Neither comes
from the language detected in a voice note: a parent who mixes Hindi and
English would otherwise hear a different speaker whenever a note was
detected as English.

The rule, from Sarvam's v4 Flash catalog
(https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/voices and
.../best-practices, checked 2026-10-17):

- v4 Flash, where it has a voice for that language and gender in a
  conversation style ("conversation" or "conversational").
- Otherwise v3: "ritu" for a female persona, "shubh" for a male one. That
  covers Malayalam and Odia (no v4 voices yet), Tamil (its only v4 voices
  are ads and narration), a male Telugu persona and a female Punjabi one.

Sarvam labels no voice's gender. The best-practices page groups some names
as female or male (simran; shubh, sunny, rohan, ratan); the rest follow the
speaker's name. Only the female Hindi and English voices have been heard
(blind A/B, 2026-10-17). Every other choice must be confirmed by ear with
the listening kit's `voices` subcommand before a family uses it.

Assamese and Urdu need beta access from Sarvam; until it is granted every
synthesis for them fails and the reply goes out as text.

TTS_MODEL=bulbul:v3 is the rollback: every persona then speaks on v3.

Gemini (docs/specs/GEMINI_TTS.md, decided 2026-10-18). With
TTS_VENDOR=gemini, a new parent whose language is in GEMINI_TTS_LANGUAGES
gets Gemini 3.8 Flash-Lite TTS in the persona's Gemini voice; every other
language keeps the Sarvam rule above. Urdu is not on Google's list, so it
stays with Sarvam (D4).

These rules are defaults for new families only. Each parent's voice is
pinned on user_profiles.tts_voice at setup, as "vendor/model/speaker", and
media/tts.py speaks with the pin. Changing a setting or this table never
changes the voice an existing parent hears; scripts/set_voice.py moves a
parent deliberately and records who did it.
"""

from __future__ import annotations

from dataclasses import dataclass

V3_MODEL = "bulbul:v3"
V4_FLASH_MODEL = "bulbul:v4-flash"
# D1: Flash-Lite, after a blind comparison with Flash (2026-10-18).
GEMINI_TTS_MODEL = "gemini-3.8-flash-lite-tts"

SARVAM = "sarvam"
GOOGLE = "google"

GENDERS = ("female", "male")
DEFAULT_GENDER = "female"

V3_VOICES: dict[str, str] = {"female": "ritu", "male": "shubh"}

# One Gemini prebuilt voice per persona, the same in every language.
# Gender from Google Cloud's Chirp 3 HD list, which shares these names
# (https://docs.cloud.google.com/text-to-speech/docs/chirp3-hd); the
# descriptor in brackets is the Gemini docs' own.
# - Sulafat (Warm): the only voice of the 30 described as warm.
# - Achird (Friendly): no male voice is described as warm or gentle;
#   friendly is the nearest, and avoids the Firm, Excitable and Upbeat ones.
GEMINI_VOICES: dict[str, str] = {"female": "Sulafat", "male": "Achird"}

# Gemini 3.8 TTS's languages, of Katha's 13: every one but Urdu.
GEMINI_SUPPORTED_LANGUAGES: frozenset[str] = frozenset(
    {
        "hi-IN", "en-IN", "ta-IN", "te-IN", "kn-IN", "ml-IN", "mr-IN",
        "bn-IN", "gu-IN", "pa-IN", "or-IN", "as-IN",
    }
)  # fmt: skip

# (language, gender) -> v4 Flash conversation-style voice. A pair missing
# here is spoken on v3.
V4_FLASH_VOICES: dict[tuple[str, str], str] = {
    ("hi-IN", "female"): "ritu_hi_conversation",
    ("hi-IN", "male"): "aditya_hi_conversational",
    ("en-IN", "female"): "simran_en_conversation",
    ("en-IN", "male"): "dev_en_conversational",
    ("mr-IN", "female"): "ishita_mr_conversational",
    ("mr-IN", "male"): "nilesh_mr_conversation",
    ("kn-IN", "female"): "chaitra_kn_conversation",
    ("kn-IN", "male"): "chetan_kn_conversation",
    ("te-IN", "female"): "kavitha_te_conversation",
    ("gu-IN", "female"): "pooja_gu_conversational",
    ("gu-IN", "male"): "bhavik_gu_conversation",
    ("bn-IN", "female"): "roopa_bn_conversational",
    ("bn-IN", "male"): "arnab_bn_conversation",
    ("pa-IN", "male"): "anand_pa_conversation",
}

# Heard by a person and judged right for the persona. Everything else in
# V4_FLASH_VOICES is the catalog's best match on paper.
HEARD: set[tuple[str, str]] = {("hi-IN", "female"), ("en-IN", "female")}

# Katha's language codes that Sarvam spells differently. Odia is "or-IN"
# everywhere in Katha and "od-IN" at Sarvam, which rejects "or-IN".
_SARVAM_LANGUAGE_CODES: dict[str, str] = {"or-IN": "od-IN"}


@dataclass(frozen=True)
class Voice:
    model: str
    speaker: str
    # The code the vendor accepts for the language: Sarvam's spelling for
    # Sarvam ("od-IN"), Katha's own for Google, which takes none and only
    # uses it for the style hint.
    language_code: str
    vendor: str = SARVAM

    @property
    def pin(self) -> str:
        """How the voice is stored on user_profiles.tts_voice."""
        return f"{self.vendor}/{self.model}/{self.speaker}"


def normalise_gender(gender: str | None) -> str:
    """'female' or 'male'; anything else, including empty, is the default."""
    return gender if gender in GENDERS else DEFAULT_GENDER


def sarvam_language_code(language_code: str) -> str:
    return _SARVAM_LANGUAGE_CODES.get(language_code, language_code)


def v3_voice(language_code: str, gender: str | None) -> Voice:
    return Voice(
        V3_MODEL,
        V3_VOICES[normalise_gender(gender)],
        sarvam_language_code(language_code),
    )


def voice_for(
    language_code: str, gender: str | None, tts_model: str = V4_FLASH_MODEL
) -> Voice:
    """
    The model and voice for a persona speaking `language_code`.

    `tts_model` is settings.TTS_MODEL: v4 Flash applies the table above,
    and v3 puts every persona on v3.
    """
    gender = normalise_gender(gender)
    if tts_model == V4_FLASH_MODEL:
        speaker = V4_FLASH_VOICES.get((language_code, gender))
        if speaker is not None:
            return Voice(V4_FLASH_MODEL, speaker, sarvam_language_code(language_code))
    return v3_voice(language_code, gender)


def gemini_voice(language_code: str, gender: str | None) -> Voice:
    return Voice(
        GEMINI_TTS_MODEL,
        GEMINI_VOICES[normalise_gender(gender)],
        language_code,
        vendor=GOOGLE,
    )


def parse_languages(value: str) -> frozenset[str]:
    """GEMINI_TTS_LANGUAGES ("hi-IN,ta-IN" or "all") as a set of codes.
    Codes Gemini does not speak are dropped, so Urdu can never be listed."""
    if value.strip().lower() == "all":
        return GEMINI_SUPPORTED_LANGUAGES
    codes = {code.strip() for code in value.split(",") if code.strip()}
    return frozenset(codes & GEMINI_SUPPORTED_LANGUAGES)


def default_voice(
    language_code: str,
    gender: str | None,
    *,
    tts_vendor: str,
    tts_model: str,
    gemini_languages: frozenset[str],
) -> Voice:
    """
    The voice a NEW parent gets, from the settings of the day: Gemini when
    TTS_VENDOR is gemini and the language is switched, else the Sarvam rule.
    """
    if tts_vendor == "gemini" and language_code in gemini_languages:
        return gemini_voice(language_code, gender)
    return voice_for(language_code, gender, tts_model)


def from_pin(pin: str | None, language_code: str) -> Voice | None:
    """
    The pinned voice for a parent speaking `language_code`, or None if the
    pin is empty or unreadable (the caller then uses the default).

    A Sarvam pin names a voice for one language. If the parent's language
    has changed since, that speaker cannot say the new one, so the pin is
    ignored rather than sending Sarvam a request it rejects.
    """
    if not pin:
        return None
    parts = pin.split("/")
    if len(parts) != 3 or not all(parts):
        return None
    vendor, model, speaker = parts
    if vendor == GOOGLE:
        if language_code not in GEMINI_SUPPORTED_LANGUAGES:
            return None
        return Voice(model, speaker, language_code, vendor=GOOGLE)
    if vendor != SARVAM:
        return None
    if model == V4_FLASH_MODEL and speaker not in {
        name
        for (language, _), name in V4_FLASH_VOICES.items()
        if language == language_code
    }:
        return None
    return Voice(model, speaker, sarvam_language_code(language_code))


def sarvam_counterpart(language_code: str, gender: str | None, tts_model: str) -> Voice:
    """The Sarvam voice for the persona: where a Gemini reply goes when
    Google fails (D3), and what a parent is moved back to on rollback."""
    return voice_for(language_code, gender, tts_model)
