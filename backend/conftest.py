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


@pytest.fixture(autouse=True)
def no_real_s3(request, monkeypatch):
    """
    No test reaches AWS. backend/.env holds real credentials, and a test
    that forgot to stub storage would otherwise create a real S3 client
    with them (found 2026-10-19: card review tests deleting a made-up key
    in the real bucket). Any such call now fails loudly instead. Tests
    stub upload_media, delete_media and generate_presigned_url themselves;
    a test that needs the real client with dummy credentials (presigning
    is local) marks itself real_s3_client.
    """
    if request.node.get_closest_marker("real_s3_client"):
        return

    def refuse(*args, **kwargs):
        raise RuntimeError("tests must not create a real S3 client")

    monkeypatch.setattr("media.storage._s3_client", refuse)


@pytest.fixture(autouse=True)
def media_objects(request, monkeypatch):
    """
    Record media/storage's object tracking in memory (P6), like the usage
    ledger above: a unit test must not open a real DB connection to write
    it. A test that needs the real write marks itself real_media_tracking.
    Yields the list of keys recorded.
    """
    if request.node.get_closest_marker("real_media_tracking"):
        yield None
        return

    from core import usage

    keys: list[tuple[str, str]] = []

    async def _track(key):
        user_id = usage.current_scope().get("user_id")
        if user_id and not key.startswith("system/"):
            keys.append((user_id, key))

    monkeypatch.setattr("media.storage._track", _track)
    yield keys


@pytest.fixture(autouse=True)
def twilio_queue(request, monkeypatch):
    """
    Capture core/twilio_cleanup's queue and deletes in memory (P6): unit
    tests must not write the queue to a real database or call Twilio. A
    test that needs the real thing marks itself real_twilio_queue. Yields
    (sid, "inbound" | "outbound" | "deleted") in order.
    """
    if request.node.get_closest_marker("real_twilio_queue"):
        yield None
        return

    queued: list[tuple[str, str]] = []

    async def _queue(message_sid, *, direction, user_id=None, delay=None):
        if message_sid:
            queued.append((message_sid, direction))

    async def _delete_now(message_sid, whatsapp=None):
        if message_sid:
            queued.append((message_sid, "deleted"))
        return True

    monkeypatch.setattr("core.twilio_cleanup.queue", _queue)
    monkeypatch.setattr("core.twilio_cleanup.delete_now", _delete_now)
    yield queued


@pytest.fixture(autouse=True)
def no_real_gemini(monkeypatch):
    """
    No test reaches Google with the developer's real key from backend/.env.
    New parents default to Gemini, so an unmocked speech path would
    otherwise make a live, billed call. With the key blank, such a call
    fails before any request and media/tts.py falls back to Sarvam, which
    the test mocks or which fails just the same. Tests of the Gemini
    adapter set their own key.
    """
    monkeypatch.setattr("config.settings.GEMINI_API_KEY", "")


@pytest.fixture(autouse=True)
def system_speech_store(monkeypatch):
    """
    Keep the system-speech cache in memory for every test. backend/.env can
    hold real AWS keys, and a unit test must never read or write the real
    bucket. Yields the dict standing in for S3, keyed by object key.
    """
    from media import system_speech

    store: dict[str, bytes] = {}

    async def _get(key):
        return store.get(key)

    async def _put(key, data):
        store[key] = data

    monkeypatch.setattr(system_speech, "_s3_get", _get)
    monkeypatch.setattr(system_speech, "_s3_put", _put)
    system_speech.clear_memory()
    yield store
    system_speech.clear_memory()
