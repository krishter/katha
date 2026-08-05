"""
One-off script (REMEDIATION_PLAN WS3.2): purge audio/ objects in the S3
bucket that predate response_audio_s3_key tracking and are therefore
untracked in the database — the voice notes sent before that column
existed, uploaded with a public-read ACL under a key never recorded
anywhere.

Lists every object under the audio/ prefix, compares against every key
currently recorded in turns.response_audio_s3_key, and deletes whatever
isn't referenced. Defaults to a dry run — pass --yes to actually delete.

Usage:
    python -m scripts.purge_orphaned_audio            # dry run, lists only
    python -m scripts.purge_orphaned_audio --yes      # actually deletes
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import boto3
from sqlalchemy import select

from config import settings
from models.db import AsyncSessionLocal
from models.turn import Turn

logger = logging.getLogger(__name__)


def _list_audio_keys() -> set[str]:
    client = boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    keys: set[str] = set()
    paginator = client.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=settings.AWS_S3_BUCKET, Prefix="audio/"):
        for obj in page.get("Contents", []):
            keys.add(obj["Key"])
    return keys


async def _tracked_audio_keys() -> set[str]:
    async with AsyncSessionLocal() as db:
        result = await db.execute(
            select(Turn.response_audio_s3_key).where(
                Turn.response_audio_s3_key.is_not(None)
            )
        )
        return set(result.scalars().all())


def _delete_keys(keys: set[str]) -> None:
    client = boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )
    for key in sorted(keys):
        client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
        logger.info("Deleted orphaned object: %s", key)


async def main(apply: bool) -> None:
    bucket_keys = _list_audio_keys()
    tracked_keys = await _tracked_audio_keys()
    orphaned = bucket_keys - tracked_keys

    print(f"audio/ objects in bucket: {len(bucket_keys)}")
    print(f"tracked in turns.response_audio_s3_key: {len(tracked_keys)}")
    print(f"orphaned (untracked): {len(orphaned)}")
    for key in sorted(orphaned):
        print(f"  {key}")

    if not orphaned:
        print("Nothing to delete.")
        return

    if not apply:
        print("\nDry run — no objects deleted. Re-run with --yes to delete.")
        return

    _delete_keys(orphaned)
    print(f"\nDeleted {len(orphaned)} orphaned object(s).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Actually delete orphaned objects (default is a dry run).",
    )
    args = parser.parse_args()
    asyncio.run(main(apply=args.yes))
