from __future__ import annotations

import asyncio
import logging

import boto3

from config import settings

logger = logging.getLogger(__name__)


def _s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


async def upload_media(
    data: bytes,
    key: str,
    content_type: str = "audio/ogg",
) -> str:
    """
    Upload bytes to S3 under the given key (e.g. "audio/{filename}.ogg" or
    "cards/{session_id}.png"). Private by default — this holds verbatim
    quotes and voice audio of an elderly person's life history, which must
    never sit at a permanently public URL. Callers that need to hand a URL
    to a third party (Twilio) should call generate_presigned_url.
    Returns the S3 key (not a URL) — callers must track it themselves so
    the object can be found and deleted later.
    Files are stored in ap-south-1 (Mumbai) for DPDP Act compliance.
    """
    client = _s3_client()
    await asyncio.to_thread(
        client.put_object,
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
    )
    logger.info("Uploaded %s (%d bytes)", key, len(data))
    return key


async def generate_presigned_url(key: str, expires_in: int = 900) -> str:
    """
    Short-lived signed URL for a private object (default 15 minutes) — for
    handing to Twilio to fetch once, or to the family dashboard for display.
    Never store this URL; generate it fresh on each use.
    """
    client = _s3_client()
    return await asyncio.to_thread(
        client.generate_presigned_url,
        "get_object",
        Params={"Bucket": settings.AWS_S3_BUCKET, "Key": key},
        ExpiresIn=expires_in,
    )


async def delete_media(key: str) -> None:
    """Delete a media file from S3 after delivery."""
    client = _s3_client()
    await asyncio.to_thread(
        client.delete_object, Bucket=settings.AWS_S3_BUCKET, Key=key
    )
    logger.info("Deleted %s from S3", key)
