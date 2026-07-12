from unittest.mock import MagicMock, patch

from media.storage import upload_audio

_AUDIO = b"fake-audio-bytes"


async def test_upload_audio_calls_put_object():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        await upload_audio(_AUDIO, "test.ogg", "audio/ogg")

    mock_client.put_object.assert_called_once()
    call_kwargs = mock_client.put_object.call_args.kwargs
    assert call_kwargs["Key"] == "audio/test.ogg"
    assert call_kwargs["ContentType"] == "audio/ogg"
    assert call_kwargs["ACL"] == "public-read"
    assert call_kwargs["Body"] == _AUDIO


async def test_upload_audio_returns_public_url():
    mock_client = MagicMock()
    with patch("media.storage._s3_client", return_value=mock_client):
        url = await upload_audio(_AUDIO, "test.ogg")

    assert url.startswith("https://")
    assert "test.ogg" in url
    assert "amazonaws.com" in url
