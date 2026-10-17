#!/usr/bin/env python
"""
Operator listening kit for the human checks in docs/specs/COST_TO_SERVE.md.

Five subcommands, one per check the eval harness cannot do:

    # §8: hear how the dialogue model sounds in a parent's language
    python scripts/listening_kit.py replies --lang hi-IN --n 10 --out ~/kit/hi

    # §10: blind A/B of Bulbul v3 against v4 Flash on identical text
    python scripts/listening_kit.py abtest --langs hi-IN,en-IN --out ~/kit/ab

    # docs/proposals/tts-strategy.md §5: production Sarvam voice against
    # Gemini 3.8 Flash TTS (needs GEMINI_API_KEY), then tally the scores
    python scripts/listening_kit.py abtest --compare sarvam-gemini \\
        --langs ta-IN,ml-IN,or-IN --out ~/kit/ab-gemini
    python scripts/listening_kit.py abscore --in ~/kit/ab-gemini

    # §10: one clip per language and persona, to confirm each mapped voice
    # sounds like the persona it was chosen for
    python scripts/listening_kit.py voices --out ~/kit/voices

    # §11 (C7): does shortening pauses lose any words?
    python scripts/listening_kit.py pauses --in ~/self-trial-notes --out ~/kit/p

Everything is written to --out, which must be outside the repository: the
output is audio and transcripts, and none of it belongs in git. Nothing is
written to the database except the usage ledger.

The calls are real and billed. They go through the production adapters, so
they are recorded in api_usage like any other call, with purpose "eval" and
under the user id "eval-listening-kit" — add that to
METRICS_EXCLUDED_USER_IDS if the kit is ever run against the production
database, so it stays out of the pilot's numbers.

Run from backend/, with backend/.env holding real ANTHROPIC_API_KEY and
SARVAM_API_KEY. `pauses` also needs ffmpeg.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import difflib
import json
import pathlib
import random
import shutil
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from adapters import gemini_tts, llm, sarvam_stt, sarvam_tts  # noqa: E402
from adapters.llm import Message  # noqa: E402
from config import settings  # noqa: E402
from core import conversation_policy, orchestrator, usage  # noqa: E402
from core.session_manager import SessionState  # noqa: E402
from media import audio_convert, audio_split, voices  # noqa: E402
from prompts.system_prompt import (  # noqa: E402
    SUPPORTED_LANGUAGES,
    PriorContext,
    UserProfile,
    _language_name,
    build_system_prompt_parts,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LEDGER_USER = "eval-listening-kit"
PURPOSE = "eval"
GLOSS_MODEL = "claude-haiku-5-5"
# Haiku 5.5 thinks before answering; at 400 tokens it once spent the whole
# budget thinking and returned no translation at all.
GLOSS_MAX_TOKENS = 2000
AUDIO_SUFFIXES = {".ogg", ".opus", ".oga", ".m4a", ".mp3", ".wav", ".aac", ".amr"}


@dataclass(frozen=True)
class Sample:
    domain: str
    transcript: str
    note: str  # what this sample is for, shown to reviewers


# Ten realistic parent voice-note transcripts per language. Different life
# domains, two short or tired replies, and one that mentions Kamala, who is
# in the synthetic profile's fact store, so recall can be heard too.
SAMPLES: dict[str, list[Sample]] = {
    "hi-IN": [
        Sample(
            "childhood",
            "हमारा घर नदी के किनारे था, बहुत छोटा सा। सुबह-सुबह माँ आँगन में "
            "तुलसी को पानी देती थीं और हम बच्चे नंगे पाँव भागते रहते थे। "
            "बारिश में छत टपकती थी, तो हम बाल्टियाँ लगा देते थे।",
            "childhood home",
        ),
        Sample(
            "family_ancestors",
            "मेरे दादाजी पोस्ट ऑफ़िस में थे, बहुत सख़्त आदमी। लेकिन शाम को वो "
            "हमें रामायण की कहानियाँ सुनाते थे, आवाज़ बदल-बदल कर।",
            "grandparent",
        ),
        Sample(
            "education",
            "स्कूल तीन किलोमीटर दूर था, हम पैदल जाते थे, रास्ते में आम के पेड़ "
            "थे। हमारी हेडमिस्ट्रेस से सब डरते थे, but she was very fair, you know.",
            "school, code-mixed",
        ),
        Sample(
            "career",
            "मेरी पहली नौकरी उन्नीस सौ बहत्तर में लगी, स्कूल में पढ़ाने की। पहली "
            "तनख़्वाह एक सौ अस्सी रुपये थी, सारी माँ के हाथ में रख दी।",
            "first job",
        ),
        Sample(
            "love_marriage",
            "हमारी शादी घर वालों ने तय की थी। उनको पहली बार शादी के दिन ही ठीक से "
            "देखा। वो बहुत चुप-चुप थे, पर बाद में पता चला बड़े मज़ाकिया हैं।",
            "marriage",
        ),
        Sample(
            "historical_events",
            "इमरजेंसी के टाइम पर हम दिल्ली में थे। रात को रेडियो पर ख़बरें बहुत "
            "धीमी आवाज़ में सुनते थे, कि पड़ोसी न सुन लें।",
            "historical event",
        ),
        Sample(
            "hobbies",
            "मुझे सिलाई का बहुत शौक़ था। पूरे मोहल्ले की लड़कियों के ब्लाउज़ मैं "
            "ही सिलती थी, उषा की मशीन पर।",
            "hobby",
        ),
        Sample(
            "childhood",
            "कमला दीदी कल फ़ोन पर बात कर रही थीं, तो बचपन की वो होली याद आ गई "
            "जब हमने पूरे मोहल्ले को रंग दिया था।",
            "mentions a known person (Kamala, sister)",
        ),
        Sample("wisdom", "आज थोड़ी थकान है।", "short, tired"),
        Sample("wisdom", "हाँ... ठीक है, बस ऐसे ही।", "short, low energy"),
    ],
    "ta-IN": [
        Sample(
            "childhood",
            "எங்க வீடு கோயில் தெருவுல இருந்தது. காலையில அம்மா வாசல்ல கோலம் "
            "போடுவாங்க, நாங்க பக்கத்துல உட்கார்ந்து பார்த்துக்கிட்டே இருப்போம்.",
            "childhood home",
        ),
        Sample(
            "family_ancestors",
            "என் தாத்தா ஒரு விவசாயி. வயல்ல வேலை முடிஞ்சதும் சாயங்காலம் எங்களுக்குக் கதை சொல்லுவாரு.",
            "grandparent",
        ),
        Sample(
            "education",
            "நான் படிச்ச பள்ளிக்கூடம் ஊருக்கு வெளியே இருந்தது. மழைக்காலத்துல "
            "ஆத்தைத் தாண்டித்தான் போகணும்.",
            "school",
        ),
        Sample(
            "career",
            "என் முதல் வேலை ஒரு பள்ளிக்கூடத்துல டீச்சரா, ஆயிரத்து தொள்ளாயிரத்து "
            "எழுபத்தஞ்சுல. முதல் சம்பளத்துல அப்பாவுக்கு ஒரு கடிகாரம் வாங்கிக் "
            "கொடுத்தேன்.",
            "first job",
        ),
        Sample(
            "love_marriage",
            "எங்க கல்யாணம் மதுரையில நடந்தது. அவரை முதல் தடவை பொண்ணு பார்க்க வந்தப்போதான் பார்த்தேன்.",
            "marriage",
        ),
        Sample(
            "historical_events",
            "சுனாமி வந்த அன்னிக்கு நாங்க சென்னையில இருந்தோம். எல்லாரும் "
            "கடற்கரைப் பக்கம் இருந்து ஓடி வந்தாங்க.",
            "historical event",
        ),
        Sample(
            "hobbies",
            "எனக்கு கர்நாடக சங்கீதம் ரொம்பப் பிடிக்கும். சின்ன வயசுல வீணை கத்துக்கிட்டேன்.",
            "hobby",
        ),
        Sample(
            "childhood",
            "கமலா அக்கா நேத்து வந்திருந்தாங்க. நாங்க ரெண்டு பேரும் சின்ன "
            "வயசுல மாங்கா பறிச்சது ஞாபகம் வந்துச்சு.",
            "mentions a known person (Kamala, sister)",
        ),
        Sample("wisdom", "இன்னைக்கு கொஞ்சம் சோர்வா இருக்கு.", "short, tired"),
        Sample("wisdom", "சரி... அவ்வளவுதான்.", "short, low energy"),
    ],
    # ml-IN and or-IN: drafted by Claude on 2026-10-17, not yet checked by a
    # native speaker (see NEEDS_NATIVE_CHECK). Synthetic, like every sample
    # here: no real parent's words, so they are safe to send to any vendor.
    "ml-IN": [
        Sample(
            "childhood",
            "ഞങ്ങളുടെ വീട് പുഴയുടെ അടുത്തായിരുന്നു. രാവിലെ അമ്മ മുറ്റം "
            "അടിച്ചുവാരും, ഞങ്ങൾ കുട്ടികൾ പുഴയിൽ കുളിക്കാൻ ഓടും.",
            "childhood home",
        ),
        Sample(
            "family_ancestors",
            "എന്റെ അപ്പൂപ്പൻ ഒരു കർഷകനായിരുന്നു. വൈകുന്നേരം ഉമ്മറത്തിരുന്ന് "
            "ഞങ്ങൾക്ക് രാമായണം വായിച്ചു തരുമായിരുന്നു.",
            "grandparent",
        ),
        Sample(
            "education",
            "ഞാൻ പഠിച്ച സ്കൂൾ മൂന്നു കിലോമീറ്റർ ദൂരെയായിരുന്നു. മഴക്കാലത്ത് "
            "വഴി മുഴുവൻ വെള്ളമായിരിക്കും, എന്നാലും ഞങ്ങൾ നടന്നുതന്നെ പോകും.",
            "school",
        ),
        Sample(
            "career",
            "എന്റെ ആദ്യത്തെ ജോലി ഒരു സ്കൂളിൽ ടീച്ചറായിട്ടായിരുന്നു, ആയിരത്തി "
            "തൊള്ളായിരത്തി എഴുപത്തിരണ്ടിൽ. ആദ്യത്തെ ശമ്പളം മുഴുവൻ അമ്മയുടെ "
            "കയ്യിൽ കൊടുത്തു.",
            "first job",
        ),
        Sample(
            "love_marriage",
            "ഞങ്ങളുടെ കല്യാണം വീട്ടുകാർ ഉറപ്പിച്ചതായിരുന്നു. പെണ്ണുകാണലിന് "
            "വന്നപ്പോഴാണ് അദ്ദേഹത്തെ ആദ്യമായി കണ്ടത്.",
            "marriage",
        ),
        Sample(
            "historical_events",
            "രണ്ടായിരത്തി പതിനെട്ടിലെ വെള്ളപ്പൊക്കത്തിൽ ഞങ്ങൾ ഒരാഴ്ച "
            "ക്യാമ്പിലായിരുന്നു. അയൽക്കാരെല്ലാം ഒരുമിച്ചാണ് കഴിഞ്ഞത്.",
            "historical event",
        ),
        Sample(
            "hobbies",
            "എനിക്ക് പാട്ട് വലിയ ഇഷ്ടമായിരുന്നു. ചെറുപ്പത്തിൽ അമ്പലത്തിൽ ഭജനയ്ക്ക് ഞാനായിരുന്നു പാടിയിരുന്നത്.",
            "hobby",
        ),
        Sample(
            "childhood",
            "കമലേച്ചി ഇന്നലെ വിളിച്ചിരുന്നു. ചെറുപ്പത്തിൽ മാങ്ങ പറിക്കാൻ ഞങ്ങൾ "
            "മരത്തിൽ കയറിയതൊക്കെ ഓർത്ത് ഞങ്ങൾ ചിരിച്ചു.",
            "mentions a known person (Kamala, sister)",
        ),
        Sample("wisdom", "ഇന്ന് കുറച്ച് ക്ഷീണമുണ്ട്.", "short, tired"),
        Sample("wisdom", "ഉം... ശരി, അത്രയൊക്കെയേ ഉള്ളൂ.", "short, low energy"),
    ],
    "or-IN": [
        Sample(
            "childhood",
            "ଆମ ଘର ନଦୀ କୂଳରେ ଥିଲା। ସକାଳୁ ମା' ଅଗଣାରେ ଝୋଟି ପକାଉଥିଲେ, ଆମେ ପିଲାମାନେ ପାଖରେ ବସି ଦେଖୁଥିଲୁ।",
            "childhood home",
        ),
        Sample(
            "family_ancestors",
            "ମୋ ଜେଜେବାପା ଜଣେ ଚାଷୀ ଥିଲେ। ସଞ୍ଜବେଳେ ସେ ଆମକୁ ଭାଗବତ ପଢ଼ି ଶୁଣାଉଥିଲେ।",
            "grandparent",
        ),
        Sample(
            "education",
            "ମୁଁ ଯେଉଁ ସ୍କୁଲରେ ପଢ଼ୁଥିଲି ତାହା ଗାଁ ବାହାରେ ଥିଲା। ବର୍ଷାଦିନେ ନାଳ ପାର ହୋଇ ଯିବାକୁ ପଡୁଥିଲା।",
            "school",
        ),
        Sample(
            "career",
            "ମୋର ପ୍ରଥମ ଚାକିରି ଉଣେଇଶି ଶହ ବାସ୍ତରୀରେ, ଗୋଟିଏ ସ୍କୁଲରେ ଶିକ୍ଷୟିତ୍ରୀ "
            "ଭାବେ। ପ୍ରଥମ ଦରମା ସବୁ ମା'ଙ୍କ ହାତରେ ଦେଇଥିଲି।",
            "first job",
        ),
        Sample(
            "love_marriage",
            "ଆମ ବାହାଘର ଘର ଲୋକେ ଠିକ୍ କରିଥିଲେ। ତାଙ୍କୁ ପ୍ରଥମ ଥର ବାହାଘର ଦିନ ହିଁ ଭଲ ଭାବେ ଦେଖିଥିଲି।",
            "marriage",
        ),
        Sample(
            "historical_events",
            "ଉଣେଇଶି ଶହ ଅନେଶତ ମହାବାତ୍ୟା ବେଳେ ଆମେ କଟକରେ ଥିଲୁ। ଅନେକ ଦିନ ଯାଏଁ "
            "ବିଜୁଳି ନ ଥିଲା, ପଡ଼ିଶାମାନେ ସମସ୍ତେ ଗୋଟିଏ ଘରେ ରହିଥିଲୁ।",
            "historical event",
        ),
        Sample(
            "hobbies",
            "ମୋତେ ଭଜନ ଗାଇବାକୁ ବହୁତ ଭଲ ଲାଗୁଥିଲା। ଛୋଟବେଳେ ମନ୍ଦିରରେ ମୁଁ ହିଁ ଗାଉଥିଲି।",
            "hobby",
        ),
        Sample(
            "childhood",
            "କମଳା ଅପା କାଲି ଫୋନ୍ କରିଥିଲେ। ଛୋଟବେଳେ ଆମ୍ବ ତୋଳିବାକୁ ଗଛରେ ଚଢ଼ିଥିବା କଥା ମନେ ପକାଇ ଆମେ ଦୁହେଁ ହସିଲୁ।",
            "mentions a known person (Kamala, sister)",
        ),
        Sample("wisdom", "ଆଜି ଟିକିଏ କ୍ଲାନ୍ତ ଲାଗୁଛି।", "short, tired"),
        Sample("wisdom", "ହଁ... ଠିକ୍ ଅଛି, ଏତିକି।", "short, low energy"),
    ],
    "en-IN": [
        Sample(
            "childhood",
            "Our house was near the railway line. Every evening the Madras mail "
            "would pass and all of us children would run out to wave at it.",
            "childhood home",
        ),
        Sample(
            "family_ancestors",
            "My grandmother had a big brass vessel, and every Sunday she made "
            "rasam in it. The whole street could smell it.",
            "grandparent",
        ),
        Sample(
            "education",
            "I studied in a convent school. Sister Mary taught us English, and "
            "she was very strict about handwriting.",
            "school",
        ),
        Sample(
            "career",
            "I started teaching in 1972. The first salary was one hundred and "
            "eighty rupees, and I gave all of it to my mother.",
            "first job",
        ),
        Sample(
            "love_marriage",
            "We met at my cousin's wedding, actually. He was helping with the "
            "pandal and dropped a whole tray of laddoos.",
            "marriage",
        ),
        Sample(
            "historical_events",
            "When the news came on the radio in 1971, the whole colony gathered "
            "in one house to listen.",
            "historical event",
        ),
        Sample(
            "hobbies",
            "I used to grow roses on the terrace. At one time I had twenty-two "
            "pots, all different colours.",
            "hobby",
        ),
        Sample(
            "childhood",
            "Kamala called me yesterday, my sister. We laughed about the time we "
            "hid from the tailor.",
            "mentions a known person (Kamala, sister)",
        ),
        Sample("wisdom", "I'm a bit tired today.", "short, tired"),
        Sample("wisdom", "Yes, that's all really.", "short, low energy"),
    ],
}

_PROFILES = {
    "hi-IN": "Savitri",
    "ta-IN": "Lakshmi",
    "en-IN": "Lakshmi",
    "ml-IN": "Saraswathy",
    "or-IN": "Sabitri",
}

# Sample sets drafted by Claude and not yet read by a native speaker. Every
# sheet the kit writes for these languages says so at the top.
NEEDS_NATIVE_CHECK = {"ml-IN", "or-IN"}


def _profile(language: str, gender: str = voices.DEFAULT_GENDER) -> UserProfile:
    return UserProfile(
        name=_PROFILES.get(language, "Lakshmi"),
        preferred_language=language,
        onboarding_context="Retired schoolteacher. Grew up in a small town.",
        pronouns="she/her",
        katha_gender=gender,
    )


# A plausible session-5 Layer 3: a few facts and one known person, so the
# prompt is the size and shape a parent four sessions in would actually get.
_PRIOR = PriorContext(
    facts={
        "people": [
            {"name": "Kamala", "relationship": "elder sister"},
            {"name": "Raghu", "relationship": "husband"},
        ],
        "places": ["Madurai", "Delhi"],
        "dates": ["1972"],
        "institutions": ["Government Girls' School"],
    },
    open_threads=["what her mother cooked on festival days"],
    significant_people=[],
)


def _state(domain: str, index: int) -> SessionState:
    return SessionState(
        session_id=f"listening-kit-{index}",
        user_id=LEDGER_USER,
        session_number=5,
        domain=domain,
        # Mid-conversation: past the domain's entry question, before the
        # recall pivot, so replies are ordinary follow-ups.
        exchange_count=1,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=False,
    )


# ── shared ───────────────────────────────────────────────────────────────────


def ensure_outside_repo(path: pathlib.Path) -> pathlib.Path:
    """Refuse an output folder inside the repository: no audio in git, ever."""
    resolved = path.expanduser().resolve()
    if resolved == REPO_ROOT or REPO_ROOT in resolved.parents:
        raise SystemExit(
            f"--out must be outside the repository ({REPO_ROOT}); got {resolved}"
        )
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


def require_ffmpeg() -> None:
    """
    Every subcommand needs ffmpeg: replies are converted to the OGG/Opus
    parents actually receive, and pauses runs compress_pauses. Checked
    before the first billed call, not discovered after it.
    """
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if missing:
        raise SystemExit(
            f"{' and '.join(missing)} not found. Install it first "
            "(macOS: brew install ffmpeg); nothing has been called or billed."
        )


def _samples(language: str, n: int) -> list[Sample]:
    if language not in SAMPLES:
        raise SystemExit(f"No samples for {language}; have {', '.join(SAMPLES)}")
    if not 1 <= n <= len(SAMPLES[language]):
        raise SystemExit(f"--n must be 1..{len(SAMPLES[language])}")
    return SAMPLES[language][:n]


async def dialogue_reply(
    language: str, sample: Sample, index: int, gender: str = voices.DEFAULT_GENDER
) -> str:
    """The reply production would give: real prompt, model and policy."""
    state = _state(sample.domain, index)
    parts = build_system_prompt_parts(_profile(language, gender), state, _PRIOR)
    response = await llm.chat(
        [Message(role="user", content=sample.transcript)],
        system=parts.per_turn,
        cache_system_prefix=parts.stable,
        max_tokens=orchestrator._DIALOGUE_MAX_TOKENS,
        model=settings.LLM_DIALOGUE_MODEL,
        effort=settings.LLM_DIALOGUE_EFFORT,
        purpose=PURPOSE,
    )
    checked = conversation_policy.check_post_turn(
        response.content, state, truncated=response.stop_reason == "max_tokens"
    )
    return checked.response_text or checked.override_response or ""


async def english_gloss(text: str, language: str) -> str:
    if language == "en-IN":
        return text
    response = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    "Translate this reply into plain English for a reviewer who "
                    "does not read the script. Keep its tone and register; do not "
                    "add commentary. Return only the translation.\n\n" + text
                ),
            )
        ],
        max_tokens=GLOSS_MAX_TOKENS,
        model=GLOSS_MODEL,
        effort="low",
        purpose=PURPOSE,
    )
    return response.content.strip()


async def speak(
    text: str, language: str, gender: str, model: str | None = None
) -> bytes:
    wav = await sarvam_tts.synthesize(
        text, language, gender=gender, purpose=PURPOSE, model=model
    )
    return await audio_convert.convert_wav_to_ogg(wav)


# ── replies ──────────────────────────────────────────────────────────────────


async def cmd_replies(language: str, gender: str, n: int, out: pathlib.Path) -> None:
    out = ensure_outside_repo(out)
    voice = voices.voice_for(language, gender, settings.TTS_MODEL)
    lines = [
        f"# Listening check: {language}, {gender} Katha",
        "",
        f"Dialogue model `{settings.LLM_DIALOGUE_MODEL}` at effort "
        f"`{settings.LLM_DIALOGUE_EFFORT}`; speech `{voice.model}` voice "
        f"`{voice.speaker}`. Session 5, synthetic profile (she/her).",
        "",
        "Listen for warmth and register, not just correctness "
        "(docs/specs/COST_TO_SERVE.md §8).",
        "",
    ]
    for i, sample in enumerate(_samples(language, n), start=1):
        reply = await dialogue_reply(language, sample, i, gender)
        gloss = await english_gloss(reply, language)
        (out / f"{i:02d}.ogg").write_bytes(await speak(reply, language, gender))
        lines += [
            f"## {i:02d} · {sample.domain} · {sample.note}",
            "",
            f"**Parent said:** {sample.transcript}",
            "",
            f"**Katha replied** (`{i:02d}.ogg`): {reply}",
            "",
            f"**English gloss:** {gloss}",
            "",
        ]
        print(f"{i:02d} done")
    (out / "replies.md").write_text("\n".join(lines), encoding="utf-8")
    print(f"Wrote {n} replies to {out}")


# ── abtest ───────────────────────────────────────────────────────────────────


COMPARISONS = ("v3-v4", "sarvam-gemini")

SCORE_COLUMNS = [
    "pair",
    "listener",
    "listener age",
    "preferred (A/B/same)",
    "understood every word (y/n)",
    "clarity note",
]

# Both clips of a pair are brought to the same integrated loudness, so the
# louder one cannot win by being louder. -18 LUFS is a common target for
# speech heard on a phone speaker.
LOUDNESS_LUFS = -18


@dataclass(frozen=True)
class Side:
    """One contender in a pair: what it is, and how it makes a WAV."""

    vendor: str
    model: str
    voice: str

    def label(self) -> dict:
        return {"vendor": self.vendor, "model": self.model, "voice": self.voice}


async def side_wav(side: Side, text: str, language: str, gender: str) -> bytes:
    if side.vendor == "google":
        return await gemini_tts.synthesize(
            text, language, gender=gender, purpose=PURPOSE
        )
    return await sarvam_tts.synthesize(
        text, language, gender=gender, purpose=PURPOSE, model=side.model
    )


def sides_for(compare: str, language: str, gender: str) -> list[Side] | None:
    """
    The two contenders for a language and persona, or None to skip it.

    v3-v4: Sarvam's v3 voice against its v4 Flash voice; skipped where the
    persona has no v4 voice, since both sides would be v3.
    sarvam-gemini: whatever production would say this with
    (voices.voice_for under TTS_MODEL: v4 Flash for Hindi and English, v3 for
    Tamil, Malayalam and Odia) against Gemini's voice for the persona.
    """
    if compare == "v3-v4":
        v4 = voices.voice_for(language, gender, voices.V4_FLASH_MODEL)
        if v4.model != voices.V4_FLASH_MODEL:
            return None
        v3 = voices.v3_voice(language, gender)
        return [
            Side("sarvam", v3.model, v3.speaker),
            Side("sarvam", v4.model, v4.speaker),
        ]
    sarvam = voices.voice_for(language, gender, settings.TTS_MODEL)
    gemini = gemini_tts.VOICES[voices.normalise_gender(gender)]
    return [
        Side("sarvam", sarvam.model, sarvam.speaker),
        Side("google", gemini_tts.MODEL, gemini),
    ]


async def matched_ogg(wav: bytes) -> bytes:
    """WAV to OGG/Opus at LOUDNESS_LUFS, in one ffmpeg pass."""
    proc = await asyncio.create_subprocess_exec(
        "ffmpeg",
        "-f",
        "wav",
        "-i",
        "pipe:0",
        "-af",
        f"loudnorm=I={LOUDNESS_LUFS}:TP=-2:LRA=11",
        "-ar",
        "48000",
        "-c:a",
        "libopus",
        "-f",
        "ogg",
        "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await asyncio.wait_for(proc.communicate(input=wav), timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg loudness match failed: {stderr.decode()[:200]}")
    return stdout


async def cmd_abtest(
    languages: list[str],
    gender: str,
    n: int,
    out: pathlib.Path,
    seed: int | None,
    compare: str = "v3-v4",
) -> None:
    if compare == "sarvam-gemini" and not settings.GEMINI_API_KEY:
        raise SystemExit(
            "GEMINI_API_KEY is not set in backend/.env; nothing has been called "
            "or billed."
        )
    out = ensure_outside_repo(out)
    rng = random.Random(seed)
    key: dict[str, dict] = {}
    title = (
        "Bulbul v3 against v4 Flash"
        if compare == "v3-v4"
        else "Sarvam (production voice) against Gemini 3.8 Flash TTS"
    )
    listing = [
        f"# Blind A/B: {title}, {gender} persona",
        "",
        "Each pair is the same text in two voices, matched for loudness. Score "
        "each pair in scores.csv without opening key.json.",
        "",
    ]
    for language in languages:
        sides = sides_for(compare, language, gender)
        if sides is None:
            print(f"{language}: no v4 Flash voice, both sides would be v3 — skipped")
            continue
        print(
            f"{language}: "
            + " vs ".join(f"{side.model} {side.voice}" for side in sides)
        )
        if language in NEEDS_NATIVE_CHECK:
            listing += [
                f"> **{language}:** the parent lines behind these replies were "
                "drafted by Claude and have not been checked by a native speaker.",
                "",
            ]
        for i, sample in enumerate(_samples(language, n), start=1):
            pair = f"{language}-{i:02d}"
            text = await dialogue_reply(language, sample, i, gender)
            clips = [
                await matched_ogg(await side_wav(side, text, language, gender))
                for side in sides
            ]
            order = [0, 1]
            rng.shuffle(order)
            for letter, index in zip("AB", order):
                (out / f"{pair}-{letter}.ogg").write_bytes(clips[index])
            key[pair] = {
                letter: sides[index].label() for letter, index in zip("AB", order)
            }
            listing += [f"## {pair}", "", text, ""]
            print(f"{pair} done")
    (out / "key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    with open(out / "scores.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(SCORE_COLUMNS)
        for pair in key:
            writer.writerow([pair] + [""] * (len(SCORE_COLUMNS) - 1))
    (out / "pairs.md").write_text("\n".join(listing), encoding="utf-8")
    print(f"Wrote {len(key)} pairs to {out}")


# ── abscore ──────────────────────────────────────────────────────────────────


def _column(header: list[str], prefix: str) -> int | None:
    """Index of the column whose name starts with `prefix`, if there is one.
    Older score sheets lack the age and understood columns."""
    for index, name in enumerate(header):
        if name.strip().lower().startswith(prefix):
            return index
    return None


def tally(rows: list[list[str]], key: dict) -> dict[str, dict]:
    """
    Per language: wins by model, "same", unscored rows, the understood-every-
    word answers, and the clarity notes. Each row is one listener's verdict
    on one pair, so a pair scored by three listeners counts three times.
    """
    header, body = rows[0], rows[1:]
    col_pref = _column(header, "preferred")
    col_understood = _column(header, "understood")
    col_note = _column(header, "clarity")
    col_listener = _column(header, "listener")
    result: dict[str, dict] = {}
    for row in body:
        if not row or not row[0].strip():
            continue
        pair = row[0].strip()
        sides = key.get(pair)
        if sides is None:
            continue
        language = pair.rsplit("-", 1)[0]
        lang = result.setdefault(
            language,
            {
                "wins": {side["model"]: 0 for side in sides.values()},
                "same": 0,
                "unscored": 0,
                "understood": {"y": 0, "n": 0},
                "notes": [],
            },
        )
        for side in sides.values():
            lang["wins"].setdefault(side["model"], 0)

        def cell(index):
            return row[index].strip() if index is not None and index < len(row) else ""

        choice = cell(col_pref).upper()
        if choice in ("A", "B"):
            winner = sides[choice]["model"]
            lang["wins"][winner] += 1
        elif choice in ("SAME", "S", "="):
            winner = "same"
            lang["same"] += 1
        else:
            winner = None
            lang["unscored"] += 1
        understood = cell(col_understood).lower()[:1]
        if understood in ("y", "n"):
            lang["understood"][understood] += 1
        note = cell(col_note)
        if note:
            lang["notes"].append(
                {
                    "pair": pair,
                    "listener": cell(col_listener),
                    "preferred": winner,
                    "note": note,
                }
            )
    return result


def cmd_abscore(source: pathlib.Path) -> dict[str, dict]:
    folder = source.expanduser().resolve()
    key = json.loads((folder / "key.json").read_text(encoding="utf-8"))
    with open(folder / "scores.csv", newline="", encoding="utf-8") as f:
        rows = list(csv.reader(f))
    result = tally(rows, key)
    for language, lang in result.items():
        print(f"\n{language}")
        for model, wins in sorted(lang["wins"].items(), key=lambda kv: -kv[1]):
            print(f"  {model:<24} {wins} wins")
        print(f"  {'same':<24} {lang['same']}")
        if lang["unscored"]:
            print(f"  {'not scored':<24} {lang['unscored']}")
        understood = lang["understood"]
        print(f"  understood every word    y {understood['y']} · n {understood['n']}")
        print(f"  clarity notes            {len(lang['notes'])}")
        for note in lang["notes"]:
            who = f" ({note['listener']})" if note["listener"] else ""
            print(
                f"    {note['pair']}{who}, preferred {note['preferred'] or '—'}: "
                f"{note['note']}"
            )
    return result


# ── voices ───────────────────────────────────────────────────────────────────

# Said by every persona in every language, so the voices can be compared on
# the same words.
VOICE_LINE = (
    "Namaste! I'm Katha. I'd love to hear about the house you grew up in. "
    "What do you remember about it?"
)
# Assamese and Urdu need beta access from Sarvam; synthesis fails without it.
_NO_SPEECH = {"as-IN", "ur-IN"}


async def voice_line(language: str, gender: str) -> str:
    """VOICE_LINE in `language`, with Katha's own forms in the persona's gender."""
    if language == "en-IN":
        return VOICE_LINE
    response = await llm.chat(
        [
            Message(
                role="user",
                content=(
                    f"Translate into natural spoken {_language_name(language)}, "
                    "as a respectful "
                    f"younger {'man' if gender == 'male' else 'woman'} speaking to "
                    "an elder: use that gender's forms where the speaker refers to "
                    "themselves, and the respectful form of address. Return only "
                    "the translation.\n\n" + VOICE_LINE
                ),
            )
        ],
        max_tokens=GLOSS_MAX_TOKENS,
        model=GLOSS_MODEL,
        effort="low",
        purpose=PURPOSE,
    )
    text = response.content.strip()
    if not text:
        raise RuntimeError(f"no translation came back for {language}")
    return text


async def cmd_voices(languages: list[str], out: pathlib.Path) -> None:
    out = ensure_outside_repo(out)
    lines = [
        "# Katha's voices, one per language and persona",
        "",
        f"`TTS_MODEL={settings.TTS_MODEL}`. For each clip: does the voice sound "
        "like the persona it was chosen for, and would a parent enjoy talking to "
        "it? Note anything off in voices.csv; a wrong voice is a one-line change "
        "in backend/media/voices.py.",
        "",
        "| Clip | Model | Voice | Heard before |",
        "|---|---|---|---|",
    ]
    rows = []
    texts = []
    for language in languages:
        for gender in voices.GENDERS:
            voice = voices.voice_for(language, gender, settings.TTS_MODEL)
            clip = f"{language}-{gender}.ogg"
            # One failure is reported and the rest still run: the point is
            # to hear every voice, and a rerun would pay for them all again.
            try:
                text = await voice_line(language, gender)
                (out / clip).write_bytes(await speak(text, language, gender))
            except Exception as exc:
                print(f"{clip} FAILED: {exc}")
                lines.append(
                    f"| `{clip}` | {voice.model} | {voice.speaker} | FAILED: {exc} |"
                )
                continue
            heard = "yes" if (language, gender) in voices.HEARD else "no"
            lines.append(f"| `{clip}` | {voice.model} | {voice.speaker} | {heard} |")
            texts += [f"## {clip}", "", text, ""]
            rows.append([clip, voice.model, voice.speaker, "", "", ""])
            print(f"{clip} done ({voice.model} {voice.speaker})")
    (out / "voices.md").write_text("\n".join(lines + [""] + texts), encoding="utf-8")
    with open(out / "voices.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "clip",
                "model",
                "voice",
                "sounds female/male",
                "good for Katha (Y/N)",
                "note",
            ]
        )
        writer.writerows(rows)
    print(f"Wrote {len(rows)} clips to {out}")


# ── pauses ───────────────────────────────────────────────────────────────────


def word_diff(before: str, after: str) -> list[str]:
    """Words lost or changed between the two transcripts, one line each."""
    a, b = before.split(), after.split()
    lines = []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b).get_opcodes():
        if op == "equal":
            continue
        lines.append(
            f"- {op}: `{' '.join(a[i1:i2]) or '∅'}` → `{' '.join(b[j1:j2]) or '∅'}`"
        )
    return lines


async def cmd_pauses(source: pathlib.Path, out: pathlib.Path) -> None:
    out = ensure_outside_repo(out)
    files = sorted(
        p
        for p in source.expanduser().resolve().iterdir()
        if p.suffix.lower() in AUDIO_SUFFIXES
    )
    if not files:
        raise SystemExit(f"No audio files in {source}")
    max_pause = settings.STT_MAX_PAUSE_SECONDS
    lines = [
        "# Pause compression check (C7)",
        "",
        f"Pauses longer than {max_pause}s cut to {max_pause}s. Acceptance: word "
        "count within 2% and no word lost at a cut "
        "(docs/specs/COST_TO_SERVE.md §11).",
        "",
        "| File | Seconds before | Seconds after | Words before | Words after | "
        "Difference |",
        "|---|---|---|---|---|---|",
    ]
    details = []
    for path in files:
        original = path.read_bytes()
        shortened, seconds_in, seconds_out = await audio_split.compress_pauses(
            original, max_pause
        )
        before = await sarvam_stt.transcribe(
            original, path.name, trim_pauses=False, purpose=PURPOSE
        )
        after = await sarvam_stt.transcribe(
            shortened, path.name, trim_pauses=False, purpose=PURPOSE
        )
        words_before = len(before.transcript.split())
        words_after = len(after.transcript.split())
        pct = (words_after - words_before) / words_before * 100 if words_before else 0.0

        def _s(value):
            return f"{value:.1f}" if value is not None else "?"

        lines.append(
            f"| {path.name} | {_s(seconds_in)} | {_s(seconds_out)} | "
            f"{words_before} | {words_after} | {pct:+.1f}% |"
        )
        diff = word_diff(before.transcript, after.transcript)
        details += [f"## {path.name}", ""] + (diff or ["No word differences."]) + [""]
        print(f"{path.name}: {words_before} -> {words_after} words ({pct:+.1f}%)")
    (out / "pauses.md").write_text("\n".join(lines + [""] + details), encoding="utf-8")
    print(f"Wrote {out / 'pauses.md'}")


# ── entry point ──────────────────────────────────────────────────────────────


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[1])
    sub = parser.add_subparsers(dest="command", required=True)

    replies = sub.add_parser("replies", help="dialogue replies to listen to (§8)")
    replies.add_argument("--lang", required=True, choices=sorted(SAMPLES))
    replies.add_argument("--gender", choices=voices.GENDERS, default="female")
    replies.add_argument("--n", type=int, default=10)
    replies.add_argument("--out", type=pathlib.Path, required=True)

    abtest = sub.add_parser("abtest", help="blind Bulbul v3 vs v4 Flash (§10)")
    abtest.add_argument("--langs", default="hi-IN,en-IN")
    abtest.add_argument("--gender", choices=voices.GENDERS, default="female")
    abtest.add_argument("--n", type=int, default=10)
    abtest.add_argument("--out", type=pathlib.Path, required=True)
    abtest.add_argument("--seed", type=int, default=None, help="fix the A/B order")
    abtest.add_argument(
        "--compare",
        choices=COMPARISONS,
        default="v3-v4",
        help="v3-v4: Bulbul v3 vs v4 Flash; sarvam-gemini: production Sarvam "
        "voice vs Gemini 3.8 Flash TTS",
    )

    abscore = sub.add_parser("abscore", help="tally a scored abtest folder")
    abscore.add_argument("--in", dest="source", type=pathlib.Path, required=True)

    voices_cmd = sub.add_parser("voices", help="every language and persona (§10)")
    voices_cmd.add_argument(
        "--langs",
        default=",".join(
            lang for lang in SUPPORTED_LANGUAGES if lang not in _NO_SPEECH
        ),
    )
    voices_cmd.add_argument("--out", type=pathlib.Path, required=True)

    pauses = sub.add_parser("pauses", help="transcripts with and without C7 (§11)")
    pauses.add_argument("--in", dest="source", type=pathlib.Path, required=True)
    pauses.add_argument("--out", type=pathlib.Path, required=True)
    return parser


async def run(args: argparse.Namespace) -> None:
    from models.db import engine

    require_ffmpeg()
    try:
        with usage.usage_scope(user_id=LEDGER_USER):
            if args.command == "replies":
                await cmd_replies(args.lang, args.gender, args.n, args.out)
            elif args.command == "abtest":
                languages = [lang.strip() for lang in args.langs.split(",") if lang]
                await cmd_abtest(
                    languages, args.gender, args.n, args.out, args.seed, args.compare
                )
            elif args.command == "voices":
                languages = [lang.strip() for lang in args.langs.split(",") if lang]
                await cmd_voices(languages, args.out)
            else:
                await cmd_pauses(args.source, args.out)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "abscore":
        # Reads two local files: no ffmpeg, no database, nothing billed.
        cmd_abscore(args.source)
        return 0
    asyncio.run(run(args))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
