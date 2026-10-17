"""
Smoke test for scripts/listening_kit.py with every vendor mocked: each
subcommand writes what it promises, outside the repository, billed as eval.
"""

from __future__ import annotations

import csv
import json
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
    tts = AsyncMock(return_value=b"WAV")
    with (
        patch("scripts.listening_kit.llm.chat", new=chat),
        patch("scripts.listening_kit.sarvam_tts.synthesize", new=tts),
        patch(
            "scripts.listening_kit.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda wav: b"OGG"),
        ),
    ):
        yield SimpleNamespace(chat=chat, tts=tts)


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
    await kit.cmd_replies("hi-IN", 3, tmp_path)

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


async def test_abtest_randomises_pairs_and_writes_a_key_and_blank_scores(
    tmp_path, vendors
):
    await kit.cmd_abtest(["hi-IN", "ta-IN", "en-IN"], 2, tmp_path, seed=7)

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
    assert rows[0] == ["pair", "listener", "preferred (A/B/same)", "clarity note"]
    assert [r[0] for r in rows[1:]] == list(key) and all(
        r[1:] == ["", "", ""] for r in rows[1:]
    )


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
