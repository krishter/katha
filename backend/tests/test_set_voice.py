"""
scripts/set_voice.py and the a7c2e9f4b8d1 backfill
(docs/specs/GEMINI_TTS.md §4.2, §5). No database: profiles are stand-ins.
"""

from __future__ import annotations

import importlib.util
import pathlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from media import voices
from models.voice_change import VoiceChange
from scripts import set_voice

BACKEND = pathlib.Path(__file__).resolve().parents[1]
SARVAM_HI = "sarvam/bulbul:v4-flash/ritu_hi_conversation"
GEMINI_F = "google/gemini-3.8-flash-lite-tts/Sulafat"


def profile(user_id, language, gender, pin):
    return SimpleNamespace(
        user_id=user_id,
        preferred_language=language,
        katha_gender=gender,
        tts_voice=pin,
    )


@pytest.fixture(autouse=True)
def defaults(monkeypatch):
    monkeypatch.setattr("config.settings.TTS_VENDOR", "gemini")
    monkeypatch.setattr("config.settings.GEMINI_TTS_LANGUAGES", "all")
    monkeypatch.setattr("config.settings.TTS_MODEL", "bulbul:v4-flash")


def test_moving_to_gemini_picks_the_persona_voice():
    moves = set_voice.plan(
        [
            profile("a", "hi-IN", "female", SARVAM_HI),
            profile("b", "ta-IN", "male", "sarvam/bulbul:v3/shubh"),
        ],
        "gemini",
    )
    assert [(m.user_id, m.to_voice) for m in moves] == [
        ("a", GEMINI_F),
        ("b", "google/gemini-3.8-flash-lite-tts/Achird"),
    ]


def test_urdu_and_unswitched_languages_are_left_alone(monkeypatch):
    monkeypatch.setattr("config.settings.GEMINI_TTS_LANGUAGES", "hi-IN")
    moves = set_voice.plan(
        [
            profile("u", "ur-IN", "female", "sarvam/bulbul:v3/ritu"),
            profile("t", "ta-IN", "female", "sarvam/bulbul:v3/ritu"),
        ],
        "gemini",
    )
    assert all(m.to_voice is None for m in moves)
    assert "ur-IN" in moves[0].note and "ta-IN" in moves[1].note


def test_a_parent_already_on_the_voice_is_not_moved():
    (move,) = set_voice.plan([profile("a", "hi-IN", "female", GEMINI_F)], "gemini")
    assert move.to_voice is None and move.note == "already on it"


def test_rollback_goes_to_the_persona_sarvam_voice():
    (move,) = set_voice.plan([profile("a", "ml-IN", "male", "x")], "sarvam")
    assert move.to_voice == voices.voice_for("ml-IN", "male", "bulbul:v4-flash").pin


def test_an_exact_pin_must_be_able_to_speak_the_language():
    (move,) = set_voice.plan(
        [profile("a", "ta-IN", "female", "x")],
        "sarvam/bulbul:v4-flash/simran_en_conversation",
    )
    assert move.to_voice is None


async def test_apply_moves_the_pin_and_records_who_and_why():
    parents = [
        profile("a", "hi-IN", "female", SARVAM_HI),
        profile("u", "ur-IN", "female", "sarvam/bulbul:v3/ritu"),
    ]
    moves = set_voice.plan(parents, "gemini")
    db = MagicMock()
    db.commit = AsyncMock()

    changed = await set_voice.apply(db, parents, moves, by="Krish", reason="D2")

    assert changed == 1
    assert parents[0].tts_voice == GEMINI_F
    assert parents[1].tts_voice == "sarvam/bulbul:v3/ritu"
    (row,) = [c.args[0] for c in db.add.call_args_list]
    assert isinstance(row, VoiceChange)
    assert (row.user_id, row.from_voice, row.to_voice, row.changed_by, row.reason) == (
        "a",
        SARVAM_HI,
        GEMINI_F,
        "Krish",
        "D2",
    )
    db.commit.assert_awaited_once()


def test_applying_without_saying_who_is_refused():
    with pytest.raises(SystemExit):
        set_voice.parse(["--all", "--to", "gemini", "--apply"])
    with pytest.raises(SystemExit):
        set_voice.parse(["--user-id", "a"])  # no --to
    args = set_voice.parse(["--all", "--to", "gemini"])
    assert not args.apply  # a dry run unless asked


# ── the migration's backfill ─────────────────────────────────────────────────


def _migration():
    path = BACKEND / "migrations/versions/a7c2e9f4b8d1_pin_tts_voice.py"
    spec = importlib.util.spec_from_file_location("pin_tts_voice", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize("tts_model", ["bulbul:v4-flash", "bulbul:v3"])
def test_backfill_pins_every_parent_to_the_voice_they_hear_today(
    monkeypatch, tts_model
):
    """Nobody's voice changes by migrating: each existing profile is pinned
    to what the Sarvam rule gives under the deploy's TTS_MODEL."""
    migration = _migration()
    rows = [
        SimpleNamespace(id=i, preferred_language=language, katha_gender=gender)
        for i, (language, gender) in enumerate(
            [
                ("hi-IN", "female"),
                ("ta-IN", "male"),
                ("or-IN", "female"),
                ("ur-IN", "male"),
            ]
        )
    ]
    updates = {}

    class Bind:
        def execute(self, statement, params=None):
            if params is None:
                return SimpleNamespace(fetchall=lambda: rows)
            updates[params["id"]] = params["pin"]

    op = MagicMock()
    op.get_bind.return_value = Bind()
    monkeypatch.setattr(migration, "op", op)
    monkeypatch.setenv("TTS_MODEL", tts_model)

    migration.upgrade()

    assert updates == {
        row.id: voices.voice_for(
            row.preferred_language, row.katha_gender, tts_model
        ).pin
        for row in rows
    }
    assert all(pin.startswith("sarvam/") for pin in updates.values())
    op.alter_column.assert_called_once_with(
        "user_profiles", "tts_voice", nullable=False
    )
