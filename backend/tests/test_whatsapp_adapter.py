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


async def test_stub_send_image_returns_fake_sid():
    adapter = StubWhatsAppAdapter()
    sid = await adapter.send_image(_TO, "cards/abc.png", caption="A memory")
    assert sid.startswith("STUB_MSG_")


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


async def test_twilio_send_image_presigns_without_uploading():
    """S2.4b: send_image takes a key the caller already uploaded. It must
    never upload a second copy — that copy was untracked and survived
    DELETE /user/{user_id}."""
    from adapters.whatsapp import TwilioWhatsAppAdapter

    mock_msg = MagicMock()
    mock_msg.sid = "SM_IMG_789"

    mock_twilio = MagicMock()
    mock_twilio.messages.create.return_value = mock_msg

    with (
        patch("adapters.whatsapp.TwilioClient", return_value=mock_twilio),
        patch(
            "adapters.whatsapp.storage.upload_media",
            new=AsyncMock(return_value="cards/should-never-happen.png"),
        ) as mock_upload,
        patch(
            "adapters.whatsapp.storage.generate_presigned_url",
            new=AsyncMock(return_value="https://signed.example/cards/test.png"),
        ) as mock_presign,
    ):
        adapter = TwilioWhatsAppAdapter("ACtest", "authtest", "whatsapp:+14155238886")
        sid = await adapter.send_image(_TO, "cards/test.png", caption="A memory")

    mock_upload.assert_not_called()
    mock_presign.assert_called_once_with("cards/test.png")
    call_kwargs = mock_twilio.messages.create.call_args.kwargs
    assert call_kwargs["to"] == f"whatsapp:{_TO}"
    assert call_kwargs["body"] == "A memory"
    assert call_kwargs["media_url"] == ["https://signed.example/cards/test.png"]
    assert sid == "SM_IMG_789"


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


# ── S3.5: defects found while provisioning the production sender ─────────────
#
# Neither was visible to the tests above, which assert against a mocked
# Twilio client that accepts anything at all.


def _twilio_adapter(messaging_service_sid=""):
    from adapters.whatsapp import TwilioWhatsAppAdapter

    mock_msg = MagicMock()
    mock_msg.sid = "SM_TEST"
    mock_twilio = MagicMock()
    mock_twilio.messages.create.return_value = mock_msg
    patcher = patch("adapters.whatsapp.TwilioClient", return_value=mock_twilio)
    patcher.start()
    adapter = TwilioWhatsAppAdapter(
        "ACtest",
        "authtest",
        "whatsapp:+14155238886",
        messaging_service_sid=messaging_service_sid,
    )
    patcher.stop()
    return adapter, mock_twilio


async def test_content_variables_is_json_not_a_python_repr():
    """str() on a dict gives {'1': 'Lakshmi'} — single quotes, not JSON —
    which Twilio does not parse, so the template either fails outright or
    substitutes nothing. Asserted by parsing rather than by comparing to a
    fixed string, which would just re-encode the same assumption."""
    import json

    adapter, mock_twilio = _twilio_adapter()

    await adapter.send_text(
        _TO, "", template_sid="HXtest", template_variables={"1": "Lakshmi"}
    )

    sent = mock_twilio.messages.create.call_args.kwargs["content_variables"]
    assert isinstance(sent, str)
    assert json.loads(sent) == {"1": "Lakshmi"}


async def test_sends_use_messaging_service_when_configured():
    """The production sender sits in a Messaging Service. Passing the bare
    number sends from outside it, losing its sender pool and compliance
    settings."""
    adapter, mock_twilio = _twilio_adapter(messaging_service_sid="MGtest")

    with (
        patch(
            "adapters.whatsapp.storage.upload_media",
            new=AsyncMock(return_value="audio/x.ogg"),
        ),
        patch(
            "adapters.whatsapp.storage.generate_presigned_url",
            new=AsyncMock(return_value="https://signed.example/x"),
        ),
    ):
        await adapter.send_voice_note(_TO, b"audio")
        await adapter.send_image(_TO, "cards/x.png", caption="hi")
        await adapter.send_text(_TO, "hello")

    assert mock_twilio.messages.create.call_count == 3
    for call in mock_twilio.messages.create.call_args_list:
        assert call.kwargs.get("messaging_service_sid") == "MGtest"
        assert "from_" not in call.kwargs, (
            "from_ alongside messaging_service_sid sends from outside the service"
        )


async def test_sends_fall_back_to_the_number_without_a_messaging_service():
    """Unconfigured, the sandbox number must keep working."""
    adapter, mock_twilio = _twilio_adapter(messaging_service_sid="")

    await adapter.send_text(_TO, "hello")

    kwargs = mock_twilio.messages.create.call_args.kwargs
    assert kwargs.get("from_") == "whatsapp:+14155238886"
    assert "messaging_service_sid" not in kwargs
