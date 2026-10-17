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
"""

from __future__ import annotations

from dataclasses import dataclass

V3_MODEL = "bulbul:v3"
V4_FLASH_MODEL = "bulbul:v4-flash"

GENDERS = ("female", "male")
DEFAULT_GENDER = "female"

V3_VOICES: dict[str, str] = {"female": "ritu", "male": "shubh"}

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
    # The code Sarvam accepts for the language.
    language_code: str


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
