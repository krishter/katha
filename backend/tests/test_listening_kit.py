"""
Smoke test for scripts/listening_kit.py with every vendor mocked: each
subcommand writes what it promises, outside the repository, billed as eval.
"""

from __future__ import annotations

import array
import csv
import io
import json
import math
import shutil
import subprocess
import wave
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from scripts import listening_kit as kit


@pytest.fixture
def vendors():
    async def _chat(messages, system=None, max_tokens=500, **kwargs):
        if kwargs.get("model") == kit.GLOSS_MODEL:
            return SimpleNamespace(content="An English gloss.", stop_reason="end_turn")
        return SimpleNamespace(
            content="<response>What a lovely memory. What was her name?</response>",
            stop_reason="end_turn",
        )

    chat = AsyncMock(side_effect=_chat)
    tts = AsyncMock(return_value=b"SARVAM-WAV")
    gemini = AsyncMock(return_value=b"GEMINI-WAV")
    # abtest writes loudness-matched clips; tag them so a test can tell
    # which vendor's clip landed on which side.
    matched = AsyncMock(side_effect=lambda wav: b"OGG:" + wav)
    with (
        patch("scripts.listening_kit.llm.chat", new=chat),
        patch("scripts.listening_kit.sarvam_tts.synthesize", new=tts),
        patch("scripts.listening_kit.gemini_tts.synthesize", new=gemini),
        patch("scripts.listening_kit.matched_ogg", new=matched),
        patch(
            "scripts.listening_kit.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda wav: b"OGG"),
        ),
    ):
        yield SimpleNamespace(chat=chat, tts=tts, gemini=gemini, matched=matched)


def test_every_language_has_ten_samples_with_the_required_mix():
    for language, samples in kit.SAMPLES.items():
        assert len(samples) == 10, language
        assert len({s.domain for s in samples}) >= 6, language
        assert sum("short" in s.note for s in samples) >= 2, language
        assert any("Kamala" in s.note for s in samples), language


def test_output_inside_the_repo_is_refused(tmp_path):
    with pytest.raises(SystemExit, match="outside the repository"):
        kit.ensure_outside_repo(kit.REPO_ROOT / "backend" / "listening")
    assert kit.ensure_outside_repo(tmp_path / "out").is_dir()


async def test_replies_writes_audio_and_a_review_sheet(tmp_path, vendors):
    await kit.cmd_replies("hi-IN", "male", 3, tmp_path)

    assert sorted(p.name for p in tmp_path.glob("*.ogg")) == [
        "01.ogg",
        "02.ogg",
        "03.ogg",
    ]
    sheet = (tmp_path / "replies.md").read_text()
    assert kit.SAMPLES["hi-IN"][0].transcript in sheet
    assert "What a lovely memory" in sheet and "An English gloss." in sheet
    # Every call is billed as eval; dialogue on the configured model.
    assert {c.kwargs["purpose"] for c in vendors.chat.await_args_list} == {"eval"}
    dialogue = [
        c for c in vendors.chat.await_args_list if c.kwargs["model"] != kit.GLOSS_MODEL
    ]
    assert dialogue[0].kwargs["cache_system_prefix"]  # the real two-part prompt
    assert {c.kwargs["purpose"] for c in vendors.tts.await_args_list} == {"eval"}
    # The persona reaches both the voice and the prompt.
    assert {c.kwargs["gender"] for c in vendors.tts.await_args_list} == {"male"}
    assert "man's voice" in dialogue[0].kwargs["cache_system_prefix"]
    assert "aditya_hi_conversational" in sheet


async def test_abtest_randomises_pairs_and_writes_a_key_and_blank_scores(
    tmp_path, vendors
):
    await kit.cmd_abtest(["hi-IN", "ta-IN", "en-IN"], "female", 2, tmp_path, seed=7)

    key = json.loads((tmp_path / "key.json").read_text())
    # Tamil has no v4 Flash voice, so it is skipped rather than compared to itself.
    assert sorted(key) == ["en-IN-01", "en-IN-02", "hi-IN-01", "hi-IN-02"]
    for pair, sides in key.items():
        assert {sides["A"]["model"], sides["B"]["model"]} == {
            "bulbul:v3",
            "bulbul:v4-flash",
        }
        assert (tmp_path / f"{pair}-A.ogg").exists()
        assert (tmp_path / f"{pair}-B.ogg").exists()
    # Both sides of a pair are the same text.
    texts = [c.args[0] for c in vendors.tts.await_args_list]
    assert texts[0] == texts[1]
    with open(tmp_path / "scores.csv") as f:
        rows = list(csv.reader(f))
    assert rows[0] == [
        "pair",
        "listener",
        "listener age",
        "preferred (A/B/same)",
        "understood every word (y/n)",
        "clarity note",
    ]
    assert [r[0] for r in rows[1:]] == list(key) and all(
        r[1:] == [""] * 5 for r in rows[1:]
    )
    # Every clip written was loudness-matched first.
    assert vendors.matched.await_count == 2 * len(key)


async def test_pauses_reports_counts_and_a_word_diff(tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "one.ogg").write_bytes(b"original")
    (notes / "readme.txt").write_text("not audio")
    out = tmp_path / "out"

    transcripts = {
        b"original": "my father ran a small shop near the temple",
        b"shorter": "my father ran a shop near the temple",
    }
    stt = AsyncMock(
        side_effect=lambda audio, name, **kw: SimpleNamespace(
            transcript=transcripts[audio]
        )
    )
    with (
        patch(
            "scripts.listening_kit.audio_split.compress_pauses",
            new=AsyncMock(return_value=(b"shorter", 40.0, 31.5)),
        ),
        patch("scripts.listening_kit.sarvam_stt.transcribe", new=stt),
    ):
        await kit.cmd_pauses(notes, out)

    report = (out / "pauses.md").read_text()
    assert "| one.ogg | 40.0 | 31.5 | 9 | 8 | -11.1% |" in report
    assert "delete: `small` → `∅`" in report
    assert "readme" not in report
    # Both transcriptions skip the production trim and are billed as eval.
    assert all(
        c.kwargs == {"trim_pauses": False, "purpose": "eval"}
        for c in stt.await_args_list
    )
    assert not list(out.glob("*.ogg"))  # no audio written


def test_cli_parses_each_subcommand(tmp_path):
    parser = kit.build_parser()
    args = parser.parse_args(["replies", "--lang", "ta-IN", "--out", str(tmp_path)])
    assert (args.command, args.n) == ("replies", 10)
    args = parser.parse_args(["abtest", "--out", str(tmp_path)])
    assert args.langs == "hi-IN,en-IN"
    args = parser.parse_args(["pauses", "--in", str(tmp_path), "--out", str(tmp_path)])
    assert args.source == tmp_path
    args = parser.parse_args(["voices", "--out", str(tmp_path)])
    # Assamese and Urdu are left out: Sarvam needs beta access for them.
    assert "as-IN" not in args.langs and "ur-IN" not in args.langs
    assert "ml-IN" in args.langs


async def test_voices_writes_a_clip_per_language_and_persona(tmp_path, vendors):
    await kit.cmd_voices(["hi-IN", "ta-IN"], tmp_path)

    assert sorted(p.name for p in tmp_path.glob("*.ogg")) == [
        "hi-IN-female.ogg",
        "hi-IN-male.ogg",
        "ta-IN-female.ogg",
        "ta-IN-male.ogg",
    ]
    sheet = (tmp_path / "voices.md").read_text()
    assert "aditya_hi_conversational" in sheet and "shubh" in sheet
    with open(tmp_path / "voices.csv") as f:
        rows = list(csv.reader(f))
    assert len(rows) == 5 and rows[1][0] == "hi-IN-female.ogg"
    genders = [c.kwargs["gender"] for c in vendors.tts.await_args_list]
    assert genders == ["female", "male", "female", "male"]


async def test_a_missing_ffmpeg_stops_before_any_billed_call(tmp_path, vendors):
    with (
        patch("scripts.listening_kit.shutil.which", return_value=None),
        pytest.raises(SystemExit, match="nothing has been called or billed"),
    ):
        await kit.run(
            kit.build_parser().parse_args(
                ["replies", "--lang", "hi-IN", "--out", str(tmp_path)]
            )
        )
    vendors.chat.assert_not_awaited()
    vendors.tts.assert_not_awaited()


# ── --compare and abscore (docs/proposals/tts-strategy.md §5) ────────────────


@pytest.mark.parametrize(
    "compare,language,gender,expected",
    [
        (
            "sarvam-gemini",
            "hi-IN",
            "female",
            [
                ("sarvam", "bulbul:v4-flash", "ritu_hi_conversation"),
                ("google", "gemini-3.8-flash-tts", "Sulafat"),
            ],
        ),
        (
            "sarvam-gemini",
            "en-IN",
            "male",
            [
                ("sarvam", "bulbul:v4-flash", "dev_en_conversational"),
                ("google", "gemini-3.8-flash-tts", "Achird"),
            ],
        ),
        # Tamil, Malayalam and Odia have no v4 voice: production says them on
        # v3, so that is what Gemini is compared against.
        (
            "sarvam-gemini",
            "ta-IN",
            "female",
            [
                ("sarvam", "bulbul:v3", "ritu"),
                ("google", "gemini-3.8-flash-tts", "Sulafat"),
            ],
        ),
        (
            "sarvam-gemini",
            "or-IN",
            "male",
            [
                ("sarvam", "bulbul:v3", "shubh"),
                ("google", "gemini-3.8-flash-tts", "Achird"),
            ],
        ),
        (
            "v3-v4",
            "hi-IN",
            "female",
            [
                ("sarvam", "bulbul:v3", "ritu"),
                ("sarvam", "bulbul:v4-flash", "ritu_hi_conversation"),
            ],
        ),
        ("v3-v4", "ta-IN", "female", None),
    ],
)
def test_compare_picks_the_two_sides(monkeypatch, compare, language, gender, expected):
    monkeypatch.setattr("scripts.listening_kit.settings.TTS_MODEL", "bulbul:v4-flash")
    sides = kit.sides_for(compare, language, gender)
    if expected is None:
        assert sides is None
    else:
        assert [(s.vendor, s.model, s.voice) for s in sides] == expected


async def test_sarvam_gemini_abtest_records_the_real_sides(
    tmp_path, vendors, monkeypatch
):
    monkeypatch.setattr("scripts.listening_kit.settings.GEMINI_API_KEY", "k")
    monkeypatch.setattr("scripts.listening_kit.settings.TTS_MODEL", "bulbul:v4-flash")
    await kit.cmd_abtest(
        ["hi-IN", "ta-IN"], "female", 2, tmp_path, seed=3, compare="sarvam-gemini"
    )

    key = json.loads((tmp_path / "key.json").read_text())
    # Tamil is not skipped here: its Sarvam side is v3.
    assert sorted(key) == ["hi-IN-01", "hi-IN-02", "ta-IN-01", "ta-IN-02"]
    assert key["ta-IN-01"]["A"]["model"] in {"bulbul:v3", "gemini-3.8-flash-tts"}
    for pair, sides in key.items():
        labels = {(s["vendor"], s["model"], s["voice"]) for s in sides.values()}
        sarvam = (
            ("sarvam", "bulbul:v4-flash", "ritu_hi_conversation")
            if pair.startswith("hi")
            else ("sarvam", "bulbul:v3", "ritu")
        )
        assert labels == {sarvam, ("google", "gemini-3.8-flash-tts", "Sulafat")}
        # The file on each side is that side's vendor's clip.
        for letter, side in sides.items():
            clip = (tmp_path / f"{pair}-{letter}.ogg").read_bytes()
            tag = b"GEMINI" if side["vendor"] == "google" else b"SARVAM"
            assert clip == b"OGG:" + tag + b"-WAV"
    # Same text to both vendors, with the persona.
    sarvam_texts = [c.args[0] for c in vendors.tts.await_args_list]
    gemini_texts = [c.args[0] for c in vendors.gemini.await_args_list]
    assert sarvam_texts == gemini_texts
    assert {c.kwargs["gender"] for c in vendors.gemini.await_args_list} == {"female"}
    assert {c.kwargs["purpose"] for c in vendors.gemini.await_args_list} == {"eval"}


async def test_the_ab_order_is_seeded(tmp_path, vendors, monkeypatch):
    monkeypatch.setattr("scripts.listening_kit.settings.GEMINI_API_KEY", "k")
    first, second = tmp_path / "1", tmp_path / "2"
    for out in (first, second):
        await kit.cmd_abtest(
            ["en-IN"], "female", 4, out, seed=11, compare="sarvam-gemini"
        )
    assert (first / "key.json").read_text() == (second / "key.json").read_text()


async def test_sarvam_gemini_without_a_key_stops_before_any_call(
    tmp_path, vendors, monkeypatch
):
    monkeypatch.setattr("scripts.listening_kit.settings.GEMINI_API_KEY", "")
    with pytest.raises(SystemExit, match="GEMINI_API_KEY"):
        await kit.cmd_abtest(
            ["hi-IN"], "female", 1, tmp_path, seed=1, compare="sarvam-gemini"
        )
    vendors.chat.assert_not_called()
    vendors.tts.assert_not_called()


def test_new_languages_have_samples_flagged_for_a_native_check():
    for language in ("ml-IN", "or-IN"):
        assert len(kit.SAMPLES[language]) == 10
        assert language in kit.NEEDS_NATIVE_CHECK
    assert [s.domain for s in kit.SAMPLES["ml-IN"]] == [
        s.domain for s in kit.SAMPLES["ta-IN"]
    ]


async def test_flagged_languages_are_marked_in_the_pair_listing(
    tmp_path, vendors, monkeypatch
):
    monkeypatch.setattr("scripts.listening_kit.settings.GEMINI_API_KEY", "k")
    await kit.cmd_abtest(
        ["ml-IN"], "female", 1, tmp_path, seed=1, compare="sarvam-gemini"
    )
    assert "drafted by Claude" in (tmp_path / "pairs.md").read_text()


_KEY = {
    "hi-IN-01": {
        "A": {"vendor": "sarvam", "model": "bulbul:v4-flash", "voice": "v4"},
        "B": {"vendor": "google", "model": "gemini-3.8-flash-tts", "voice": "S"},
    },
    "hi-IN-02": {
        "A": {"vendor": "google", "model": "gemini-3.8-flash-tts", "voice": "S"},
        "B": {"vendor": "sarvam", "model": "bulbul:v4-flash", "voice": "v4"},
    },
    "ta-IN-01": {
        "A": {"vendor": "sarvam", "model": "bulbul:v3", "voice": "ritu"},
        "B": {"vendor": "google", "model": "gemini-3.8-flash-tts", "voice": "S"},
    },
}


def _write_scores(folder, rows):
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "key.json").write_text(json.dumps(_KEY))
    with open(folder / "scores.csv", "w", newline="") as f:
        csv.writer(f).writerows(rows)


def test_abscore_tallies_wins_same_and_clarity_per_language(tmp_path, capsys):
    _write_scores(
        tmp_path,
        [
            kit.SCORE_COLUMNS,
            ["hi-IN-01", "Krish", "45", "B", "y", ""],
            ["hi-IN-01", "Amma", "72", "a", "n", "A mumbled the name"],
            ["hi-IN-02", "Krish", "45", "A", "y", ""],
            ["hi-IN-02", "Amma", "72", "same", "y", ""],
            ["ta-IN-01", "Paati", "80", "", "", ""],
        ],
    )
    result = kit.cmd_abscore(tmp_path)

    hi = result["hi-IN"]
    # hi-IN-01 B and hi-IN-02 A are both Gemini; hi-IN-01 "a" is Sarvam.
    assert hi["wins"] == {"bulbul:v4-flash": 1, "gemini-3.8-flash-tts": 2}
    assert hi["same"] == 1
    assert hi["understood"] == {"y": 3, "n": 1}
    assert hi["notes"] == [
        {
            "pair": "hi-IN-01",
            "listener": "Amma",
            "preferred": "bulbul:v4-flash",
            "note": "A mumbled the name",
        }
    ]
    ta = result["ta-IN"]
    assert ta["wins"] == {"bulbul:v3": 0, "gemini-3.8-flash-tts": 0}
    assert ta["unscored"] == 1

    printed = capsys.readouterr().out
    assert "gemini-3.8-flash-tts" in printed and "2 wins" in printed
    assert "understood every word    y 3 · n 1" in printed


def test_abscore_reads_a_sheet_from_before_the_new_columns(tmp_path):
    _write_scores(
        tmp_path,
        [
            ["pair", "listener", "preferred (A/B/same)", "clarity note"],
            ["hi-IN-01", "Krish", "A", "clearer"],
            ["hi-IN-02", "Krish", "B", ""],
        ],
    )
    hi = kit.cmd_abscore(tmp_path)["hi-IN"]
    assert hi["wins"] == {"bulbul:v4-flash": 2, "gemini-3.8-flash-tts": 0}
    assert hi["understood"] == {"y": 0, "n": 0}


def test_abscore_needs_no_ffmpeg_or_database(tmp_path):
    _write_scores(tmp_path, [kit.SCORE_COLUMNS, ["hi-IN-01", "K", "", "A", "y", ""]])
    with (
        patch("scripts.listening_kit.require_ffmpeg") as ffmpeg,
        patch("scripts.listening_kit.run") as run,
    ):
        assert kit.main(["abscore", "--in", str(tmp_path)]) == 0
    ffmpeg.assert_not_called()
    run.assert_not_called()


def _tone_wav(amplitude: int, seconds: float = 3.0) -> bytes:
    rate = 22050
    samples = array.array(
        "h",
        (
            int(amplitude * math.sin(2 * math.pi * 220 * i / rate))
            for i in range(int(rate * seconds))
        ),
    )
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(rate)
        w.writeframes(samples.tobytes())
    return buffer.getvalue()


def _mean_db(ogg: bytes) -> float:
    out = subprocess.run(
        ["ffmpeg", "-i", "pipe:0", "-af", "volumedetect", "-f", "null", "-"],
        input=ogg,
        capture_output=True,
    ).stderr.decode()
    return float(out.split("mean_volume:")[1].split("dB")[0])


@pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="needs ffmpeg")
async def test_matched_ogg_brings_a_quiet_and_a_loud_clip_to_the_same_level():
    loud = await kit.matched_ogg(_tone_wav(8000))
    quiet = await kit.matched_ogg(_tone_wav(800))
    # 20 dB apart going in; within 1 dB coming out.
    assert abs(_mean_db(loud) - _mean_db(quiet)) < 1.0
