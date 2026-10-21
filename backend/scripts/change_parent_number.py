"""
Move a parent to a new WhatsApp number, deliberately, and record it
(docs/PRE_PILOT_FIX_PLAN.md P3 3.4, M-03).

Katha knows her only by her number, and her consent was given from it. So
after she has agreed, the family cannot change the number in the setup
form (it returns 409); an operator does it here, having spoken to the
family. The change:

  - writes a "parent-1.0-reset" consent record, naming the old number and
    who made the change, so the audit trail shows why her grant stopped
    counting;
  - moves the profile to the new number;
  - leaves her unconsented: the new number's first message gets the
    welcome and the question, as on day one. Whoever holds the new number
    has agreed to nothing yet.

Dry run by default: it prints what it would change. Pass --apply to write.

Usage (from backend/):
    python -m scripts.change_parent_number --user-id user_abc \\
        --new +919876543210 --by Krish --apply
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core import parent_consent  # noqa: E402
from models.consent_record import ConsentRecord  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402

_E164_RE = re.compile(r"^\+[1-9]\d{7,14}$")


class ChangeRefused(Exception):
    pass


async def change_number(
    user_id: str, new_number: str, by: str, db, *, apply: bool
) -> str:
    """Move the profile to `new_number`. Returns a one-line account of what
    was (or would be) done; raises ChangeRefused if it cannot be done."""
    if not _E164_RE.match(new_number):
        raise ChangeRefused(f"{new_number!r} is not E.164, e.g. +919876543210")
    if not by.strip():
        raise ChangeRefused("--by must name who is making the change")

    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        raise ChangeRefused(f"no profile for {user_id}")
    old_number = profile.whatsapp_number
    if old_number == new_number:
        raise ChangeRefused(f"{user_id} already has {new_number}")

    holder = (
        await db.execute(
            select(UserProfileModel.user_id).where(
                UserProfileModel.whatsapp_number == new_number
            )
        )
    ).scalar_one_or_none()
    if holder is not None:
        raise ChangeRefused(f"{new_number} already belongs to {holder}")

    summary = f"{user_id}: {old_number} -> {new_number}, consent reset, by {by.strip()}"
    if not apply:
        return f"DRY RUN  {summary}"

    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="",
            consent_version=parent_consent.RESET_VERSION,
            principal=parent_consent.PRINCIPAL_PARENT,
            # The number whose answers this record retires.
            whatsapp_number=old_number,
            # No request made this record; the column carries who did.
            user_agent=f"scripts/change_parent_number.py by {by.strip()}",
        )
    )
    profile.whatsapp_number = new_number
    profile.parent_consent_asks = 0
    profile.parent_consent_state = parent_consent.STATE_ASKING
    await db.commit()
    return f"CHANGED  {summary}"


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--new", required=True, help="the new number, E.164")
    parser.add_argument("--by", required=True, help="who is making the change")
    parser.add_argument("--apply", action="store_true", help="write the change")
    args = parser.parse_args()

    async with AsyncSessionLocal() as db:
        try:
            print(
                await change_number(
                    args.user_id, args.new, args.by, db, apply=args.apply
                )
            )
        except ChangeRefused as exc:
            print(f"REFUSED  {exc}")
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
