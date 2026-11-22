"""
Embed the story atoms that have no vector yet, once, at cutover
(docs/specs/SEMANTIC_RETRIEVAL.md §5.5, rollout stage 3).

Sequential, with a pause between atoms sized to the Vertex quota (P3).
Resumable: it only ever picks atoms with no vector from the current model,
so stopping and running it again carries on where it left off. Runs only
with EMBEDDINGS_ENABLED true, since that is what allows text to leave for
Google; --dry-run needs neither the flag nor credentials and sends nothing.

Usage (from backend/):
    python -m scripts.backfill_embeddings --dry-run
    python -m scripts.backfill_embeddings --limit 20
    python -m scripts.backfill_embeddings --sleep 1.0
"""

from __future__ import annotations

import argparse
import asyncio
import importlib
import pathlib
import pkgutil
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

from sqlalchemy import func, or_, select  # noqa: E402

import models  # noqa: E402
from adapters import gemini_embeddings  # noqa: E402
from config import settings  # noqa: E402
from memory import vector_store  # noqa: E402
from models.db import AsyncSessionLocal  # noqa: E402
from models.story_atom import StoryAtom  # noqa: E402

# Load every model, as the app does. story_atoms points at turns, turns at
# sessions, and so on: with only StoryAtom loaded, every flush failed with
# NoReferencedTableError (found on the first production backfill).
for _module in pkgutil.iter_modules(models.__path__):
    importlib.import_module(f"models.{_module.name}")

# One request a second stays far inside any default Vertex quota. Replace
# with the figure from the P3 quota check.
_DEFAULT_SLEEP_SECONDS = 1.0


def _pending():
    return or_(
        StoryAtom.embedding.is_(None),
        StoryAtom.embedding_model.is_distinct_from(gemini_embeddings.MODEL_TAG),
    )


async def main(args: argparse.Namespace) -> int:
    async with AsyncSessionLocal() as db:
        count = select(func.count()).select_from(StoryAtom).where(_pending())
        total = (await db.execute(count)).scalar_one()
        todo = min(total, args.limit) if args.limit else total
        print(f"{total} atom(s) without a {gemini_embeddings.MODEL_TAG} vector")
        if args.dry_run:
            print(f"dry run: would embed {todo}, sending nothing")
            return 0
        if not settings.EMBEDDINGS_ENABLED:
            print("EMBEDDINGS_ENABLED is false: nothing may be sent. Set it and rerun.")
            return 1

        done = failed = 0
        while done + failed < todo:
            atom = (
                await db.execute(
                    select(StoryAtom)
                    .where(_pending())
                    .where(StoryAtom.embedding_failed.is_(False))
                    .order_by(StoryAtom.created_at.asc())
                    .limit(1)
                )
            ).scalar_one_or_none()
            if atom is None:
                break
            if await vector_store.embed_atoms([atom], db):
                done += 1
            else:
                failed += 1
            print(f"\r{done} embedded, {failed} failed", end="", flush=True)
            await asyncio.sleep(args.sleep)
        print()
        if failed:
            print(f"{failed} marked embedding_failed; the hourly sweep retries them")
        return 0


def parse() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    p.add_argument("--dry-run", action="store_true", help="count only, send nothing")
    p.add_argument("--limit", type=int, default=0, help="stop after this many atoms")
    p.add_argument(
        "--sleep",
        type=float,
        default=_DEFAULT_SLEEP_SECONDS,
        help="seconds between atoms (size from the quota)",
    )
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(asyncio.run(main(parse())))
