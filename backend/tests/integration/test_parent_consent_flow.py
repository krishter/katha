"""
S3.2/S3.3 — the parent consent conversation, end to end against real
Postgres.

The unit tests cover interpretation and wording in isolation. This covers
the thing that actually has to work: an inbound WhatsApp message from a
parent who has never agreed produces the welcome rather than a domain
session, her answer is read and recorded against her own principal with
the turn that carries it, and nothing advances until she says yes twice.

Her answers are read by the real classifier path (core/parent_answers.py)
with the model's replies replayed from
tests/fixtures/consent_classifier_replay.json, recorded by
`run_eval.py consent` with EVAL_RECORD_CONSENT=1. So the parsing, the
quoted-span rule and the decision order all run for real; only the model
call itself is replayed. A transcript with no recorded reply fails the
test rather than quietly reading as unclear.
"""

from __future__ import annotations

import json
import pathlib
import uuid
from datetime import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from core import parent_consent
from core.parent_consent import ConsentStatus
from main import app
from models.consent_record import ConsentRecord
from models.db import AsyncSessionLocal, get_db
from models.session import Session
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

_NUMBER = "+919000000777"

_REPLAY = json.loads(
    (
        pathlib.Path(__file__).resolve().parent.parent
        / "fixtures/consent_classifier_replay.json"
    ).read_text()
)["replies"]

# M-01: each was, or could have been, recorded as consent by the old word
# list. All have recorded classifier replies.
_NOT_CONSENT = [
    "Kaun ho aap?",
    "Tum kaun ho beta?",
    "I am fine, who is this?",
    "Ok, what is this about?",
    "Where do we start?",
    "Hello, kya ho raha hai?",
    "Theek hai, par yeh kya hai?",
]


async def _seed_parent(
    db, user_id: str, language: str = "ta-IN", number: str = _NUMBER
) -> UserProfileModel:
    profile = UserProfileModel(
        user_id=user_id,
        name="Lakshmi",
        whatsapp_number=number,
        preferred_language=language,
        onboarding_context="",
        family_whatsapp_number="+919000000888",
        scheduled_time=time(10, 30),
        timezone="Asia/Kolkata",
    )
    db.add(profile)
    await db.commit()
    await db.refresh(profile)
    return profile


def _stub_adapter():
    """A WhatsApp adapter that records what was said without sending."""
    adapter = MagicMock()
    adapter.validate_signature = MagicMock(return_value=True)
    adapter.send_voice_note = AsyncMock(return_value=("SM_V", "audio/x.ogg"))
    adapter.send_text = AsyncMock(return_value="SM_T")
    adapter.download_voice_note = AsyncMock(return_value=b"audio-bytes")
    return adapter


async def _post_inbound(
    client, body: str = "", with_audio: bool = False, number: str = _NUMBER
) -> None:
    data = {
        "From": f"whatsapp:{number}",
        "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
        "Body": body,
    }
    if with_audio:
        data["MediaUrl0"] = "https://example.test/audio.ogg"
        data["MediaContentType0"] = "audio/ogg"
    resp = await client.post("/webhook/whatsapp", data=data)
    assert resp.status_code == 200


def _spoken(adapter) -> str:
    """Everything Katha said, however it was delivered."""
    said = []
    for call in adapter.send_text.await_args_list:
        said.append(str(call.args[1]) if len(call.args) > 1 else "")
    return " ".join(said)


class _Harness:
    """Drives the real webhook with a per-request session, patching only
    the network edges — Twilio, Sarvam STT and Sarvam TTS."""

    def __init__(self, transcript: str = "", classifier_down: bool = False):
        self.adapter = _stub_adapter()
        self.transcript = transcript
        self.classifier_down = classifier_down
        self.spoken: list[str] = []
        self.unrecorded: list[str] = []

    async def __aenter__(self):
        async def _override_get_db():
            async with AsyncSessionLocal() as s:
                yield s

        from adapters.whatsapp_stub import get_whatsapp_adapter

        self._prev = app.dependency_overrides.get(get_db)
        self._prev_wa = app.dependency_overrides.get(get_whatsapp_adapter)
        app.dependency_overrides[get_db] = _override_get_db
        app.dependency_overrides[get_whatsapp_adapter] = lambda: self.adapter

        async def _capture_tts(text, language_code=None, **_kwargs):
            self.spoken.append(text)
            return b"wav"

        async def _replay_classifier(messages, **_kwargs):
            if self.classifier_down:
                raise RuntimeError("classifier unavailable")
            prompt = messages[0].content
            reply = prompt.split("HER REPLY\n", 1)[1].split("\n\nRespond with", 1)[0]
            if reply not in _REPLAY:
                self.unrecorded.append(reply)
                raise KeyError(reply)
            return MagicMock(
                content=_REPLAY[reply],
                input_tokens=1,
                output_tokens=1,
                stop_reason="end_turn",
            )

        self._patches = [
            patch(
                "api.routes.webhook.sarvam_stt.transcribe",
                new=AsyncMock(
                    return_value=MagicMock(
                        transcript=self.transcript, language_code="ta-IN"
                    )
                ),
            ),
            # The classifier's model call, replayed (see module docstring).
            patch("adapters.llm.chat", new=_replay_classifier),
            # Both vendors: a new parent is pinned to Gemini by default.
            patch("adapters.sarvam_tts.synthesize", new=_capture_tts),
            patch("adapters.gemini_tts.synthesize", new=_capture_tts),
            patch(
                "media.audio_convert.convert_wav_to_ogg",
                new=AsyncMock(return_value=b"ogg"),
            ),
        ]
        for p in self._patches:
            p.start()

        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        return self

    async def __aexit__(self, *exc):
        # Reverse order, so overlapping patches unwind to the real function.
        for p in reversed(self._patches):
            p.stop()
        from adapters.whatsapp_stub import get_whatsapp_adapter

        for key, prev in ((get_db, self._prev), (get_whatsapp_adapter, self._prev_wa)):
            if prev is not None:
                app.dependency_overrides[key] = prev
            else:
                app.dependency_overrides.pop(key, None)
        assert not self.unrecorded, (
            f"no recorded classifier reply for {self.unrecorded}; add them to "
            "evals/fixtures/consent_answers.json and re-record"
        )

    def everything_said(self) -> str:
        return " ".join(self.spoken) + " " + _spoken(self.adapter)


async def test_first_message_gets_the_welcome_not_a_domain_session(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")

    said = h.everything_said()
    # Her profile says Tamil, so the welcome is the Tamil text: disclosure,
    # then the ask. It used to be English read in a Tamil voice (M-14), and
    # this test asserted the English.
    assert parent_consent.build_welcome_text("Lakshmi", language="ta-IN") in said
    assert "நான் ஒரு மனிதர் இல்லை" in said, "the AI disclosure must be in it"
    assert "சம்மதமா" in said, "the welcome must actually ask"
    assert "not a person" not in said.lower(), "English read in a Tamil voice"

    # Nothing may advance into a conversation.
    real_db.expire_all()
    sessions = (
        (await real_db.execute(select(Session).where(Session.user_id == user_id)))
        .scalars()
        .all()
    )
    assert all(s.status == "consent" for s in sessions), (
        f"no domain session may open before consent: {[s.status for s in sessions]}"
    )

    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.AWAITING


async def test_a_spoken_yes_is_recorded_with_the_turn_that_carries_it(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")  # welcome
    async with _Harness(transcript="Yes, that sounds lovely") as h2:
        await _post_inbound(h2.client, with_audio=True)  # her answer, spoken
    async with _Harness(transcript="Yes, that's right.") as h3:
        await _post_inbound(h3.client, with_audio=True)  # and confirmed

    real_db.expire_all()
    records = (
        (
            await real_db.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )
    assert len(records) == 1
    record = records[0]
    assert record.principal == "parent"
    assert record.evidence_ref is not None, (
        "a consent record pointing at nothing proves nothing"
    )
    # Bound to the number she said it from (M-03).
    assert record.whatsapp_number == _NUMBER

    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.GRANTED


async def test_a_refusal_is_recorded_and_stops_everything(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="No, I don't want this") as h2:
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.DECLINED

    record = (
        (
            await real_db.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        )
        .scalars()
        .first()
    )
    assert record.consent_version.endswith("-declined")
    # In Tamil, her language (M-14).
    assert parent_consent.declined_text("ta-IN") in h2.everything_said()


async def test_ambiguity_reasks_once_then_halts(real_db):
    """Pestering someone who does not understand what is being asked is its
    own harm. One clarification, her answer to it read, then a human looks
    at it."""
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")  # ask 1: welcome
    async with _Harness(transcript="Hmm.") as h2:
        await _post_inbound(h2.client, with_audio=True)  # ask 2: re-ask
    assert parent_consent.build_reask_text("Lakshmi", language="ta-IN") in (
        h2.everything_said()
    )

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.AWAITING
    ), "her answer to the re-ask must still be read"

    async with _Harness(transcript="I am fine, who is this?") as h3:
        await _post_inbound(h3.client, with_audio=True)
    assert h3.everything_said().strip() == "", "halted means silent, not louder"
    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.HALTED
    )

    # A further message must not produce a third ask.
    async with _Harness(transcript="Yes, that sounds lovely") as h4:
        await _post_inbound(h4.client, with_audio=True)
    assert h4.everything_said().strip() == ""

    assert await _records(real_db, user_id) == [], "an unclear answer is not an answer"


async def test_her_answer_to_the_re_ask_is_read(real_db):
    """The re-ask used to halt her on sending, so even a clear yes to it
    went unheard."""
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="Hmm.") as h2:
        await _post_inbound(h2.client, with_audio=True)
    async with _Harness(transcript="Yes, that sounds lovely") as h3:
        await _post_inbound(h3.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.CONFIRMING
    )
    assert parent_consent.confirm_text("ta-IN") in h3.everything_said()


async def _records(db, user_id) -> list[ConsentRecord]:
    db.expire_all()
    return list(
        (
            await db.execute(
                select(ConsentRecord).where(ConsentRecord.user_id == user_id)
            )
        )
        .scalars()
        .all()
    )


# ── P3: no false yeses, and a yes is confirmed before it counts ──────────────


@pytest.mark.parametrize("said", _NOT_CONSENT)
async def test_a_question_back_is_never_consent(real_db, said):
    """M-01: "Kaun ho aap?" was recorded as consent. None of these may move
    her towards a recording, through the real classifier path."""
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id, language="hi-IN")

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript=said) as h2:
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    state = await parent_consent.get_status(user_id, real_db)
    assert state.status is ConsentStatus.AWAITING, f"{said!r} -> {state.status}"
    assert await _records(real_db, user_id) == []
    # She hears the re-ask, not the confirmation.
    assert parent_consent.confirm_text("hi-IN") not in h2.everything_said()


async def test_a_clear_yes_is_confirmed_then_recorded_once(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id, language="hi-IN")

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="Haan, theek hai, aap record kar sakte ho") as h2:
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.CONFIRMING
    )
    assert parent_consent.confirm_text("hi-IN") in h2.everything_said()
    assert await _records(real_db, user_id) == [], "a first yes records nothing"

    async with _Harness(transcript="haan") as h3:
        await _post_inbound(h3.client, with_audio=True)

    records = await _records(real_db, user_id)
    assert len(records) == 1
    assert records[0].consent_version == "parent-1.1"
    assert records[0].whatsapp_number == _NUMBER
    assert parent_consent.granted_text("hi-IN") in h3.everything_said()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.GRANTED
    )


async def test_nahi_declines_at_once_without_the_classifier(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id, language="hi-IN")

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="Nahi", classifier_down=True) as h2:
        # The classifier is down: a refusal must not need it.
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.DECLINED
    )


async def test_with_the_classifier_down_nothing_is_a_yes(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(
        transcript="Yes, that sounds lovely", classifier_down=True
    ) as h2:
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.AWAITING
    )
    assert parent_consent.build_reask_text("Lakshmi", language="ta-IN") in (
        h2.everything_said()
    )
    assert await _records(real_db, user_id) == []


async def test_anything_but_a_second_yes_goes_back_to_asking(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="Yes, that sounds lovely") as h2:
        await _post_inbound(h2.client, with_audio=True)
    async with _Harness(transcript="Enna solreenga?") as h3:
        await _post_inbound(h3.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.AWAITING
    )
    assert parent_consent.build_reask_text("Lakshmi", language="ta-IN") in (
        h3.everything_said()
    )
    assert await _records(real_db, user_id) == []


async def test_the_welcome_names_the_child_who_set_it_up(real_db):
    from models.family_account import FamilyAccount

    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)
    real_db.add(
        FamilyAccount(
            email=f"{user_id}@example.test",
            user_id=user_id,
            plan="free",
            onboarding_complete=True,
            child_name="Priya",
        )
    )
    await real_db.commit()

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")

    expected = parent_consent.build_welcome_text("Lakshmi", "Priya", language="ta-IN")
    assert expected in h.everything_said()


# ── M-03: consent belongs to the number ──────────────────────────────────────


async def _granted_parent(db, user_id: str) -> None:
    await _seed_parent(db, user_id)
    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste")
    async with _Harness(transcript="Yes, that sounds lovely") as h2:
        await _post_inbound(h2.client, with_audio=True)
    async with _Harness(transcript="Yes, that's right.") as h3:
        await _post_inbound(h3.client, with_audio=True)
    db.expire_all()
    assert await parent_consent.has_granted(user_id, db)


async def test_a_new_number_starts_from_the_welcome(real_db):
    from scripts.change_parent_number import change_number

    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _granted_parent(real_db, user_id)
    new_number = "+919000000779"

    outcome = await change_number(user_id, new_number, "test", real_db, apply=True)
    assert outcome.startswith("CHANGED")

    real_db.expire_all()
    assert not await parent_consent.has_granted(user_id, real_db)

    async with _Harness() as h:
        await _post_inbound(h.client, body="Namaste", number=new_number)
    assert parent_consent.build_welcome_text("Lakshmi", language="ta-IN") in (
        h.everything_said()
    ), "whoever holds the new number has agreed to nothing yet"
    real_db.expire_all()
    sessions = (
        (await real_db.execute(select(Session).where(Session.user_id == user_id)))
        .scalars()
        .all()
    )
    assert all(s.status == "consent" for s in sessions)

    # The old number is nobody's now.
    async with _Harness() as h2:
        await _post_inbound(h2.client, body="Namaste")
    assert "isn't scheduled" in h2.everything_said()


async def test_the_change_script_refuses_a_number_in_use(real_db):
    from scripts.change_parent_number import ChangeRefused, change_number

    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _seed_parent(real_db, user_id)
    await _seed_parent(real_db, f"pc-{uuid.uuid4().hex[:8]}", number="+919000000780")

    with pytest.raises(ChangeRefused):
        await change_number(user_id, "+919000000780", "test", real_db, apply=True)


async def test_a_dry_run_changes_nothing(real_db):
    from scripts.change_parent_number import change_number

    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _granted_parent(real_db, user_id)

    outcome = await change_number(
        user_id, "+919000000781", "test", real_db, apply=False
    )
    assert outcome.startswith("DRY RUN")
    real_db.expire_all()
    assert await parent_consent.has_granted(user_id, real_db)


# ── P10: a parent who agreed to the 1.0 wording is asked once more ───────────


async def _agreed_to_1_0(db, user_id: str) -> None:
    await _seed_parent(db, user_id)
    db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="",
            consent_version=parent_consent.PREVIOUS_GRANT_VERSION,
            principal=parent_consent.PRINCIPAL_PARENT,
            whatsapp_number=_NUMBER,
        )
    )
    await db.commit()


async def test_a_1_0_parent_hears_the_update_then_agrees_to_1_1(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _agreed_to_1_0(real_db, user_id)
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.UPDATE
    )
    assert not await parent_consent.has_granted(user_id, real_db)

    async with _Harness(transcript="Yes, that sounds lovely") as h:
        await _post_inbound(h.client, with_audio=True)
    said = h.everything_said()
    assert parent_consent.update_text("Lakshmi", language="ta-IN") in said
    assert (
        await _records(real_db, user_id) and len(await _records(real_db, user_id)) == 1
    ), "her first message after the change only hears the update"

    async with _Harness(transcript="Yes, that sounds lovely") as h2:
        await _post_inbound(h2.client, with_audio=True)

    versions = [r.consent_version for r in await _records(real_db, user_id)]
    assert sorted(versions) == ["parent-1.0", "parent-1.1"]
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.GRANTED
    )
    assert parent_consent.granted_text("ta-IN") in h2.everything_said()


async def test_a_1_0_parent_who_says_no_is_declined(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _agreed_to_1_0(real_db, user_id)
    async with _Harness(transcript="Namaste") as h:
        await _post_inbound(h.client, with_audio=True)
    async with _Harness(transcript="Nahi", classifier_down=True) as h2:
        await _post_inbound(h2.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.DECLINED
    )


async def test_a_1_0_parent_still_unclear_after_the_re_ask_is_halted(real_db):
    user_id = f"pc-{uuid.uuid4().hex[:8]}"
    await _agreed_to_1_0(real_db, user_id)
    async with _Harness(transcript="Namaste") as h:
        await _post_inbound(h.client, with_audio=True)
    async with _Harness(transcript="Hmm.") as h2:
        await _post_inbound(h2.client, with_audio=True)
    assert parent_consent.build_reask_text("Lakshmi", language="ta-IN") in (
        h2.everything_said()
    )
    async with _Harness(transcript="Hmm.") as h3:
        await _post_inbound(h3.client, with_audio=True)

    real_db.expire_all()
    assert (await parent_consent.get_status(user_id, real_db)).status is (
        ConsentStatus.HALTED
    )
    assert h3.everything_said().strip() == ""
