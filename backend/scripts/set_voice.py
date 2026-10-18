"""
Move a parent to a different Katha voice, deliberately, and record it
(docs/specs/GEMINI_TTS.md §4.2, D2).

A parent's voice is pinned on her profile at setup, and nothing else ever
changes it: not TTS_VENDOR, not TTS_MODEL, not the voice table. This is
the one way to. Every move writes a voice_changes row saying from what, to
what, by whom and why.

Dry run by default: it prints what it would change. Pass --apply to write.

Usage (from backend/):
    # Show every parent's voice
    python -m scripts.set_voice --show

    # One family to Gemini, after texting them (D2)
    python -m scripts.set_voice --user-id user_abc --to gemini \\
        --by Krish --reason "D2 switch, family texted 2026-10-19" --apply

    # Every family whose language Gemini speaks
    python -m scripts.set_voice --all --to gemini --by Krish \\
        --reason "D2 switch" --apply

    # Rollback: back to the persona's Sarvam voice
    python -m scripts.set_voice --user-id user_abc --to sarvam --by Krish \\
        --reason "rollback" --apply

--to also takes an exact pin, "vendor/model/speaker".
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from dataclasses import dataclass

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from config import settings  # noqa: E402
from media import voices  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402
from models.voice_change import VoiceChange  # noqa: E402


@dataclass(frozen=True)
class Move:
    user_id: str
    language: str
    from_voice: str
    to_voice: str | None  # None: cannot move (the reason is in `note`)
    note: str = ""


def target_pin(to: str, language: str, gender: str) -> tuple[str | None, str]:
    """The pin `to` means for this parent, or (None, why not)."""
    if to == "gemini":
        if language not in voices.parse_languages(settings.GEMINI_TTS_LANGUAGES):
            return None, f"Gemini is not switched on for {language}"
        return voices.gemini_voice(language, gender).pin, ""
    if to == "sarvam":
        return voices.sarvam_counterpart(language, gender, settings.TTS_MODEL).pin, ""
    if voices.from_pin(to, language) is None:
        return None, f"{to!r} is not a voice that can speak {language}"
    return to, ""


def plan(profiles, to: str) -> list[Move]:
    moves = []
    for profile in profiles:
        pin, why = target_pin(to, profile.preferred_language, profile.katha_gender)
        if pin == profile.tts_voice:
            why = "already on it"
            pin = None
        moves.append(
            Move(
                profile.user_id, profile.preferred_language, profile.tts_voice, pin, why
            )
        )
    return moves


async def apply(db, profiles, moves: list[Move], *, by: str, reason: str) -> int:
    """Write every possible move and its voice_changes row in one commit."""
    by_user = {profile.user_id: profile for profile in profiles}
    changed = 0
    for move in moves:
        if move.to_voice is None:
            continue
        by_user[move.user_id].tts_voice = move.to_voice
        db.add(
            VoiceChange(
                user_id=move.user_id,
                from_voice=move.from_voice,
                to_voice=move.to_voice,
                changed_by=by,
                reason=reason,
            )
        )
        changed += 1
    await db.commit()
    return changed


async def main(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        query = select(UserProfileModel).order_by(UserProfileModel.created_at)
        if args.user_id:
            query = query.where(UserProfileModel.user_id == args.user_id)
        profiles = list((await db.execute(query)).scalars().all())
        if args.user_id and not profiles:
            raise SystemExit(f"No profile for {args.user_id}")

        if args.show:
            for p in profiles:
                print(
                    f"{p.user_id}  {p.preferred_language}  "
                    f"{p.katha_gender:6}  {p.tts_voice}"
                )
            return

        moves = plan(profiles, args.to)
        for move in moves:
            if move.to_voice:
                print(
                    f"{move.user_id} ({move.language}): "
                    f"{move.from_voice} -> {move.to_voice}"
                )
            else:
                print(f"{move.user_id} ({move.language}): unchanged, {move.note}")
        if not args.apply:
            print("Dry run: nothing written. Add --apply to make these changes.")
            return
        changed = await apply(db, profiles, moves, by=args.by, reason=args.reason)
        print(f"Moved {changed} parent(s); each move is recorded in voice_changes.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    who = parser.add_mutually_exclusive_group(required=True)
    who.add_argument("--user-id")
    who.add_argument("--all", action="store_true")
    parser.add_argument("--show", action="store_true", help="list voices and exit")
    parser.add_argument("--to", help='"gemini", "sarvam" or a vendor/model/speaker pin')
    parser.add_argument("--by", help="who is making the change (recorded)")
    parser.add_argument("--reason", default="", help="why (recorded)")
    parser.add_argument("--apply", action="store_true", help="write the changes")
    return parser


def parse(argv: list[str] | None = None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.show and not args.to:
        parser.error("--to is required unless --show")
    if args.apply and not (args.by and args.by.strip()):
        parser.error("--by is required with --apply: every change records who made it")
    return args


if __name__ == "__main__":
    asyncio.run(main(parse()))
