"""
Drive one pilot family end to end, against a real database, and assert
every step rather than printing it.

    cd backend
    ./.venv/bin/python scripts/pilot_rehearsal.py

Why this is a script and not a checklist (SPRINT_1_PLAN S4.1): a checklist
gets walked once, by the person who already knows the answer, and then
rots. Nearly everything Sprint 1 found was found by running something
rather than reading it — the fact store that had never populated, the
embedding call that killed every turn, error 63049, and the presigned URLs
that were 403 for every recipient. Re-run this before every pilot family.

What it drives, in order:

     1. Create a family account and complete onboarding
     2. Produce the wa.me link the child forwards
     3. Simulate the parent's first inbound message
     4. Assert the welcome is sent, says plainly that Katha is an AI, and
        is spoken in the parent's language
     5. Assert no domain session opens before consent is recorded
     6. Give consent; assert a parent-principal ConsentRecord with evidence
     7. Run two sessions, a day apart
     8. Assert session 2's Layer 3 carries facts, significant people and
        open threads from session 1
     9. Assert a memory card was generated and delivered
    10. Assert the card and stories are visible through the family API
    11. Delete the user through the real endpoint
    12. Verify deletion by direct table and bucket inspection

Step 12 is the one a human checklist skips. During gate 5.5 the endpoint
returned {"status": "deleted"} while two objects were still in the bucket,
so the assertions here go to Postgres and S3 directly and never read the
endpoint's own account of itself.

Paid and outbound edges are stubbed: Sarvam STT/TTS, Anthropic, and the
WhatsApp send (via the stub adapter). verify_whatsapp_sender.py covers the
real send separately; this must be runnable without spending money or
touching a live number.

S3 is NOT stubbed. Real objects are written to the real bucket and the
deletion sweep is verified against it, because the stranded objects gate
5.5 found were invisible from the database side. Pass --skip-s3 to run
without AWS credentials; the run is then explicitly incomplete and says so.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import pathlib
import sys
import traceback
import uuid
from datetime import datetime, time, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402
from sqlalchemy import delete, select, text, update  # noqa: E402

from config import settings  # noqa: E402
from core import auth, orchestrator, parent_consent, session_manager  # noqa: E402
from core.auth import get_current_user  # noqa: E402
from main import app  # noqa: E402
from media import storage  # noqa: E402
from models.consent_record import ConsentRecord  # noqa: E402
from models.db import AsyncSessionLocal, get_db  # noqa: E402
from models.fact import Fact  # noqa: E402
from models.family_account import FamilyAccount  # noqa: E402
from models.memory_card import MemoryCard  # noqa: E402
from models.session import Session  # noqa: E402
from models.story_atom import StoryAtom  # noqa: E402
from models.turn import Turn  # noqa: E402
from models.user_profile import UserProfileModel  # noqa: E402
from prompts.system_prompt import UserProfile  # noqa: E402
from scheduler import session_initiator  # noqa: E402

# A single run's identity. Unique per run so a rehearsal never collides
# with a previous one's leftovers, and so a failed run can be inspected
# afterwards without ambiguity about which rows were its.
_RUN = uuid.uuid4().hex[:8]
_EMAIL = f"rehearsal-{_RUN}@katha.life"
# E.164, digits only — a hex run id here fails validation, which is its
# own small proof that the onboarding validator works.
_PARENT_NUMBER = f"+9199{uuid.uuid4().int % 10**8:08d}"
_FAMILY_NUMBER = "+919000000112"
_PARENT_NAME = "Subramaniam"
_LANGUAGE = "ta-IN"

_FAKE_WAV = b"RIFF" + b"\x00" * 40
_IST = timezone(timedelta(hours=5, minutes=30))


class Aborted(Exception):
    """A prerequisite failed, so everything after it would be noise."""


class Report:
    """
    Collects results so one failure does not hide the next. Independent
    checks continue; a failed `require` stops the run, because a rehearsal
    that carries on after (say) onboarding failed produces a page of
    cascading falsehoods and buries the one line that mattered.
    """

    def __init__(self) -> None:
        self.failures: list[str] = []
        self.skipped: list[str] = []
        self._step = 0

    def step(self, title: str) -> None:
        self._step += 1
        print(f"\n[{self._step:>2}] {title}")

    def check(self, ok: object, label: str, detail: str = "") -> bool:
        passed = bool(ok)
        print(f"     {'ok  ' if passed else 'FAIL'}  {label}")
        if not passed:
            self.failures.append(f"[{self._step}] {label}")
            if detail:
                print(f"           {detail}")
        return passed

    def require(self, ok: object, label: str, detail: str = "") -> None:
        if not self.check(ok, label, detail):
            raise Aborted(label)

    def skip(self, label: str, why: str) -> None:
        print(f"     SKIP  {label}")
        print(f"           {why}")
        self.skipped.append(f"[{self._step}] {label}")

    def note(self, text: str) -> None:
        print(f"           {text}")


# ── stubbed external edges ───────────────────────────────────────────────────


def _atom(title: str, threads: list[str]) -> dict:
    return {
        "domain": "childhood",
        "title": title,
        "narrative": f"Subramaniam described {title}.",
        "who": ["father", "Kamala"],
        "what": title,
        "when_approx": "circa 1958",
        "where_approx": "Madurai",
        "why": "A core childhood memory",
        "verbatim_quote": f"I remember {title} so clearly.",
        "open_threads": threads,
    }


_KAMALA = {
    "name": "Kamala",
    "relationship": "sister",
    "why_significant": "He paused when he said her name and did not go on.",
}

# One story-extraction payload per turn of session 1. Turns 2-4 each add an
# atom; by turn 4 the cumulative count reaches childhood's target of 3 and
# goal_met flips, so turn 5 is the closing exchange.
_SESSION1_EXTRACTIONS = {
    1: {"story_atoms": [], "significant_people": [_KAMALA]},
    2: {
        "story_atoms": [
            _atom("the street outside our house", ["what did the shop sell"])
        ],
        "significant_people": [],
    },
    3: {
        "story_atoms": [_atom("my mother's kitchen", ["what she cooked on Sundays"])],
        "significant_people": [],
    },
    4: {
        "story_atoms": [
            _atom("the neighbour who made sweets", ["the sweet-maker's name"])
        ],
        "significant_people": [],
    },
    5: {"story_atoms": [], "significant_people": []},
}

# Deliberately fenced. entity_extractor asks for bare JSON and Claude
# reliably wraps it, which is the bug that left structured_facts empty for
# every user forever (S1.2). Sending fenced JSON here keeps the tolerant
# parse under test instead of assuming it.
_ENTITY_REPLY = (
    "```json\n"
    + json.dumps(
        {
            "people": [
                {"name": "Kamala", "relationship": "sister"},
                {"name": "Vellai anna", "relationship": "neighbour"},
            ],
            "places": ["Madurai"],
            "dates": ["1948"],
            "institutions": ["Loyola College"],
        }
    )
    + "\n```"
)


def _extraction_envelope(payload: dict) -> str:
    full = {
        "story_atoms": payload["story_atoms"],
        "named_entities": {},
        "significant_people": payload["significant_people"],
        "themes": ["childhood"],
        "energy_signal": "high",
        "gaps_remaining": [],
        "session_end_suggested": False,
    }
    return f"<extraction>{json.dumps(full)}</extraction>"


class _LlmScript:
    """
    Stands in for Anthropic. Three call shapes reach it and they are told
    apart the same way the code tells them apart: the dialogue call passes
    a system prompt, and the two extraction calls do not. Entity extraction
    is identified by its own prompt text.
    """

    def __init__(self) -> None:
        self.turn = 0
        self.dialogue_prompts: list[str] = []

    async def __call__(self, messages, system=None, max_tokens=500):
        if system is not None:
            self.dialogue_prompts.append(system)
            return SimpleNamespace(
                content="<response>Tell me more about that.</response>",
                input_tokens=100,
                output_tokens=50,
            )

        content = messages[0].content if messages else ""
        if content.startswith("You are a precise entity extractor"):
            return SimpleNamespace(
                content=_ENTITY_REPLY, input_tokens=40, output_tokens=40
            )

        self.turn += 1
        payload = _SESSION1_EXTRACTIONS.get(
            self.turn, {"story_atoms": [], "significant_people": []}
        )
        return SimpleNamespace(
            content=_extraction_envelope(payload), input_tokens=50, output_tokens=100
        )


def _stub_paid_apis(llm_script: _LlmScript, spoken: list[tuple[str, str]]):
    """Patch every edge that costs money or leaves the machine, except S3."""

    async def _tts(text, language_code=None):
        spoken.append((text, language_code or ""))
        return _FAKE_WAV

    return [
        # _LlmScript.__call__ is already async, so it replaces chat
        # directly. Wrapping it in AsyncMock(side_effect=...) would
        # hand the caller an un-awaited coroutine.
        patch("adapters.llm.chat", new=llm_script),
        patch("adapters.sarvam_tts.synthesize", new=_tts),
        patch("core.orchestrator.sarvam_tts.synthesize", new=_tts),
        patch(
            "core.orchestrator.sarvam_stt.transcribe",
            new=AsyncMock(
                side_effect=lambda audio_bytes: SimpleNamespace(
                    transcript=(
                        "I was born in Madurai in 1948. My sister Kamala and I "
                        "played on the street outside my father's shop."
                    ),
                    language_code=_LANGUAGE,
                    language_probability=0.95,
                )
            ),
        ),
        patch(
            "core.orchestrator.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda audio_bytes: audio_bytes),
        ),
        patch(
            "media.audio_convert.convert_wav_to_ogg",
            new=AsyncMock(side_effect=lambda audio_bytes: audio_bytes),
        ),
    ]


# ── helpers ──────────────────────────────────────────────────────────────────


def _override_db():
    async def _dep():
        async with AsyncSessionLocal() as request_session:
            yield request_session

    return _dep


async def _post_inbound(client, body: str = "", with_audio: bool = False) -> int:
    data = {
        "From": f"whatsapp:{_PARENT_NUMBER}",
        "MessageSid": f"SM{uuid.uuid4().hex[:12]}",
        "Body": body,
    }
    if with_audio:
        data["MediaUrl0"] = "https://example.test/audio.ogg"
        data["MediaContentType0"] = "audio/ogg"
    response = await client.post("/webhook/whatsapp", data=data)
    return response.status_code


async def _row_counts(db, user_id: str) -> dict[str, int]:
    counts = {}
    for label, model in [
        ("user_profiles", UserProfileModel),
        ("sessions", Session),
        ("turns", Turn),
        ("story_atoms", StoryAtom),
        ("memory_cards", MemoryCard),
        ("facts", Fact),
    ]:
        rows = (
            (await db.execute(select(model).where(model.user_id == user_id)))
            .scalars()
            .all()
        )
        counts[label] = len(rows)
    return counts


def _s3_client():
    import boto3
    from botocore.config import Config

    return boto3.client(
        "s3",
        region_name=settings.AWS_S3_REGION,
        aws_access_key_id=settings.AWS_ACCESS_KEY_ID,
        aws_secret_access_key=settings.AWS_SECRET_ACCESS_KEY,
        config=Config(s3={"addressing_style": "virtual"}),
    )


def _object_exists(key: str) -> bool:
    import botocore.exceptions

    try:
        _s3_client().head_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
        return True
    except botocore.exceptions.ClientError as exc:
        if exc.response["Error"]["Code"] in ("404", "NoSuchKey", "NotFound"):
            return False
        raise


# ── the rehearsal ────────────────────────────────────────────────────────────


async def run(report: Report, use_s3: bool, state: dict) -> None:
    """
    Writes the user_id into `state` as soon as the account exists,
    rather than returning it. A crash partway through would otherwise
    leave the caller with no id to clean up by — and the leftover turns
    then collide with the next run on turns.inbound_message_sid, which
    is globally unique. Found by crashing.
    """
    prev_db = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override_db()
    client = httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )

    llm_script = _LlmScript()
    spoken: list[tuple[str, str]] = []
    patches = _stub_paid_apis(llm_script, spoken)
    for p in patches:
        p.start()

    user_id = ""
    try:
        async with AsyncSessionLocal() as db:
            # ── 1. Family account and onboarding ─────────────────────────
            report.step("Create a family account and complete onboarding")

            start = await client.post("/onboarding/start", data={"email": _EMAIL})
            report.require(
                start.status_code == 200 and start.json().get("status") == "new",
                "POST /onboarding/start creates a new account",
                f"{start.status_code} {start.text[:200]}",
            )

            account = (
                await db.execute(
                    select(FamilyAccount).where(FamilyAccount.email == _EMAIL)
                )
            ).scalar_one_or_none()
            report.require(account is not None, "family_accounts row written")
            user_id = account.user_id
            state["user_id"] = user_id

            # A real signed JWT in a real cookie — the same path the portal
            # uses. Overriding get_current_user instead would skip the
            # thing S4.0(a) just fixed.
            client.cookies.set("katha_token", auth.create_jwt(_EMAIL, user_id))

            profile_response = await client.post(
                "/onboarding/profile",
                data={
                    "parent_name": _PARENT_NAME,
                    "whatsapp_number": _PARENT_NUMBER,
                    "family_whatsapp_number": _FAMILY_NUMBER,
                    "preferred_language": _LANGUAGE,
                    "session_time": "10:30",
                    "onboarding_context": "Grew up in Madurai.",
                },
            )
            report.require(
                profile_response.status_code == 200,
                "POST /onboarding/profile accepts the parent profile",
                f"{profile_response.status_code} {profile_response.text[:200]}",
            )

            consent_response = await client.post(
                "/onboarding/consent", data={"consent_given": "true"}
            )
            report.require(
                consent_response.status_code == 200,
                "POST /onboarding/consent completes onboarding",
                f"{consent_response.status_code} {consent_response.text[:200]}",
            )
            consent_body = consent_response.json()

            await db.commit()
            account = (
                await db.execute(
                    select(FamilyAccount).where(FamilyAccount.user_id == user_id)
                )
            ).scalar_one()
            await db.refresh(account)
            report.check(
                account.onboarding_complete is True,
                "family_accounts.onboarding_complete is set",
            )

            buyer_consents = (
                (
                    await db.execute(
                        select(ConsentRecord).where(ConsentRecord.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            report.check(
                len(buyer_consents) == 1 and buyer_consents[0].principal == "buyer",
                "the buyer's own ConsentRecord is written with principal='buyer'",
                f"got {[(c.principal) for c in buyer_consents]}",
            )

            # ── 2. The wa.me link ────────────────────────────────────────
            report.step("Produce the wa.me link the child forwards")
            link = consent_body.get("parent_whatsapp_link", "")
            sender = settings.TWILIO_WHATSAPP_NUMBER.replace("whatsapp:", "").lstrip(
                "+"
            )
            report.check(bool(link), "onboarding returns a non-empty link")
            report.check(
                link.startswith("https://wa.me/"),
                "the link is a wa.me click-to-chat link",
                link,
            )
            report.check(
                bool(sender) and sender in link,
                "the link addresses Katha's sender number, not the parent's",
                f"sender={sender!r} link={link!r}",
            )
            report.note(f"link: {link}")
            report.note(
                "Katha cannot message first (Meta 63049) — nothing happens "
                "until the parent taps this."
            )

            # ── 3. The parent's first inbound message ────────────────────
            report.step("Simulate the parent's first inbound WhatsApp message")
            spoken.clear()
            status = await _post_inbound(client, body="Namaste")
            report.require(
                status == 200, "webhook accepts the inbound message", str(status)
            )

            # ── 4. The welcome ───────────────────────────────────────────
            report.step("Assert the welcome discloses the AI and is in her language")
            report.require(spoken, "Katha said something back")
            welcome_text, welcome_language = spoken[0]
            lowered = welcome_text.lower()
            report.check(
                "i am not a person" in lowered,
                "the welcome states plainly that Katha is not a person",
                welcome_text[:160],
            )
            report.check(
                "ai" in lowered or "computer program" in lowered,
                "the welcome names what Katha actually is",
            )
            disclosure_at = lowered.find("not a person")
            ask_at = lowered.find("would you be happy")
            report.check(
                0 <= disclosure_at < ask_at,
                "the disclosure comes BEFORE the ask, not after it",
                "consent from someone who thinks they are talking to a person "
                f"is not informed consent (disclosure at {disclosure_at}, "
                f"ask at {ask_at}; -1 means absent)",
            )
            report.check(
                "would you be happy" in lowered,
                "the welcome actually asks for consent",
            )
            report.check(
                welcome_language == _LANGUAGE,
                "the welcome is spoken in the parent's language",
                f"synthesized with language_code={welcome_language!r}, "
                f"profile says {_LANGUAGE!r}",
            )

            # ── 5. Nothing opens before consent ──────────────────────────
            report.step("Assert no domain session opens before consent is recorded")
            db.expire_all()
            sessions = (
                (await db.execute(select(Session).where(Session.user_id == user_id)))
                .scalars()
                .all()
            )
            report.check(
                all(s.status == "consent" for s in sessions),
                "every session so far is a consent session, not a domain session",
                f"statuses: {[s.status for s in sessions]}",
            )
            state = await parent_consent.get_status(user_id, db)
            report.check(
                state.status is parent_consent.ConsentStatus.AWAITING,
                "parent consent state is AWAITING",
                str(state.status),
            )

            # ── 6. She says yes ──────────────────────────────────────────
            report.step("Record the parent's consent")
            with patch(
                "api.routes.webhook.sarvam_stt.transcribe",
                new=AsyncMock(
                    return_value=SimpleNamespace(
                        transcript="Yes, that sounds lovely",
                        language_code=_LANGUAGE,
                    )
                ),
            ):
                status = await _post_inbound(client, with_audio=True)
            report.require(status == 200, "webhook accepts her answer", str(status))

            db.expire_all()
            parent_records = [
                c
                for c in (
                    (
                        await db.execute(
                            select(ConsentRecord).where(
                                ConsentRecord.user_id == user_id
                            )
                        )
                    )
                    .scalars()
                    .all()
                )
                if c.principal == "parent"
            ]
            report.require(
                len(parent_records) == 1,
                "exactly one ConsentRecord with principal='parent'",
                f"found {len(parent_records)}",
            )
            report.check(
                parent_records[0].evidence_ref is not None,
                "the parent's consent record points at the turn that carries it",
                "a consent record pointing at nothing proves nothing",
            )
            report.check(
                (await parent_consent.get_status(user_id, db)).status
                is parent_consent.ConsentStatus.GRANTED,
                "parent consent state is GRANTED",
            )

            # ── 7. Two sessions, a day apart ─────────────────────────────
            report.step("Run two sessions, a day apart")
            profile = UserProfile(
                name=_PARENT_NAME,
                preferred_language=_LANGUAGE,
                onboarding_context="Grew up in Madurai.",
            )

            # The real path, not a shortcut through the orchestrator: the
            # scheduler opens the session and the parent answers through the
            # webhook. Calling process_voice_turn directly would skip
            # set_turn_audio_key and the scheduler's session_open_audio_s3_key
            # write — the two keys whose objects were stranded in gate 5.5,
            # and therefore the entire point of step 13's bucket check.
            async def _open_session_due_now() -> None:
                now_ist = datetime.now(timezone.utc).astimezone(_IST)
                await db.execute(
                    update(UserProfileModel)
                    .where(UserProfileModel.user_id == user_id)
                    .values(scheduled_time=time(now_ist.hour, now_ist.minute))
                )
                await db.commit()
                await session_initiator.initiate_sessions(AsyncSessionLocal)
                db.expire_all()

            await _open_session_due_now()
            session1_row = (
                (
                    await db.execute(
                        select(Session)
                        .where(Session.user_id == user_id)
                        .where(Session.status != "consent")
                        .order_by(Session.session_number)
                    )
                )
                .scalars()
                .first()
            )
            report.require(
                session1_row is not None,
                "the scheduler opened session 1",
                "no non-consent session exists",
            )
            report.check(
                session1_row.domain == "childhood",
                "session 1 opens on the first domain",
                session1_row.domain,
            )
            report.check(
                bool(session1_row.session_open_message_id),
                "the opening voice note was sent and its message id recorded",
            )
            report.check(
                bool(session1_row.session_open_audio_s3_key),
                "the opening voice note's S3 key is recorded on the session",
                "an untracked key here is the object gate 5.5 found stranded",
            )
            # Plain values, not the instance: expire_all() below would make
            # any later attribute access re-query lazily, on a connection the
            # webhook's background tasks may still be using.
            session1_pk = session1_row.id
            session1_domain = session1_row.domain

            for turn_number in range(1, 6):
                status = await _post_inbound(client, with_audio=True)
                report.check(
                    status == 200,
                    f"session 1 turn {turn_number} accepted by the webhook",
                    str(status),
                )

            db.expire_all()
            turns = (
                (await db.execute(select(Turn).where(Turn.user_id == user_id)))
                .scalars()
                .all()
            )
            report.check(
                len(turns) >= 5,
                "five conversational turns persisted",
                f"got {len(turns)} (the consent turn is separate)",
            )
            report.check(
                all(
                    turn.response_audio_s3_key
                    for turn in turns
                    if turn.session_id == session1_pk
                ),
                "every turn records the S3 key of the voice note Katha sent back",
                "an untracked key here is the other object gate 5.5 stranded",
            )

            closed = (
                await db.execute(
                    select(Session.status, Session.ended_reason).where(
                        Session.id == session1_pk
                    )
                )
            ).one()
            report.check(
                closed.status == "completed",
                "session 1 closed",
                f"status={closed.status} reason={closed.ended_reason}",
            )

            atoms = (
                (
                    await db.execute(
                        select(StoryAtom).where(StoryAtom.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            report.check(
                len(atoms) == 3,
                "session 1 produced three story atoms",
                f"got {len(atoms)}",
            )

            # Age session 1 by a day. The point of two sessions is that the
            # second one remembers the first; running them back to back
            # would not exercise anything a single session does not.
            yesterday = datetime.now(timezone.utc) - timedelta(days=1)
            await db.execute(
                update(Session)
                .where(Session.id == session1_pk)
                .values(
                    started_at=yesterday,
                    ended_at=yesterday,
                    updated_at=yesterday,
                    last_user_message_at=yesterday,
                )
            )
            await db.execute(
                update(StoryAtom)
                .where(StoryAtom.user_id == user_id)
                .values(created_at=yesterday)
            )
            await db.commit()
            report.note("session 1 backdated 24 hours")

            session2 = await session_manager.start_session(user_id, db)
            report.check(
                session2.session_number == 2,
                "session 2 is numbered 2",
                str(session2.session_number),
            )
            report.check(
                session2.domain != session1_domain,
                "session 2 advances to a new domain",
                f"{session1_domain} -> {session2.domain}",
            )

            # ── 8. Layer 3 carries session 1 forward ─────────────────────
            report.step("Assert session 2's Layer 3 carries session 1 forward")
            prior = await orchestrator.build_prior_context(user_id, session2.domain, db)

            report.check(
                bool(prior.facts),
                "structured facts survived into session 2",
                "empty facts means entity_extractor's fenced-JSON parse "
                "regressed (the S1.2 P0)",
            )
            report.check(
                bool(prior.significant_people),
                "significant people survived into session 2",
                f"got {prior.significant_people}",
            )
            report.check(
                bool(prior.open_threads),
                "open threads survived into session 2",
                "empty threads means recency retrieval returned nothing",
            )

            from prompts.system_prompt import build_system_prompt

            layer3 = build_system_prompt(profile, session2, prior)
            for needle, why in [
                ("Madurai", "a place from session 1"),
                ("Kamala", "a person from session 1"),
                ("1948", "a date from session 1"),
            ]:
                report.check(
                    needle in layer3,
                    f"session 2's assembled prompt contains {needle} ({why})",
                )
            threads_rendered = any(
                thread[:20] in layer3 for thread in prior.open_threads
            )
            report.check(
                threads_rendered,
                "at least one open thread from session 1 is rendered into the prompt",
                f"threads: {prior.open_threads}",
            )
            report.note(f"facts: {prior.facts}")
            report.note(f"open threads: {prior.open_threads}")

            # ── 9. Memory card ───────────────────────────────────────────
            report.step("Assert a memory card was generated and delivered")
            cards = (
                (
                    await db.execute(
                        select(MemoryCard).where(MemoryCard.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            report.require(
                len(cards) == 1,
                "exactly one memory card row for session 1",
                f"got {len(cards)}",
            )
            card = cards[0]
            report.check(
                card.delivered_at is not None,
                "the card was delivered, not merely generated",
            )
            report.check(
                bool(card.verbatim_quote),
                "the card carries a verbatim quote",
            )
            report.check(
                bool(card.image_s3_key), "the card's S3 key is recorded on the row"
            )

            if use_s3:
                report.check(
                    _object_exists(card.image_s3_key),
                    "the card image really exists in the bucket",
                    card.image_s3_key,
                )
            else:
                report.skip("card image exists in the bucket", "--skip-s3")

            # ── 10. Visible through the family API ───────────────────────
            report.step("Assert the card and stories are visible to the family")
            stories_response = await client.get("/family/stories")
            report.require(
                stories_response.status_code == 200,
                "GET /family/stories returns 200",
                f"{stories_response.status_code} {stories_response.text[:200]}",
            )
            stories = stories_response.json().get("stories", [])
            report.check(
                len(stories) == 3,
                "all three story atoms are visible to the family",
                f"got {len(stories)}",
            )
            report.check(
                any("Madurai" in (s.get("where_approx") or "") for s in stories),
                "a story carries its place through to the dashboard",
            )

            cards_response = await client.get("/family/cards")
            report.require(
                cards_response.status_code == 200,
                "GET /family/cards returns 200",
                f"{cards_response.status_code} {cards_response.text[:200]}",
            )
            card_rows = cards_response.json().get("cards", [])
            report.check(len(card_rows) == 1, "the memory card is listed")

            image_url = card_rows[0]["image_url"] if card_rows else ""
            report.check(
                settings.AWS_S3_REGION in image_url,
                "the card's presigned URL carries the region in its host",
                "a URL signed for ap-south-1 but addressed to the global "
                "endpoint returns 403 for every viewer (the S4.0 defect)",
            )
            if use_s3 and image_url:
                # The assertion mocks could never make: actually fetch it.
                async with httpx.AsyncClient() as raw:
                    fetched = await raw.get(image_url)
                report.check(
                    fetched.status_code == 200,
                    "the presigned card URL is actually fetchable",
                    f"HTTP {fetched.status_code} — the family would see a broken image",
                )
            else:
                report.skip("presigned card URL is fetchable", "--skip-s3")

            stats_response = await client.get("/family/stats")
            report.check(
                stats_response.status_code == 200
                and stats_response.json().get("total_story_atoms") == 3,
                "GET /family/stats reports the three captured stories",
                stats_response.text[:200],
            )

            # ── 11. Deletion ─────────────────────────────────────────────
            report.step("Delete the user through the real endpoint")

            # Every S3 key the database knows about for this user. The stub
            # adapter returns keys without uploading, so real objects are
            # written at exactly those keys first — otherwise step 12 would
            # "verify" the absence of objects that never existed, which is
            # the shape of check that let gate 5.5's two stranded objects
            # through in the first place.
            tracked_keys = [card.image_s3_key]
            tracked_keys += [
                key
                for key in (
                    await db.execute(
                        select(Turn.response_audio_s3_key)
                        .where(Turn.user_id == user_id)
                        .where(Turn.response_audio_s3_key.is_not(None))
                    )
                )
                .scalars()
                .all()
            ]
            tracked_keys += [
                key
                for key in (
                    await db.execute(
                        select(Session.session_open_audio_s3_key)
                        .where(Session.user_id == user_id)
                        .where(Session.session_open_audio_s3_key.is_not(None))
                    )
                )
                .scalars()
                .all()
            ]
            tracked_keys = sorted(set(tracked_keys))

            if use_s3:
                for key in tracked_keys:
                    if not _object_exists(key):
                        await storage.upload_media(b"rehearsal-audio", key, "audio/ogg")
                present = [key for key in tracked_keys if _object_exists(key)]
                report.require(
                    len(present) == len(tracked_keys),
                    f"all {len(tracked_keys)} tracked objects exist before deletion",
                    f"{len(present)}/{len(tracked_keys)} present",
                )
                report.note(f"tracked keys: {tracked_keys}")

            before = await _row_counts(db, user_id)
            report.require(
                all(count > 0 for count in before.values()),
                "every table has rows to delete",
                str(before),
            )

            deleted = await client.request("DELETE", f"/user/{user_id}")
            report.require(
                deleted.status_code == 200,
                "DELETE /user/{user_id} returns 200",
                f"{deleted.status_code} {deleted.text[:200]}",
            )
            report.note(f"endpoint said: {deleted.json()} — not evidence; see below")

            # ── 12. Verify by inspection, not by the response ────────────
            report.step("Verify deletion by direct table and bucket inspection")
            db.expire_all()
            after = await _row_counts(db, user_id)
            for label, count in after.items():
                report.check(count == 0, f"{label}: no rows remain", f"{count} left")

            account_rows = (
                (
                    await db.execute(
                        select(FamilyAccount).where(FamilyAccount.user_id == user_id)
                    )
                )
                .scalars()
                .all()
            )
            report.check(not account_rows, "family_accounts row removed")

            orphan_crisis = (
                await db.execute(
                    text(
                        "SELECT count(*) FROM crisis_events ce "
                        "LEFT JOIN sessions s ON s.id = ce.session_id "
                        "WHERE s.id IS NULL"
                    )
                )
            ).scalar_one()
            report.check(
                orphan_crisis == 0,
                "no orphaned crisis_events left behind",
                f"{orphan_crisis} orphans",
            )

            # Consent records are retained and anonymised, never deleted —
            # DPDP requires the audit trail to outlive the data.
            retained = (
                (
                    await db.execute(
                        select(ConsentRecord).where(
                            ConsentRecord.email_hash == auth.hash_email(_EMAIL)
                        )
                    )
                )
                .scalars()
                .all()
            )
            report.check(
                len(retained) >= 1,
                "consent records are retained for audit, not deleted",
                f"got {len(retained)}",
            )
            report.check(
                all(c.user_id == "DELETED" for c in retained),
                "retained consent records are anonymised (user_id='DELETED')",
                str([c.user_id for c in retained]),
            )
            report.check(
                all(c.ip_address is None and c.user_agent is None for c in retained),
                "retained consent records have ip_address and user_agent cleared",
            )

            if use_s3:
                survivors = [key for key in tracked_keys if _object_exists(key)]
                report.check(
                    not survivors,
                    f"all {len(tracked_keys)} objects swept from the bucket",
                    f"STRANDED: {survivors}",
                )
            else:
                report.skip(
                    "objects swept from the bucket",
                    "--skip-s3 — the deletion claim is UNVERIFIED on the "
                    "storage side, which is exactly where gate 5.5 failed",
                )
    finally:
        for p in patches:
            p.stop()
        await client.aclose()
        if prev_db is not None:
            app.dependency_overrides[get_db] = prev_db
        else:
            app.dependency_overrides.pop(get_db, None)
        app.dependency_overrides.pop(get_current_user, None)


async def _cleanup(user_id: str, use_s3: bool) -> None:
    """
    Leave the database as the run found it. Deletion (step 11) removes
    almost everything, but the anonymised consent records survive by
    design, and a failed run may leave rows behind at any point — so this
    sweeps by this run's own identifiers and nothing else.
    """
    async with AsyncSessionLocal() as db:
        # Collect the object keys BEFORE deleting the rows that name them.
        # Keys are session-scoped (cards/{session_id}.png) and adapter-scoped
        # (audio/stub-{hex}.ogg), so neither carries the run id — matching on
        # it left crashed runs' card images in the bucket, which is the same
        # untracked-object mistake gate 5.5 was about, committed by the
        # cleanup of the script that checks for it.
        keys: list[str] = []
        if user_id:
            for column, model in (
                (MemoryCard.image_s3_key, MemoryCard),
                (Turn.response_audio_s3_key, Turn),
                (Session.session_open_audio_s3_key, Session),
            ):
                keys += [
                    key
                    for key in (
                        await db.execute(
                            select(column)
                            .where(model.user_id == user_id)
                            .where(column.is_not(None))
                        )
                    )
                    .scalars()
                    .all()
                ]

            for model in (
                MemoryCard,
                StoryAtom,
                Turn,
                Fact,
                Session,
                UserProfileModel,
            ):
                await db.execute(delete(model).where(model.user_id == user_id))
            await db.execute(
                delete(FamilyAccount).where(FamilyAccount.user_id == user_id)
            )
        await db.execute(
            delete(ConsentRecord).where(
                ConsentRecord.email_hash == auth.hash_email(_EMAIL)
            )
        )
        await db.commit()

    if use_s3 and keys:
        import botocore.exceptions

        client = _s3_client()
        for key in sorted(set(keys)):
            try:
                client.delete_object(Bucket=settings.AWS_S3_BUCKET, Key=key)
            except botocore.exceptions.ClientError:
                print(f"cleanup: could not delete s3://{settings.AWS_S3_BUCKET}/{key}")


async def _preflight(report: Report, use_s3: bool) -> None:
    report.step("Preflight")

    try:
        async with AsyncSessionLocal() as db:
            await db.execute(text("SELECT 1"))
        reachable, reason = True, ""
    except Exception as exc:
        reachable, reason = False, f"{type(exc).__name__}: {exc}"
    report.require(
        reachable,
        "Postgres is reachable",
        f"{reason}\n           run `docker compose up -d db` and "
        "`alembic upgrade head`",
    )

    report.check(
        settings.WHATSAPP_ADAPTER == "stub",
        "WHATSAPP_ADAPTER is 'stub' — no real messages, no spend",
        f"it is {settings.WHATSAPP_ADAPTER!r}; this rehearsal would send "
        "real WhatsApp messages",
    )

    if use_s3:
        report.require(
            bool(settings.AWS_ACCESS_KEY_ID and settings.AWS_S3_BUCKET),
            "AWS credentials and bucket are configured",
            "pass --skip-s3 to run without them (the deletion claim is then "
            "unverified on the storage side)",
        )
        report.note(f"bucket: {settings.AWS_S3_BUCKET} ({settings.AWS_S3_REGION})")


async def main() -> int:
    parser = argparse.ArgumentParser(
        description="Pilot rehearsal — drive one family end to end and assert it."
    )
    parser.add_argument(
        "--skip-s3",
        action="store_true",
        help=(
            "run without touching S3. The bucket half of the deletion "
            "verification is then not performed, and the run reports as "
            "incomplete rather than green."
        ),
    )
    parser.add_argument(
        "--no-cleanup",
        action="store_true",
        help="leave this run's rows in place for inspection after a failure",
    )
    args = parser.parse_args()
    use_s3 = not args.skip_s3

    report = Report()
    print(f"Katha pilot rehearsal — run {_RUN}")
    print(f"parent {_PARENT_NAME} <{_PARENT_NUMBER}>, buyer <{_EMAIL}>")

    state: dict = {"user_id": ""}
    aborted = None
    crashed = None
    try:
        await _preflight_and_run(report, use_s3, state)
    except Aborted as exc:
        aborted = str(exc)
    except Exception:  # noqa: BLE001
        # Print the report anyway. A traceback with no indication of how
        # far the rehearsal got is half the information.
        crashed = traceback.format_exc()
    finally:
        if not args.no_cleanup:
            try:
                await _cleanup(state["user_id"], use_s3)
            except Exception as exc:  # noqa: BLE001
                print(f"\ncleanup failed: {exc}")

    print("\n" + "=" * 72)
    if crashed:
        print("CRASHED — an unexpected exception, not a failed assertion:\n")
        print(crashed)
        return 1
    if aborted:
        print(f"ABORTED at: {aborted}")
    if report.failures:
        print(f"FAILED — {len(report.failures)} assertion(s) did not hold:")
        for failure in report.failures:
            print(f"  - {failure}")
        return 1
    if aborted:
        return 1
    if report.skipped:
        print(f"INCOMPLETE — {len(report.skipped)} check(s) skipped:")
        for skipped in report.skipped:
            print(f"  - {skipped}")
        print("\nThis is not a pilot sign-off. Re-run without --skip-s3.")
        return 1
    print("PASS — every step asserted, on a real database and a real bucket.")
    print("This family could be onboarded.")
    return 0


async def _preflight_and_run(report: Report, use_s3: bool, state: dict) -> None:
    await _preflight(report, use_s3)
    await run(report, use_s3, state)


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
