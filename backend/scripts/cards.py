"""
Turn conversation cards on or off for one family, and record what the
child told you on the setup call (docs/specs/CONVERSATION_CARDS.md §15
Steps 3–4).

Cards run for a parent only when CONVERSATION_CARDS_ENABLED is on AND her
profile has them enabled; this script sets the second. It also records the
child's name, what the child calls her, the child's timezone and usual call
times, which the card wording and the send time depend on.

Dry run by default: it prints what it would change. Pass --apply to write.

Usage (from backend/):
    python -m scripts.cards --show
    python -m scripts.cards --user-id user_abc --enable \\
        --child-name Priya --parent-title Amma \\
        --timezone America/New_York --slot "sun 11:00" --slot "wed 19:30" --apply
    python -m scripts.cards --user-id user_abc --disable --apply
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from cards import setup  # noqa: E402
from cards.context import family_of  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402


def parse_slot(text: str) -> dict:
    """ "sun 11:00" -> {"dow": 6, "time": "11:00"}."""
    try:
        return setup.parse_slot(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def parse_timezone(name: str) -> str:
    try:
        return setup.parse_timezone(name)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from exc


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id")
    parser.add_argument("--show", action="store_true", help="list families and exit")
    switch = parser.add_mutually_exclusive_group()
    switch.add_argument("--enable", action="store_true")
    switch.add_argument("--disable", action="store_true")
    parser.add_argument("--child-name")
    parser.add_argument("--parent-title", help='what the child calls her: "Amma"')
    parser.add_argument("--timezone", type=parse_timezone, help="the child's")
    parser.add_argument(
        "--slot", type=parse_slot, action="append", help='"sun 11:00"; repeat'
    )
    parser.add_argument("--apply", action="store_true", help="write the changes")
    return parser


def parse(argv=None) -> argparse.Namespace:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.show and not args.user_id:
        parser.error("--user-id is required unless --show")
    if args.enable and not (args.child_name and args.parent_title):
        parser.error(
            "--enable needs --child-name and --parent-title: every card and "
            "relayed question uses them"
        )
    return args


async def main(args: argparse.Namespace) -> None:
    async with AsyncSessionLocal() as db:
        if args.show:
            rows = (
                (
                    await db.execute(
                        select(UserProfileModel).order_by(UserProfileModel.created_at)
                    )
                )
                .scalars()
                .all()
            )
            for p in rows:
                family = await family_of(p.user_id, db)
                on = "on" if p.conversation_cards_enabled else "off"
                print(
                    f"{p.user_id}  cards={on}"
                    f"  share={p.share_preference}"
                    f"  child={family.child_name if family else None}"
                    f"  title={family.parent_title if family else None}"
                    f"  tz={family.timezone if family else None}"
                    f"  slots={family.call_slots if family else None}"
                )
            return

        profile = (
            await db.execute(
                select(UserProfileModel).where(UserProfileModel.user_id == args.user_id)
            )
        ).scalar_one_or_none()
        if profile is None:
            raise SystemExit(f"No profile for {args.user_id}")
        family = await family_of(args.user_id, db)
        if family is None:
            raise SystemExit(f"No family account for {args.user_id}")

        changes = []
        if args.enable or args.disable:
            changes.append(
                ("cards", profile.conversation_cards_enabled, bool(args.enable))
            )
            profile.conversation_cards_enabled = bool(args.enable)
        for name, value in (
            ("child_name", args.child_name),
            ("parent_title", args.parent_title),
            ("timezone", args.timezone),
            ("call_slots", args.slot),
        ):
            if value is not None:
                changes.append((name, getattr(family, name), value))
                setattr(family, name, value)

        for name, old, new in changes:
            print(f"{args.user_id}: {name}: {old!r} -> {new!r}")
        if not changes:
            print("Nothing to change.")
            return
        if not args.apply:
            await db.rollback()
            print("Dry run: nothing written. Add --apply to make these changes.")
            return
        await db.commit()
        print("Written.")


if __name__ == "__main__":
    asyncio.run(main(parse()))
