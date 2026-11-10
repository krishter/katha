#!/usr/bin/env python
"""
Run the Stories consolidation pass over one family's past sessions (D-c).

    python scripts/backfill_stories.py --user-id user_7f3a2b           # list
    python scripts/backfill_stories.py --user-id user_7f3a2b --write   # run

Without --write it only lists the sessions it would read. With --write it
runs stories/consolidate.py over each, oldest first, exactly as after a
live session: threads and pending Stories are written, each Story waits
for the operator, and nothing is offered to her until her family's
stories switch is on. A session already consolidated is skipped, so a
second run only picks up what the first missed.

The LLM calls are real and billed (about ₹3 a session, STORIES.md §2.1).
Nothing is made for a parent who has withdrawn or declined consent.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from core import parent_consent, usage  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.session import Session  # noqa: E402
from stories.consolidate import consolidate_for_session  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("backfill_stories")


async def run(args) -> int:
    async with AsyncSessionLocal() as db:
        status = (await parent_consent.get_status(args.user_id, db)).status
        if status in (
            parent_consent.ConsentStatus.WITHDRAWN,
            parent_consent.ConsentStatus.DECLINED,
        ):
            logger.error("%s has %s: nothing is made", args.user_id, status.value)
            return 1
        sessions = (
            (
                await db.execute(
                    select(Session)
                    .where(Session.user_id == args.user_id)
                    .order_by(Session.session_number, Session.started_at)
                )
            )
            .scalars()
            .all()
        )
        # Finished conversations, closed or simply stopped: an active one is
        # still being told, and the consent handshake is not a conversation.
        sessions = [s for s in sessions if s.status in ("completed", "abandoned")]
        logger.info("%s: %d closed session(s)", args.user_id, len(sessions))
        if not args.write:
            for s in sessions:
                logger.info("  would read session %s (#%s)", s.id, s.session_number)
            logger.info("Nothing written. Pass --write to run the pass.")
            return 0
        promoted = 0
        for s in sessions:
            with usage.usage_scope(user_id=args.user_id, session_id=str(s.id)):
                outcome = await consolidate_for_session(
                    str(s.id), args.user_id, db, backfill=True
                )
            promoted += len(outcome.promoted)
            logger.info(
                "session #%s: %s, %d incident(s), %d new thread(s), %d promoted",
                s.session_number,
                outcome.result,
                outcome.incidents,
                outcome.new_threads,
                len(outcome.promoted),
            )
        logger.info("Done: %d Story(ies) waiting at /admin/stories/view", promoted)
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--user-id", required=True)
    parser.add_argument("--write", action="store_true")
    sys.exit(asyncio.run(run(parser.parse_args())))


if __name__ == "__main__":
    main()
