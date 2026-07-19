from __future__ import annotations

import logging

import boto3

from config import settings

logger = logging.getLogger(__name__)

_KEY_PREFIX = "audio"


def _s3_client():
    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
    )


async def upload_audio(
    audio_bytes: bytes,
    filename: str,
    content_type: str = "audio/ogg",
) -> str:
    """
    Upload audio bytes to S3 under katha-media/audio/{filename}.
    ACL is public-read so Twilio can fetch it.
    Returns the public HTTPS URL.
    Files are stored in ap-south-1 (Mumbai) for DPDP Act compliance.
    """
    key = f"{_KEY_PREFIX}/{filename}"
    client = _s3_client()
    client.put_object(
        Bucket=settings.AWS_S3_BUCKET,
        Key=key,
        Body=audio_bytes,
        ContentType=content_type,
        ACL="public-read",
    )
    url = (
        f"https://{settings.AWS_S3_BUCKET}.s3.{settings.AWS_S3_REGION}"
        f".amazonaws.com/{key}"
    )
    logger.info("Uploaded %s (%d bytes) → %s", filename, len(audio_bytes), url)
    return url


async def delete_audio(filename: str) -> None:
    """Delete an audio file from S3 after delivery."""
    key = f"{_KEY_PREFIX}/{filename}"
    client = _s3_client()
    client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
    logger.info("Deleted %s from S3", key)
