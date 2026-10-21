"""
Put a family on a plan (PRE_PILOT_FIX_PLAN P8, D7).

Pilot families get `pilot`: no limit on conversations. On `free`, the 11th
conversation is refused; she then hears, once a week at most, that her
conversations are paused and that her family will be in touch, and the
family is emailed the upgrade prompt. Run this for every pilot family
before her first message (PILOT_RUNBOOK.md, before the family).

Dry run by default: it prints what it would change. Pass --apply to write.

Usage (from backend/):
    python -m scripts.set_plan --user-id user_abc --plan pilot --by Krish --apply
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from models.db import AsyncSessionLocal  # noqa: E402
from models.family_account import FamilyAccount  # noqa: E402

PLANS = ("free", "pilot", "paid")

logger = logging.getLogger(__name__)


async def set_plan(user_id: str, plan: str, by: str, db, *, apply: bool) -> str:
    """Returns a one-line account of what was, or would be, done."""
    if plan not in PLANS:
        return f"REFUSED  plan must be one of {', '.join(PLANS)}"
    if not by.strip():
        return "REFUSED  --by must name who is making the change"
    account = (
        await db.execute(select(FamilyAccount).where(FamilyAccount.user_id == user_id))
    ).scalar_one_or_none()
    if account is None:
        return f"REFUSED  no family account for {user_id}"
    if account.plan == plan:
        return f"UNCHANGED  {user_id} is already on {plan}"
    summary = f"{user_id}: {account.plan} -> {plan}, by {by.strip()}"
    if not apply:
        return f"DRY RUN  {summary}"
    account.plan = plan
    await db.commit()
    logger.info("Plan changed: %s", summary)
    return f"CHANGED  {summary}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--plan", required=True, choices=PLANS)
    parser.add_argument("--by", required=True, help="who is making the change")
    parser.add_argument("--apply", action="store_true", help="write the change")
    args = parser.parse_args()
    async with AsyncSessionLocal() as db:
        outcome = await set_plan(args.user_id, args.plan, args.by, db, apply=args.apply)
    print(outcome)
    return 1 if outcome.startswith("REFUSED") else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
