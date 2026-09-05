import asyncio
import time
from unittest.mock import MagicMock, patch
from urllib.parse import urlparse

from media.storage import delete_media, generate_presigned_url, upload_media

_AUDIO = b"fake-audio-bytes"
_PNG = b"fake-png-bytes"


async def test_upload_media_calls_put_object():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        await upload_media(_AUDIO, "audio/test.ogg", "audio/ogg")

    mock_client.put_object.assert_called_once()
    call_kwargs = mock_client.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "audio/test.ogg"
    assert call_kwargs["ContentType"] == "audio/ogg"
    assert call_kwargs["Body"] == _AUDIO


async def test_upload_media_does_not_set_public_acl():
    """Objects must be private by default — no ACL kwarg at all (C7)."""
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        await upload_media(_AUDIO, "audio/test.ogg")

    call_kwargs = mock_client.put_object.call_args.kwargs
    assert "ACL" not in call_kwargs


async def test_upload_media_returns_the_key_not_a_url():
    """Callers must track the returned key to find/delete the object later —
    a permanent public URL is no longer handed back at all."""
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        result = await upload_media(_AUDIO, "audio/test.ogg")

    assert result == "audio/test.ogg"


async def test_upload_media_supports_image_content_type():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        result = await upload_media(_PNG, "cards/session-1.png", "image/png")

    call_kwargs = mock_client.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "cards/session-1.png"
    assert call_kwargs["ContentType"] == "image/png"
    assert result == "cards/session-1.png"


async def test_generate_presigned_url_calls_boto_with_expiry():
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://signed.example/audio/x"
    with patch("media.storage._s3_client", return_value=mock_client):
        url = await generate_presigned_url("audio/x.ogg", expires_in=900)

    assert url == "https://signed.example/audio/x"
    call_args = mock_client.generate_presigned_url.call_args
    assert call_args.args[0] == "get_object"
    assert call_args.kwargs["Params"]["Key"] == "audio/x.ogg"
    assert call_args.kwargs["ExpiresIn"] == 900


async def test_generate_presigned_url_defaults_to_15_minutes():
    mock_client = MagicMock()
    mock_client.generate_presigned_url.return_value = "https://signed.example/x"
    with patch("media.storage._s3_client", return_value=mock_client):
        await generate_presigned_url("cards/x.png")

    assert mock_client.generate_presigned_url.call_args.kwargs["ExpiresIn"] == 900


async def test_delete_media_calls_delete_object():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        await delete_media("audio/test.ogg")

    mock_client.delete_object.assert_called_once()
    assert mock_client.delete_object.call_args.kwargs["Key"] == "audio/test.ogg"


async def test_upload_media_does_not_block_the_event_loop():
    """
    Regression for H1: a slow (blocking) boto3 call must not stall other
    coroutines. put_object here sleeps synchronously — if upload_media
    called it inline instead of via asyncio.to_thread, the concurrently
    scheduled tick() coroutine below would be delayed by the same amount.
    """
    ticks: list[float] = []

    async def tick():
        for _ in range(20):
            ticks.append(time.monotonic())
            await asyncio.sleep(0.005)

    def blocking_put_object(**kwargs):
        time.sleep(0.1)

    mock_client = MagicMock()
    mock_client.put_object.side_effect = blocking_put_object

    with patch("media.storage._s3_client", return_value=mock_client):
        await asyncio.gather(upload_media(_AUDIO, "audio/test.ogg"), tick())

    # If the event loop had been blocked for the 0.1s of the "boto3" call,
    # ticks would show a gap far larger than the 0.005s sleep between them.
    gaps = [b - a for a, b in zip(ticks, ticks[1:])]
    assert max(gaps) < 0.05


async def test_presigned_url_host_carries_the_region():
    """
    Regression for the S4.0 presign defect: every presigned URL Katha
    issued came back 403 SignatureDoesNotMatch, so Twilio could not fetch
    an outbound voice note and the family dashboard could not load audio
    or memory cards.

    Cause: under boto3's default addressing_style="auto", the presigner
    emits the legacy global host <bucket>.s3.amazonaws.com while still
    signing under the ap-south-1 credential scope, so S3 rebuilds a
    different canonical request and rejects the signature.

    This test deliberately does NOT patch _s3_client — every other test in
    this file does, which is precisely why the defect survived. Presigning
    is a local computation, so the real client can be exercised with dummy
    credentials and no network.
    """
    with patch.multiple(
        "config.settings",
        AWS_S3_REGION="ap-south-1",
        AWS_S3_BUCKET="katha-media-test",
        AWS_ACCESS_KEY_ID="AKIAIOSFODNN7EXAMPLE",
        AWS_SECRET_ACCESS_KEY="wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
    ):
        url = await generate_presigned_url("audio/x.ogg")

    host = urlparse(url).netloc
    assert host == "katha-media-test.s3.ap-south-1.amazonaws.com", (
        f"presigned host {host!r} must carry the region; the global form "
        "<bucket>.s3.amazonaws.com signs under a region the host does not "
        "name and yields 403 SignatureDoesNotMatch"
    )
    # The signature must be scoped to the same region the host resolves to.
    assert "%2Fap-south-1%2Fs3%2F" in url
