from fastapi.testclient import TestClient

from main import app

client = TestClient(app)


def test_conversation_session_route_is_gone():
    """
    /conversation/* was unauthenticated Phase-1 scaffolding that accepted a
    caller-supplied user_id and session_id (C6) — deleted entirely per
    REMEDIATION_PLAN WS3.1. WhatsApp is the only production surface.
    """
    response = client.post("/conversation/session", data={"user_id": "anyone"})
    assert response.status_code == 404


def test_conversation_turn_route_is_gone():
    response = client.post("/conversation/turn", data={"session_id": "anything"})
    assert response.status_code == 404


def test_conversation_close_route_is_gone():
    response = client.post("/conversation/close", data={"session_id": "anything"})
    assert response.status_code == 404
