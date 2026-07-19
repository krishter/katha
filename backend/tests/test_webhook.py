import uuid
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient

from adapters.whatsapp_stub import get_whatsapp_adapter
from main import app
from models.db import get_db

_SESSION_ID = str(uuid.uuid4())
_FROM_NUMBER = "+919876543210"
_FAKE_WAV = b"RIFF" + b"\x00" * 40
_MEDIA_URL = "https://api.twilio.com/Accounts/AC/Messages/MM/Media/ME"


# Override DB dependency
async def _override_db():
    yield AsyncMock()


app.dependency_overrides[get_db] = _override_db


def _make_session(crisis=False, session_end=False):
    from core.session_manager import SessionState

    return SessionState(
        session_id=_SESSION_ID,
        user_id="user-1",
        session_number=1,
        domain="childhood",
        exchange_count=2,
        energy_signal="high",
        goal_met=False,
        session_end_suggested=session_end,
    )


def _make_turn_result(crisis=False, session_end=False):
    from core.orchestrator import TurnResult

    state = _make_session(session_end=session_end)
    state.session_end_suggested = session_end
    return TurnResult(
        response_audio=_FAKE_WAV,
        response_text="Hello!",
        extraction_json={"session_end_suggested": session_end},
        transcript="नमस्ते",
        detected_language="hi-IN",
        session_state=state,
        crisis_detected=crisis,
    )


def _make_stub(validate_ok=True):
    stub = MagicMock()
    stub.validate_signature.return_value = validate_ok
    stub.download_voice_note = AsyncMock(return_value=b"fake-ogg")
    stub.send_voice_note = AsyncMock(return_value="STUB_MSG_001")
    stub.send_text = AsyncMock(return_value="STUB_MSG_002")
    return stub


def _voice_form(from_number=None):
    return {
        "From": f"whatsapp:{from_number or _FROM_NUMBER}",
        "MediaUrl0": _MEDIA_URL,
        "MediaContentType0": "audio/ogg",
        "MessageSid": "MMtest123",
        "Body": "",
    }


def _text_form():
    return {
        "From": f"whatsapp:{_FROM_NUMBER}",
        "Body": "Hello",
        "MessageSid": "MMtext456",
    }


# ── tests ──────────────────────────────────────────────────────────────────────


def test_invalid_signature_returns_403():
    stub = _make_stub(validate_ok=False)
    app.dependency_overrides[get_whatsapp_adapter] = lambda: stub
    try:
        with patch(
            "api.routes.webhook.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=_make_session()),
        ):
            client = TestClient(app)
            response = client.post("/webhook/whatsapp", data=_voice_form())
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)

    assert response.status_code == 403


def test_voice_note_calls_process_voice_turn_and_send_voice_note():
    stub = _make_stub()
    turn = _make_turn_result()
    app.dependency_overrides[get_whatsapp_adapter] = lambda: stub
    try:
        with (
            patch(
                "api.routes.webhook.session_manager.get_active_session_by_number",
                new=AsyncMock(return_value=_make_session()),
            ),
            patch(
                "api.routes.webhook.orchestrator.process_voice_turn",
                new=AsyncMock(return_value=turn),
            ) as mock_turn,
            patch(
                "api.routes.webhook.session_manager.touch_last_message",
                new=AsyncMock(),
            ),
            patch(
                "api.routes.webhook._load_user_profile_for_session",
                new=AsyncMock(
                    return_value=MagicMock(
                        name="Subramaniam",
                        preferred_language="hi-IN",
                        onboarding_context="",
                    )
                ),
            ),
        ):
            client = TestClient(app)
            response = client.post("/webhook/whatsapp", data=_voice_form())
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)

    assert response.status_code == 200
    mock_turn.assert_called_once()
    stub.send_voice_note.assert_called_once()


def test_text_message_sends_voice_prompt():
    stub = _make_stub()
    app.dependency_overrides[get_whatsapp_adapter] = lambda: stub
    try:
        with patch(
            "api.routes.webhook.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=_make_session()),
        ):
            client = TestClient(app)
            response = client.post("/webhook/whatsapp", data=_text_form())
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)

    assert response.status_code == 200
    stub.send_text.assert_called_once()
    call_text = stub.send_text.call_args.args[1]
    assert "voice" in call_text.lower()


def test_crisis_sends_additional_text():
    stub = _make_stub()
    turn = _make_turn_result(crisis=True)
    app.dependency_overrides[get_whatsapp_adapter] = lambda: stub
    try:
        with (
            patch(
                "api.routes.webhook.session_manager.get_active_session_by_number",
                new=AsyncMock(return_value=_make_session()),
            ),
            patch(
                "api.routes.webhook.orchestrator.process_voice_turn",
                new=AsyncMock(return_value=turn),
            ),
            patch(
                "api.routes.webhook.session_manager.touch_last_message",
                new=AsyncMock(),
            ),
            patch(
                "api.routes.webhook._load_user_profile_for_session",
                new=AsyncMock(
                    return_value=MagicMock(
                        name="Subramaniam",
                        preferred_language="hi-IN",
                        onboarding_context="",
                    )
                ),
            ),
        ):
            client = TestClient(app)
            response = client.post("/webhook/whatsapp", data=_voice_form())
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)

    assert response.status_code == 200
    # send_text called with iCall number
    texts = [call.args[1] for call in stub.send_text.call_args_list]
    assert any("9152987821" in t for t in texts)


def test_returns_200_even_when_orchestrator_raises():
    stub = _make_stub()
    app.dependency_overrides[get_whatsapp_adapter] = lambda: stub
    try:
        with (
            patch(
                "api.routes.webhook.session_manager.get_active_session_by_number",
                new=AsyncMock(return_value=_make_session()),
            ),
            patch(
                "api.routes.webhook.orchestrator.process_voice_turn",
                new=AsyncMock(side_effect=RuntimeError("STT error")),
            ),
            patch(
                "api.routes.webhook._load_user_profile_for_session",
                new=AsyncMock(return_value=MagicMock()),
            ),
        ):
            client = TestClient(app)
            response = client.post("/webhook/whatsapp", data=_voice_form())
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)

    assert response.status_code == 200


def test_no_active_session_sends_not_scheduled():
    stub = _make_stub()
    app.dependency_overrides[get_whatsapp_adapter] = lambda: stub
    try:
        with patch(
            "api.routes.webhook.session_manager.get_active_session_by_number",
            new=AsyncMock(return_value=None),
        ):
            client = TestClient(app)
            response = client.post("/webhook/whatsapp", data=_voice_form())
    finally:
        app.dependency_overrides.pop(get_whatsapp_adapter, None)

    assert response.status_code == 200
    stub.send_text.assert_called_once()
    assert "scheduled" in stub.send_text.call_args.args[1].lower()
