"""
Erase a family, or clear a withdrawal's hold (PRE_PILOT_FIX_PLAN P4).

The same sweep the dashboard's delete button and the withdrawal hold run
(core/erasure.py). For an operator: a parent who asked someone to erase
her, a family who cannot sign in, or a declined profile the family wants
cleared so they can set Katha up again.

Dry run by default: it prints what it would remove. Pass --apply to erase.

Usage (from backend/):
    # What would go
    python -m scripts.erase_user --user-id user_abc --by Krish

    # Erase now
    python -m scripts.erase_user --user-id user_abc --by Krish --apply

    # She withdrew, then told you herself she did not mean it: keep her
    # data. Her consent stays withdrawn; Katha stays silent until she
    # agrees again through a fresh setup.
    python -m scripts.erase_user --user-id user_abc --by Krish --cancel-hold
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from core import erasure  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.memory_card import MemoryCard  # noqa: E402
from models.session import Session  # noqa: E402
from models.story_atom import StoryAtom  # noqa: E402
from models.turn import Turn  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402


async def _summary(user_id: str, db) -> dict[str, int]:
    counts = {}
    for label, model in (
        ("sessions", Session),
        ("turns", Turn),
        ("story_atoms", StoryAtom),
        ("memory_cards", MemoryCard),
    ):
        counts[label] = (
            await db.execute(
                select(func.count()).select_from(model).where(model.user_id == user_id)
            )
        ).scalar_one()
    return counts


async def run(
    user_id: str, by: str, db, *, apply: bool = False, cancel_hold: bool = False
) -> str:
    """Do what the flags ask. Returns a one-line account."""
    if not by.strip():
        return "REFUSED  --by must name who is doing this"
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()

    if cancel_hold:
        if profile is None or profile.erase_after is None:
            return f"REFUSED  {user_id} has no erasure hold to clear"
        profile.erase_after = None
        await db.commit()
        return f"CLEARED  erasure hold for {user_id}, by {by.strip()}"

    if profile is None:
        return f"REFUSED  no profile for {user_id}"
    summary = await _summary(user_id, db)
    if not apply:
        return f"DRY RUN  would erase {user_id} ({profile.name}): {summary}"

    report = await erasure.erase_user(user_id, db, actor=f"operator:{by.strip()}")
    status = "ERASED " if report.complete else "PARTIAL"
    detail = f" failures={report.failures}" if report.failures else ""
    return f"{status}  {user_id}: {report.counts}{detail}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--by", required=True, help="who is doing this")
    parser.add_argument("--apply", action="store_true", help="erase now")
    parser.add_argument(
        "--cancel-hold",
        action="store_true",
        help="clear a withdrawal's erasure hold instead of erasing",
    )
    args = parser.parse_args()
    async with AsyncSessionLocal() as db:
        outcome = await run(
            args.user_id,
            args.by,
            db,
            apply=args.apply,
            cancel_hold=args.cancel_hold,
        )
    print(outcome)
    return 0 if not outcome.startswith(("REFUSED", "PARTIAL")) else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
