"""
Which Sarvam voice speaks each language (docs/specs/COST_TO_SERVE.md C6).

bulbul:v3 uses one short voice name for every language. bulbul:v4-flash
voices are per language and style ("<name>_<language>_<style>") and reject
v3's short names, so v4 needs this map.

Chosen from Sarvam's v4 Flash catalog
(https://docs.sarvam.ai/api/api-guides-tutorials/text-to-speech/voices,
checked 2026-10-16): a conversational style, and where the catalog has one,
the same speaker as v3's "ritu", so the voice a parent knows does not change
character. The catalog states no gender; the choices follow the speaker
names and must be confirmed by ear before a language is moved to v4.

Only Hindi and English were heard in the blind A/B of 2026-10-17 (v4 Flash
preferred in 19 of 20 pairs), so only they are in V4_FLASH_VOICES. The
other choices wait in V4_FLASH_CANDIDATES, unused, until someone has
listened to them; until then those languages stay on v3 with TTS_MODEL set
to v4 Flash.

Deliberately unmapped, so they stay on v3 and its "ritu":
- ta-IN and pa-IN: every v4 Flash voice for them carries a male speaker
  name, which would change the voice a parent knows.
- ml-IN and or-IN: not offered on v4 Flash yet.
- as-IN and ur-IN: not in the v4 Flash catalog.
"""

from __future__ import annotations

V3_MODEL = "bulbul:v3"
V4_FLASH_MODEL = "bulbul:v4-flash"
V3_VOICE = "ritu"

V4_FLASH_VOICES: dict[str, str] = {
    "hi-IN": "ritu_hi_conversation",
    "en-IN": "simran_en_conversation",
}

# Chosen but not yet heard. Move a language up into V4_FLASH_VOICES once
# it has been through the listening kit's abtest.
V4_FLASH_CANDIDATES: dict[str, str] = {
    "mr-IN": "ritu_mr_narration",
    "te-IN": "kavitha_te_conversation",
    "kn-IN": "chaitra_kn_conversation",
    "bn-IN": "roopa_bn_conversational",
    "gu-IN": "pooja_gu_conversational",
}


def voice_for(requested_model: str, language_code: str) -> tuple[str, str]:
    """
    (model, voice) to synthesise `language_code` with.

    A language with no v4 voice falls back to v3 for that call, so turning
    v4 Flash on can never leave a parent with no voice at all.
    """
    if requested_model == V4_FLASH_MODEL:
        voice = V4_FLASH_VOICES.get(language_code)
        if voice is not None:
            return V4_FLASH_MODEL, voice
    return V3_MODEL, V3_VOICE
