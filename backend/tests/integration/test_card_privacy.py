"""
"Keep this between us" and her sharing preference, against real Postgres
(CONVERSATION_CARDS.md §5.1, §5.2). The topic match is the only model call
and is mocked.
"""

from __future__ import annotations

import json
import uuid
from datetime import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import select

from cards import privacy, state
from core import parent_consent
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.session import Session
from models.turn import Turn
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration


async def _setup(db, n_turns=3):
    user_id = f"priv-{uuid.uuid4().hex[:8]}"
    db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9190{uuid.uuid4().int % 10**8:08d}",
            preferred_language="ta-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=True,
            share_preference="share_with_approval",
        )
    )
    session_id = uuid.uuid4()
    db.add(
        Session(
            id=session_id,
            user_id=user_id,
            session_number=3,
            domain="family_ancestors",
        )
    )
    await db.commit()
    turn_ids = []
    for i, text in enumerate(
        [
            "My brother Ravi fell ill that winter.",
            "We walked to the talkies, six kilometres.",
            "Please, never tell anyone about my brother's illness.",
        ][:n_turns]
    ):
        turn_id = uuid.uuid4()
        turn = Turn(
            id=turn_id,
            session_id=session_id,
            user_id=user_id,
            turn_number=i + 1,
            transcript=text,
            response_text="…",
        )
        db.add(turn)
        turn_ids.append(turn_id)
    await db.commit()
    # Plain ids: attributes of committed rows expire, and reading one
    # outside the session's greenlet raises.
    return user_id, str(session_id), turn_ids


async def _card(db, user_id, turn_ids, status="awaiting_parent"):
    c = ConversationCard(
        id=uuid.uuid4(), user_id=user_id, status="draft", source_turn_ids=turn_ids
    )
    state.set_text(c, hook="Did you know…", excerpt="…", call_prompt="Ask her…")
    c.status = status
    card_id = c.id
    db.add(c)
    await db.commit()
    return card_id


async def _reload(db, model, id_):
    db.expire_all()
    return (await db.execute(select(model).where(model.id == id_))).scalar_one()


async def test_this_turn_is_made_private_and_its_card_withdrawn(real_db):
    user_id, session_id, turns = await _setup(real_db)
    on_card = await _card(real_db, user_id, [turns[1]])
    other = await _card(real_db, user_id, [turns[0]])

    await privacy.apply_share_signals(
        {"share_restriction": "this_turn"},
        turn_id=turns[1],
        session_id=session_id,
        user_id=user_id,
        db=real_db,
    )

    assert (await _reload(real_db, Turn, turns[1])).is_private is True
    assert (await _reload(real_db, Turn, turns[0])).is_private is False
    assert (await _reload(real_db, ConversationCard, on_card)).status == "withdrawn"
    assert (await _reload(real_db, ConversationCard, other)).status == (
        "awaiting_parent"
    )


async def test_a_topic_marks_matching_earlier_turns_and_is_remembered(real_db):
    user_id, session_id, turns = await _setup(real_db)
    card_on_illness = await _card(real_db, user_id, [turns[0]])
    reply = SimpleNamespace(
        content=json.dumps({"indexes": [0]}), input_tokens=1, output_tokens=1
    )
    with patch("cards.privacy.llm.chat", new=AsyncMock(return_value=reply)) as chat:
        await privacy.apply_share_signals(
            {"share_restriction": "topic", "topic_label": "my brother's illness"},
            turn_id=turns[2],
            session_id=session_id,
            user_id=user_id,
            db=real_db,
        )
    assert chat.await_args.kwargs["purpose"] == "private_topic_match"

    marked = []
    for turn_id in turns:
        turn = await _reload(real_db, Turn, turn_id)
        marked.append((turn.is_private, turn.private_topic))
    assert marked == [
        (True, "my brother's illness"),
        (False, None),
        (True, "my brother's illness"),
    ]
    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert profile.private_topics == ["my brother's illness"]
    withdrawn = await _reload(real_db, ConversationCard, card_on_illness)
    assert withdrawn.status == "withdrawn"


async def test_when_the_topic_match_fails_every_turn_is_private(real_db):
    user_id, session_id, turns = await _setup(real_db)
    with patch("cards.privacy.llm.chat", new=AsyncMock(side_effect=RuntimeError)):
        await privacy.apply_share_signals(
            {"share_restriction": "topic", "topic_label": "my brother"},
            turn_id=turns[2],
            session_id=session_id,
            user_id=user_id,
            db=real_db,
        )
    for turn_id in turns:
        assert (await _reload(real_db, Turn, turn_id)).is_private is True


async def test_never_share_withdraws_everything_unsent_and_keeps_her_recording(
    real_db,
):
    user_id, session_id, turns = await _setup(real_db)
    real_db.add(
        ConsentRecord(
            user_id=user_id,
            email_hash="",
            consent_version=parent_consent.PARENT_CONSENT_VERSION,
            principal=parent_consent.PRINCIPAL_PARENT,
        )
    )
    await real_db.commit()
    waiting = await _card(real_db, user_id, [turns[1]])
    sent = await _card(real_db, user_id, [turns[1]], status="awaiting_parent")
    sent_card = await _reload(real_db, ConversationCard, sent)
    state.record_parent_answer(
        sent_card, "approve", turn_id=None, heard_hash=sent_card.readback_hash
    )
    state.transition(sent_card, "scheduled")
    state.transition(sent_card, "sent")
    await real_db.commit()

    with patch("core.sharing.send_operator_alert", new=AsyncMock()) as alert:
        await privacy.apply_share_signals(
            {"share_preference_change": "never"},
            turn_id=turns[1],
            session_id=session_id,
            user_id=user_id,
            db=real_db,
        )
    alert.assert_awaited_once()

    profile = (
        await real_db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()
    assert profile.share_preference == "never"
    assert (await _reload(real_db, ConversationCard, waiting)).status == "withdrawn"
    assert (await _reload(real_db, ConversationCard, sent)).status == "sent"

    versions = (
        (
            await real_db.execute(
                select(ConsentRecord.consent_version)
                .where(ConsentRecord.user_id == user_id)
                .order_by(ConsentRecord.consented_at)
            )
        )
        .scalars()
        .all()
    )
    assert versions == ["parent-1.0", "share-1.0-never"]
    # Not sharing is not withdrawing consent to be recorded.
    assert await parent_consent.has_granted(user_id, real_db)


async def test_no_signal_changes_nothing(real_db):
    user_id, session_id, turns = await _setup(real_db)
    with patch("cards.privacy.llm.chat", new=AsyncMock()) as chat:
        await privacy.apply_share_signals(
            {"share_restriction": "none", "share_preference_change": "none"},
            turn_id=turns[1],
            session_id=session_id,
            user_id=user_id,
            db=real_db,
        )
    chat.assert_not_awaited()
    for turn_id in turns:
        assert (await _reload(real_db, Turn, turn_id)).is_private is False
