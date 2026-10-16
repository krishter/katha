import os

# Set required env vars before any module imports config.Settings()
os.environ.setdefault("SARVAM_API_KEY", "test-sarvam-key")
os.environ.setdefault("ANTHROPIC_API_KEY", "test-anthropic-key")
os.environ.setdefault("WHATSAPP_ADAPTER", "stub")

import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def usage_rows(request, monkeypatch):
    """
    Capture usage-ledger writes in memory for every test, and hand the rows
    to any test that asks for them by name.

    Without this, every adapter call in a unit test would open a real DB
    connection to write its api_usage row — leaving rows behind, and binding
    pooled connections to the test's event loop, which is the exact failure
    tests/integration/conftest.py disposes the engine to avoid.

    A test that needs the real write (the deletion E2E) marks itself
    `@pytest.mark.real_ledger`.
    """
    if request.node.get_closest_marker("real_ledger"):
        yield None
        return

    from core import usage

    rows: list[dict] = []

    async def _write(row):
        rows.append(dict(row))

    async def _inbound_already_recorded(message_sid):
        return any(
            r["message_sid"] == message_sid and r["operation"] == "inbound"
            for r in rows
        )

    async def _set_status(message_sid, status, error_code):
        updated = 0
        for r in rows:
            if r["message_sid"] == message_sid and r["operation"] != "inbound":
                r["status"] = status
                r["error_code"] = error_code
                updated += 1
        return updated

    monkeypatch.setattr(usage, "_write", _write)
    monkeypatch.setattr(usage, "_inbound_already_recorded", _inbound_already_recorded)
    monkeypatch.setattr(usage, "_set_status", _set_status)
    yield rows
