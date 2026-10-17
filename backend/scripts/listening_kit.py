#!/usr/bin/env python
"""
Operator listening kit for the human checks in docs/specs/COST_TO_SERVE.md.

Three subcommands, one per check the eval harness cannot do:

    # §8: hear how the dialogue model sounds in a parent's language
    python scripts/listening_kit.py replies --lang hi-IN --n 10 --out ~/kit/hi

    # §10: blind A/B of Bulbul v3 against v4 Flash on identical text
    python scripts/listening_kit.py abtest --langs hi-IN,en-IN --out ~/kit/ab

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

from adapters import llm, sarvam_stt, sarvam_tts  # noqa: E402
from adapters.llm import Message  # noqa: E402
from config import settings  # noqa: E402
from core import conversation_policy, orchestrator, usage  # noqa: E402
from core.session_manager import SessionState  # noqa: E402
from media import audio_convert, audio_split, voices  # noqa: E402
from prompts.system_prompt import (  # noqa: E402
    PriorContext,
    UserProfile,
    build_system_prompt_parts,
)

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
LEDGER_USER = "eval-listening-kit"
PURPOSE = "eval"
GLOSS_MODEL = "claude-haiku-5-5"
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
}


def _profile(language: str) -> UserProfile:
    return UserProfile(
        name=_PROFILES.get(language, "Lakshmi"),
        preferred_language=language,
        onboarding_context="Retired schoolteacher. Grew up in a small town.",
        pronouns="she/her",
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


async def dialogue_reply(language: str, sample: Sample, index: int) -> str:
    """The reply production would give: real prompt, model and policy."""
    state = _state(sample.domain, index)
    parts = build_system_prompt_parts(_profile(language), state, _PRIOR)
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
        max_tokens=800,
        model=GLOSS_MODEL,
        effort="low",
        purpose=PURPOSE,
    )
    return response.content.strip()


async def speak(text: str, language: str, model: str | None = None) -> bytes:
    wav = await sarvam_tts.synthesize(text, language, purpose=PURPOSE, model=model)
    return await audio_convert.convert_wav_to_ogg(wav)


# ── replies ──────────────────────────────────────────────────────────────────


async def cmd_replies(language: str, n: int, out: pathlib.Path) -> None:
    out = ensure_outside_repo(out)
    tts_model, voice = voices.voice_for(settings.TTS_MODEL, language)
    lines = [
        f"# Listening check: {language}",
        "",
        f"Dialogue model `{settings.LLM_DIALOGUE_MODEL}` at effort "
        f"`{settings.LLM_DIALOGUE_EFFORT}`; speech `{tts_model}` voice "
        f"`{voice}`. Session 5, synthetic profile (she/her).",
        "",
        "Listen for warmth and register, not just correctness "
        "(docs/specs/COST_TO_SERVE.md §8).",
        "",
    ]
    for i, sample in enumerate(_samples(language, n), start=1):
        reply = await dialogue_reply(language, sample, i)
        gloss = await english_gloss(reply, language)
        (out / f"{i:02d}.ogg").write_bytes(await speak(reply, language))
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


async def cmd_abtest(
    languages: list[str], n: int, out: pathlib.Path, seed: int | None
) -> None:
    out = ensure_outside_repo(out)
    rng = random.Random(seed)
    key: dict[str, dict] = {}
    listing = [
        "# Blind A/B: Bulbul v3 against v4 Flash",
        "",
        "Each pair is the same text in two voices. Score each pair in scores.csv "
        "without opening key.json.",
        "",
    ]
    for language in languages:
        v4_model, _ = voices.voice_for(voices.V4_FLASH_MODEL, language)
        if v4_model != voices.V4_FLASH_MODEL:
            print(f"{language}: no v4 Flash voice, both sides would be v3 — skipped")
            continue
        for i, sample in enumerate(_samples(language, n), start=1):
            pair = f"{language}-{i:02d}"
            text = await dialogue_reply(language, sample, i)
            clips = {
                voices.V3_MODEL: await speak(text, language, voices.V3_MODEL),
                voices.V4_FLASH_MODEL: await speak(
                    text, language, voices.V4_FLASH_MODEL
                ),
            }
            order = [voices.V3_MODEL, voices.V4_FLASH_MODEL]
            rng.shuffle(order)
            for side, model in zip("AB", order):
                (out / f"{pair}-{side}.ogg").write_bytes(clips[model])
            key[pair] = {
                side: {"model": model, "voice": voices.voice_for(model, language)[1]}
                for side, model in zip("AB", order)
            }
            listing += [f"## {pair}", "", text, ""]
            print(f"{pair} done")
    (out / "key.json").write_text(json.dumps(key, indent=2), encoding="utf-8")
    with open(out / "scores.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["pair", "listener", "preferred (A/B/same)", "clarity note"])
        for pair in key:
            writer.writerow([pair, "", "", ""])
    (out / "pairs.md").write_text("\n".join(listing), encoding="utf-8")
    print(f"Wrote {len(key)} pairs to {out}")


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
        f"Pauses longer than {max_pause}s shortened to 0.5s. Acceptance: word "
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
    replies.add_argument("--lang", required=True, choices=["hi-IN", "ta-IN"])
    replies.add_argument("--n", type=int, default=10)
    replies.add_argument("--out", type=pathlib.Path, required=True)

    abtest = sub.add_parser("abtest", help="blind Bulbul v3 vs v4 Flash (§10)")
    abtest.add_argument("--langs", default="hi-IN,en-IN")
    abtest.add_argument("--n", type=int, default=10)
    abtest.add_argument("--out", type=pathlib.Path, required=True)
    abtest.add_argument("--seed", type=int, default=None, help="fix the A/B order")

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
                await cmd_replies(args.lang, args.n, args.out)
            elif args.command == "abtest":
                languages = [lang.strip() for lang in args.langs.split(",") if lang]
                await cmd_abtest(languages, args.n, args.out, args.seed)
            else:
                await cmd_pauses(args.source, args.out)
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    asyncio.run(run(build_parser().parse_args(argv)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
