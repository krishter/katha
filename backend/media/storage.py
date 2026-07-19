from __future__ import annotations

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
    "cards/{session_id}.png"). ACL is public-read so Twilio can fetch it.
    Returns the public HTTPS URL.
    Files are stored in ap-south-1 (Mumbai) for DPDP Act compliance.
    """
    client = _s3_client()
    client.put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=data,
        ContentType=content_type,
        ACL="public-read",
    )
    url = (
        f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_S3_REGION}"
        f".amazonaws.com/{key}"
    )
    logger.info("Uploaded %s (%d bytes) → %s", key, len(data), url)
    return url


async def delete_media(key: str) -> None:
    """Delete a media file from S3 after delivery."""
    client = _s3_client()
    client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
    logger.info("Deleted %s from S3", key)
