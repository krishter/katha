"""
P5 (M-04, D5): "keep this between us" for every family, against real
Postgres, through the real extraction step and the real family API.

The cards-off acceptance case replays a live extraction of a Hindi "don't
tell my son" (tests/fixtures/private_request_extraction_replay.json,
recorded by `run_eval.py privacy` with EVAL_RECORD_PRIVATE=1). The other
cases stub the model at the LLM adapter with a fixed extraction, and the
topic matcher with one that picks the turns mentioning the topic, so they
test what is done with an answer rather than the model's judgement.
"""

from __future__ import annotations

import json
import pathlib
import re
import uuid
from datetime import datetime, time, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlalchemy import select

from core import orchestrator, session_manager
from core.auth import get_current_user
from main import app
from models.db import AsyncSessionLocal, get_db
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import PriorContext, UserProfile

pytestmark = pytest.mark.integration

_RECORDED = json.loads(
    (
        pathlib.Path(__file__).resolve().parent.parent
        / "fixtures/private_request_extraction_replay.json"
    ).read_text()
)
_TOPIC = "brother's illness"


def _extraction(*, restriction="none", topic=None, title="The river house"):
    """A fixed extraction reply, as the model returns it."""
    body = {
        "story_atoms": [
            {
                "domain": "childhood",
                "title": title,
                "narrative": f"{title}, as she told it.",
                "who": ["mother"],
                "what": title,
                "when_approx": "circa 1955",
                "where_approx": "Kanpur",
                "why": "It mattered to her.",
                "verbatim_quote": None,
                "open_threads": [],
            }
        ],
        "named_entities": {},
        "significant_people": [],
        "themes": [],
        "energy_signal": "high",
        "gaps_remaining": [],
        "session_end_suggested": False,
        "share_restriction": restriction,
        "topic_label": topic,
    }
    return f"<extraction>{json.dumps(body)}</extraction>"


class _Model:
    """The LLM adapter: extraction from a queue, topic matching by the
    word in the topic, and nothing else expected."""

    def __init__(self, *extractions: str, fail_extraction: bool = False):
        self.extractions = list(extractions)
        self.fail_extraction = fail_extraction

    async def __call__(self, messages, **kwargs):
        purpose = kwargs.get("purpose")
        if purpose in ("extraction", "extraction_retry"):
            if self.fail_extraction:
                raise RuntimeError("extraction model unavailable")
            return SimpleNamespace(
                content=self.extractions.pop(0),
                input_tokens=10,
                output_tokens=10,
                stop_reason="end_turn",
            )
        if purpose == "private_topic_match":
            prompt = messages[0].content
            numbered = re.findall(r"\[(\d+)\] (.*)", prompt)
            hits = [int(i) for i, said in numbered if "brother" in said.lower()]
            return SimpleNamespace(
                content=json.dumps({"indexes": hits}),
                input_tokens=10,
                output_tokens=10,
                stop_reason="end_turn",
            )
        raise AssertionError(f"unexpected model call: {purpose}")


async def _parent(db, *, private_topics=None) -> str:
    user_id = f"pv-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9196{uuid.uuid4().int % 100000000:08d}",
            family_whatsapp_number="+919000000400",
            family_number_confirmed_at=datetime.now(timezone.utc),
            preferred_language="hi-IN",
            onboarding_context="",
            scheduled_time=time(9, 30),
            private_topics=private_topics or [],
        )
    )
    await db.commit()
    return user_id


async def _session(db, user_id: str, number: int = 1) -> str:
    session = Session(
        id=uuid.uuid4(),
        user_id=user_id,
        session_number=number,
        domain="childhood",
        exchange_count=1,
        status="active",
        started_at=datetime.now(timezone.utc),
    )
    db.add(session)
    await db.commit()
    return str(session.id)


async def _turn(db, user_id, session_id, transcript, *, status="pending") -> uuid.UUID:
    turn = Turn(
        session_id=uuid.UUID(session_id),
        user_id=user_id,
        turn_number=1,
        transcript=transcript,
        response_text="…",
        extraction_json={},
        extraction_status=status,
    )
    db.add(turn)
    await db.commit()
    return turn.id


async def _done_turn_with_atom(db, user_id, session_id, transcript, title):
    turn_id = await _turn(db, user_id, session_id, transcript, status="done")
    db.add(
        StoryAtom(
            session_id=uuid.UUID(session_id),
            turn_id=turn_id,
            user_id=user_id,
            domain="childhood",
            title=title,
            narrative=transcript,
            who=[],
            open_threads=[],
            completeness_score=4,
            verbatim_quote=transcript,
        )
    )
    await db.commit()
    return turn_id


async def _extract(db, user_id, session_id, turn_id, transcript, model, *, cards=None):
    state = await session_manager.get_session(session_id, db)
    profile = UserProfile(
        name="Kamala", preferred_language="hi-IN", onboarding_context="", cards=cards
    )
    with patch("adapters.llm.chat", new=model):
        await orchestrator.run_extraction_for_turn(
            turn_id, session_id, state, profile, PriorContext(), transcript, "…", db
        )


class _Family:
    """The family API as the signed-in child, plus captured emails."""

    def __init__(self, user_id: str):
        self.user_id = user_id
        self.emails: list[str] = []

    async def __aenter__(self):
        async def _db():
            async with AsyncSessionLocal() as s:
                yield s

        self._prev = {
            dep: app.dependency_overrides.get(dep) for dep in (get_db, get_current_user)
        }
        app.dependency_overrides[get_db] = _db
        app.dependency_overrides[get_current_user] = lambda: {
            "user_id": self.user_id,
            "sub": "child@example.test",
        }
        self.client = httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        )
        return self

    async def __aexit__(self, *exc):
        await self.client.aclose()
        for dep, prev in self._prev.items():
            if prev is None:
                app.dependency_overrides.pop(dep, None)
            else:
                app.dependency_overrides[dep] = prev

    async def story_titles(self) -> list[str]:
        stories = (await self.client.get("/family/stories")).json()["stories"]
        return sorted(s["title"] for s in stories)

    async def export_titles(self) -> list[str]:
        bundle = (await self.client.get("/family/export")).json()
        return sorted(s["title"] for s in bundle["stories"])


@pytest.fixture
def alerts(monkeypatch):
    sent: list[str] = []

    async def _email(to, subject, body_text, body_html):
        sent.append(subject)

    monkeypatch.setattr("core.email.send_email_ses", _email)
    monkeypatch.setattr("config.settings.OPERATOR_EMAIL", "ops@example.test")
    monkeypatch.setattr(
        "api.routes.family.storage.generate_presigned_url",
        AsyncMock(return_value="https://example.test/card.png"),
    )
    return sent


async def _make_memory_card(db, session_id, user_id):
    whatsapp = MagicMock()
    whatsapp.send_image = AsyncMock(return_value="SM_CARD")
    with (
        patch("core.orchestrator.storage.upload_media", new=AsyncMock()),
        patch("core.orchestrator.get_whatsapp_adapter", return_value=whatsapp),
    ):
        await orchestrator._generate_and_deliver_memory_card(session_id, user_id, db)
    db.expire_all()
    return (
        await db.execute(
            select(MemoryCard).where(MemoryCard.session_id == uuid.UUID(session_id))
        )
    ).scalar_one_or_none()


# ── the acceptance case: cards off, "don't tell my son" ──────────────────────


async def test_a_cards_off_parent_is_heard(real_db, alerts):
    user_id = await _parent(real_db)
    session_id = await _session(real_db, user_id)
    transcript = _RECORDED["transcript"]
    turn_id = await _turn(real_db, user_id, session_id, transcript)

    await _extract(
        real_db,
        user_id,
        session_id,
        turn_id,
        transcript,
        _Model(_RECORDED["extraction"]),
    )

    real_db.expire_all()
    turn = (await real_db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
    assert turn.is_private
    assert turn.extraction_status == "done"
    atoms = (
        (await real_db.execute(select(StoryAtom).where(StoryAtom.turn_id == turn_id)))
        .scalars()
        .all()
    )
    assert atoms, "the story is kept, for her"

    async with _Family(user_id) as family:
        assert await family.story_titles() == []
        assert await family.export_titles() == []

    # It was the session's only story, so there is no card at all.
    assert await _make_memory_card(real_db, session_id, user_id) is None


async def test_the_card_uses_only_what_she_did_not_keep_private(real_db, alerts):
    user_id = await _parent(real_db)
    session_id = await _session(real_db, user_id)
    private = await _turn(real_db, user_id, session_id, "Don't tell my son, but…")
    shared = await _turn(real_db, user_id, session_id, "We lived by the river.")

    model = _Model(
        _extraction(restriction="this_turn", title="The secret"),
        _extraction(title="The river house"),
    )
    await _extract(real_db, user_id, session_id, private, "Don't tell…", model)
    await _extract(real_db, user_id, session_id, shared, "We lived…", model)

    card = await _make_memory_card(real_db, session_id, user_id)
    atom = (
        await real_db.execute(
            select(StoryAtom).where(StoryAtom.id == card.story_atom_id)
        )
    ).scalar_one()
    assert atom.turn_id == shared


# ── topic privacy reaches every session, and later turns ─────────────────────


async def test_a_topic_hides_matching_stories_from_earlier_sessions(real_db, alerts):
    user_id = await _parent(real_db)
    first = await _session(real_db, user_id, 1)
    await _done_turn_with_atom(
        real_db, user_id, first, "My brother was ill for years.", "Brother, ill"
    )
    await _done_turn_with_atom(
        real_db, user_id, first, "We had a mango tree.", "The mango tree"
    )
    second = await _session(real_db, user_id, 2)
    await _done_turn_with_atom(
        real_db, user_id, second, "The doctor came for my brother.", "The doctor"
    )

    third = await _session(real_db, user_id, 3)
    asking = await _turn(real_db, user_id, third, "Never tell anyone about him.")
    await _extract(
        real_db,
        user_id,
        third,
        asking,
        "Never tell anyone about him.",
        _Model(_extraction(restriction="topic", topic=_TOPIC, title="A request")),
    )

    async with _Family(user_id) as family:
        assert await family.story_titles() == ["The mango tree"]
        assert await family.export_titles() == ["The mango tree"]
    real_db.expire_all()
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert _TOPIC in profile.private_topics


async def test_a_later_turn_on_a_private_topic_is_private_too(real_db, alerts):
    user_id = await _parent(real_db, private_topics=[_TOPIC])
    session_id = await _session(real_db, user_id)
    turn_id = await _turn(real_db, user_id, session_id, "My brother never recovered.")

    await _extract(
        real_db,
        user_id,
        session_id,
        turn_id,
        "My brother never recovered.",
        _Model(_extraction(title="He never recovered")),
    )

    async with _Family(user_id) as family:
        assert await family.story_titles() == []


# ── fail closed ──────────────────────────────────────────────────────────────


async def test_a_turn_still_pending_is_not_shown(real_db, alerts):
    user_id = await _parent(real_db)
    session_id = await _session(real_db, user_id)
    turn_id = await _turn(real_db, user_id, session_id, "Something")
    real_db.add(
        StoryAtom(
            session_id=uuid.UUID(session_id),
            turn_id=turn_id,
            user_id=user_id,
            domain="childhood",
            title="Not yet read",
            narrative="…",
            who=[],
            open_threads=[],
            completeness_score=4,
        )
    )
    await real_db.commit()

    async with _Family(user_id) as family:
        assert await family.story_titles() == []


async def test_a_failed_extraction_is_hidden_and_alerted(real_db, alerts):
    user_id = await _parent(real_db)
    session_id = await _session(real_db, user_id)
    turn_id = await _turn(real_db, user_id, session_id, "Something")

    await _extract(
        real_db, user_id, session_id, turn_id, "Something", _Model(fail_extraction=True)
    )

    real_db.expire_all()
    turn = (await real_db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
    assert turn.extraction_status == "failed"
    assert any("extraction failed" in subject for subject in alerts)


async def test_a_failed_privacy_step_hides_the_atoms_and_alerts(real_db, alerts):
    user_id = await _parent(real_db)
    session_id = await _session(real_db, user_id)
    turn_id = await _turn(real_db, user_id, session_id, "Don't tell anyone, but…")

    with patch(
        "core.orchestrator.card_privacy.apply_private_request",
        new=AsyncMock(side_effect=RuntimeError("privacy step down")),
    ):
        await _extract(
            real_db,
            user_id,
            session_id,
            turn_id,
            "Don't tell anyone, but…",
            _Model(_extraction(restriction="this_turn", title="Unread request")),
        )

    real_db.expire_all()
    turn = (await real_db.execute(select(Turn).where(Turn.id == turn_id))).scalar_one()
    assert turn.extraction_status == "failed"
    assert any("extraction failed" in subject for subject in alerts)
    async with _Family(user_id) as family:
        assert await family.story_titles() == [], "her request was not read"


# ── a card already sent ──────────────────────────────────────────────────────


async def test_a_card_already_sent_is_hidden_and_reported(real_db, alerts):
    user_id = await _parent(real_db)
    first = await _session(real_db, user_id, 1)
    await _done_turn_with_atom(
        real_db, user_id, first, "My brother was ill for years.", "Brother, ill"
    )
    card = await _make_memory_card(real_db, first, user_id)
    assert card is not None and card.twilio_message_sid == "SM_CARD"

    second = await _session(real_db, user_id, 2)
    asking = await _turn(real_db, user_id, second, "Never tell anyone about him.")
    await _extract(
        real_db,
        user_id,
        second,
        asking,
        "Never tell anyone about him.",
        _Model(_extraction(restriction="topic", topic=_TOPIC, title="A request")),
    )

    assert any("sent before she asked for privacy" in s for s in alerts)
    async with _Family(user_id) as family:
        cards = (await family.client.get("/family/cards")).json()
        assert cards["cards"] == [] and cards["total"] == 0
        stats = (await family.client.get("/family/stats")).json()
        assert stats["latest_card_url"] is None
