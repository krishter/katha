from unittest.mock import MagicMock, patch

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
