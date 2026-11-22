"""Scripts run alone, not inside the app, so they must load every model their
tables point at. backfill_embeddings once imported only StoryAtom, and every
flush failed on story_atoms.turn_id -> turns."""

import subprocess
import sys
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent


@pytest.mark.parametrize("script", ["backfill_embeddings", "session_health"])
def test_script_resolves_every_foreign_key_on_its_own(script):
    code = (
        f"import scripts.{script}\n"
        "from models.story_atom import StoryAtom\n"
        "for table in StoryAtom.metadata.tables.values():\n"
        "    for fk in table.foreign_keys:\n"
        "        fk.column\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", code],
        cwd=BACKEND,
        capture_output=True,
        text=True,
        env={"SARVAM_API_KEY": "test", "ANTHROPIC_API_KEY": "test", "PATH": ""},
    )
    assert result.returncode == 0, result.stderr[-800:]
