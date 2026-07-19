from unittest.mock import MagicMock, patch

from media.storage import upload_media

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
    assert call_kwargs["ACL"] == "public-read"
    assert call_kwargs["Body"] == _AUDIO


async def test_upload_media_returns_public_url():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        url = await upload_media(_AUDIO, "audio/test.ogg")

    assert url.startswith("https://")
    assert "audio/test.ogg" in url
    assert "amazonaws.com" in url


async def test_upload_media_supports_image_content_type():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        url = await upload_media(_PNG, "cards/session-1.png", "image/png")

    call_kwargs = mock_client.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "cards/session-1.png"
    assert call_kwargs["ContentType"] == "image/png"
    assert "cards/session-1.png" in url
