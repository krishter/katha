"""
How one family's conversations are going, in numbers only.

    python -m scripts.session_health --user-id user_abc            # last 10
    python -m scripts.session_health --user-id user_abc --last 30

No one at Katha reads what a parent says (privacy page; parent consent
1.2), so the pilot checks that used to mean reading a transcript are
answered from counts (PILOT_RUNBOOK.md, "Reading what she said"). Per
session: turns, how long her messages were, whether the language heard was
hers, story atoms and their 5W completeness, private turns, failures, how
it ended. Then the Layer 3 check: how many life domains her atoms cover,
and how many of them the 5 most recent (what Layer 3 receives) still do.

It prints no transcript, reply, atom, title, name or place. It reads only.
"""

from __future__ import annotations

import argparse
import asyncio
import pathlib
import sys
from collections import Counter

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, select  # noqa: E402

from models.db import AsyncSessionLocal  # noqa: E402
from models.operator_alert import TurnFailure  # noqa: E402
from models.session import Session  # noqa: E402
from models.story_atom import StoryAtom  # noqa: E402
from models.turn import Turn  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402

# Layer 3 takes this many atoms, newest first (memory/vector_store.py).
LAYER3_TOP_K = 5


def _lang(code: str | None) -> str:
    return (code or "").split("-")[0].lower()


async def report(user_id: str, last: int, db) -> list[str]:
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        return [f"No profile for {user_id}"]
    preferred = profile.preferred_language

    sessions = (
        (
            await db.execute(
                select(Session)
                .where(Session.user_id == user_id)
                .order_by(Session.session_number.desc())
                .limit(last)
            )
        )
        .scalars()
        .all()
    )
    lines = [
        f"{user_id} · language {preferred} · last {len(sessions)} session(s)",
        "",
        f"{'#':>3} {'date':10} {'turns':>5} {'avg chars':>9} {'own lang':>8} "
        f"{'atoms':>5} {'avg 5W':>6} {'private':>7}  ended",
    ]
    for s in reversed(sessions):
        turns = (
            await db.execute(
                select(
                    func.length(Turn.transcript),
                    Turn.detected_language,
                    Turn.is_private,
                ).where(Turn.session_id == s.id)
            )
        ).all()
        atoms = (
            await db.execute(
                select(StoryAtom.completeness_score).where(StoryAtom.session_id == s.id)
            )
        ).all()
        n = len(turns)
        avg_chars = sum(t[0] or 0 for t in turns) / n if n else 0
        own = sum(1 for t in turns if _lang(t[1]) == _lang(preferred))
        private = sum(1 for t in turns if t[2])
        avg_5w = sum(a[0] or 0 for a in atoms) / len(atoms) if atoms else 0
        when = s.started_at.date().isoformat() if s.started_at else "—"
        lines.append(
            f"{s.session_number:>3} {when:10} {n:>5} {avg_chars:>9.0f} "
            f"{own:>4}/{n:<3} {len(atoms):>5} {avg_5w:>6.1f} {private:>7}  "
            f"{s.ended_reason or s.status}"
        )

    failures = (
        await db.execute(
            select(TurnFailure.stage, func.count())
            .where(TurnFailure.user_id == user_id)
            .group_by(TurnFailure.stage)
        )
    ).all()
    lines += [
        "",
        "Turn failures (all time): "
        + (", ".join(f"{stage} {count}" for stage, count in failures) or "none"),
    ]

    domains = (
        await db.execute(
            select(StoryAtom.domain)
            .where(StoryAtom.user_id == user_id)
            .order_by(StoryAtom.created_at.desc())
        )
    ).scalars()
    domains = list(domains)
    all_domains = Counter(domains)
    recent = set(domains[:LAYER3_TOP_K])
    dropped = sorted(set(all_domains) - recent)
    lines += [
        f"Story atoms: {len(domains)} across {len(all_domains)} domain(s): "
        + ", ".join(f"{d} {c}" for d, c in sorted(all_domains.items())),
        f"Layer 3 (newest {LAYER3_TOP_K} atoms) covers {len(recent)} domain(s). "
        + (
            f"Dropped from Layer 3: {', '.join(dropped)}. That is T5's trigger."
            if dropped
            else "Nothing dropped yet."
        ),
    ]
    return lines


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--last", type=int, default=10, help="sessions to show")
    args = parser.parse_args()
    async with AsyncSessionLocal() as db:
        lines = await report(args.user_id, args.last, db)
        await db.rollback()
    print("\n".join(lines))
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
