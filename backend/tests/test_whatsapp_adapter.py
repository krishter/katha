from unittest.mock import AsyncMock, MagicMock, patch

from adapters.whatsapp_stub import StubWhatsAppAdapter, get_whatsapp_adapter

_TO = "+919876543210"
_AUDIO = b"fake-ogg-audio"
_URL = "https://api.twilio.com/media/MSG123/0"


# ── StubWhatsAppAdapter ────────────────────────────────────────────────────────


async def test_stub_send_voice_note_returns_fake_sid_and_key():
    adapter = StubWhatsAppAdapter()
    sid, s3_key = await adapter.send_voice_note(_TO, _AUDIO)
    assert sid.startswith("STUB_MSG_")
    assert s3_key.startswith("audio/")


async def test_stub_send_image_returns_fake_sid_and_key():
    adapter = StubWhatsAppAdapter()
    sid, s3_key = await adapter.send_image(_TO, b"fake-png-bytes", caption="A memory")
    assert sid.startswith("STUB_MSG_")
    assert s3_key.startswith("cards/")


async def test_stub_send_text_returns_fake_sid():
    adapter = StubWhatsAppAdapter()
    sid = await adapter.send_text(_TO, "Hello Subramaniam!")
    assert sid.startswith("STUB_MSG_")


async def test_stub_download_returns_bytes():
    adapter = StubWhatsAppAdapter()
    data = await adapter.download_voice_note(_URL)
    assert isinstance(data, bytes)
    assert len(data) > 0


async def test_stub_validate_signature_always_true():
    adapter = StubWhatsAppAdapter()
    result = adapter.validate_signature("https://example.com", {}, "bad-sig")
    assert result is True


# ── TwilioWhatsAppAdapter ──────────────────────────────────────────────────────


async def test_twilio_send_voice_note_uploads_then_sends():
    from adapters.whatsapp import TwilioWhatsAppAdapter

    mock_msg = MagicMock()
    mock_msg.sid = "SM_TEST_123"

    mock_twilio = MagicMock()
    mock_twilio.messages.create.return_value = mock_msg

    with (
        patch("adapters.whatsapp.TwilioClient", return_value=mock_twilio),
        patch(
            "adapters.whatsapp.storage.upload_media",
            new=AsyncMock(return_value="audio/test.ogg"),
        ) as mock_upload,
        patch(
            "adapters.whatsapp.storage.generate_presigned_url",
            new=AsyncMock(return_value="https://signed.example/audio/test.ogg"),
        ) as mock_presign,
    ):
        adapter = TwilioWhatsAppAdapter("ACtest", "authtest", "whatsapp:+14155238886")
        sid, s3_key = await adapter.send_voice_note(_TO, _AUDIO)

    mock_upload.assert_called_once()
    mock_presign.assert_called_once_with("audio/test.ogg")
    mock_twilio.messages.create.assert_called_once()
    call_kwargs = mock_twilio.messages.create.call_args.kwargs
    assert call_kwargs["to"] == f"whatsapp:{_TO}"
    assert call_kwargs["media_url"] == ["https://signed.example/audio/test.ogg"]
    assert sid == "SM_TEST_123"
    assert s3_key == "audio/test.ogg"


async def test_twilio_send_image_uploads_then_sends():
    from adapters.whatsapp import TwilioWhatsAppAdapter

    mock_msg = MagicMock()
    mock_msg.sid = "SM_IMG_789"

    mock_twilio = MagicMock()
    mock_twilio.messages.create.return_value = mock_msg

    with (
        patch("adapters.whatsapp.TwilioClient", return_value=mock_twilio),
        patch(
            "adapters.whatsapp.storage.upload_media",
            new=AsyncMock(return_value="cards/test.png"),
        ) as mock_upload,
        patch(
            "adapters.whatsapp.storage.generate_presigned_url",
            new=AsyncMock(return_value="https://signed.example/cards/test.png"),
        ),
    ):
        adapter = TwilioWhatsAppAdapter("ACtest", "authtest", "whatsapp:+14155238886")
        sid, s3_key = await adapter.send_image(
            _TO, b"fake-png-bytes", caption="A memory"
        )

    mock_upload.assert_called_once()
    upload_kwargs = mock_upload.call_args.args
    assert upload_kwargs[2] == "image/png"
    call_kwargs = mock_twilio.messages.create.call_args.kwargs
    assert call_kwargs["to"] == f"whatsapp:{_TO}"
    assert call_kwargs["body"] == "A memory"
    assert sid == "SM_IMG_789"
    assert s3_key == "cards/test.png"


async def test_twilio_send_text_correct_format():
    from adapters.whatsapp import TwilioWhatsAppAdapter

    mock_msg = MagicMock()
    mock_msg.sid = "SM_TEXT_456"
    mock_twilio = MagicMock()
    mock_twilio.messages.create.return_value = mock_msg

    with patch("adapters.whatsapp.TwilioClient", return_value=mock_twilio):
        adapter = TwilioWhatsAppAdapter("ACtest", "authtest", "whatsapp:+14155238886")
        sid = await adapter.send_text(_TO, "Hello!")

    call_kwargs = mock_twilio.messages.create.call_args.kwargs
    assert call_kwargs["from_"] == "whatsapp:+14155238886"
    assert call_kwargs["to"] == f"whatsapp:{_TO}"
    assert call_kwargs["body"] == "Hello!"
    assert sid == "SM_TEXT_456"


def test_twilio_validate_signature_calls_request_validator():
    from adapters.whatsapp import TwilioWhatsAppAdapter

    mock_twilio = MagicMock()
    with (
        patch("adapters.whatsapp.TwilioClient", return_value=mock_twilio),
        patch("adapters.whatsapp.RequestValidator") as mock_validator_cls,
    ):
        mock_validator = MagicMock()
        mock_validator.validate.return_value = True
        mock_validator_cls.return_value = mock_validator

        adapter = TwilioWhatsAppAdapter("ACtest", "authtest", "whatsapp:+14155238886")
        result = adapter.validate_signature("https://example.com/webhook", {}, "SIG")

    mock_validator.validate.assert_called_once_with(
        "https://example.com/webhook", {}, "SIG"
    )
    assert result is True


# ── get_whatsapp_adapter factory ───────────────────────────────────────────────


def test_get_whatsapp_adapter_returns_stub_when_configured():
    with patch("config.settings") as mock_settings:
        mock_settings.WHATSAPP_ADAPTER = "stub"
        adapter = get_whatsapp_adapter()
    assert isinstance(adapter, StubWhatsAppAdapter)
