"""
Conversation card metrics (CONVERSATION_CARDS.md §13) against real
Postgres: the counts, and that no card text, question or phone number
reaches the JSON or the page.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, time, timedelta, timezone

import pytest

from cards import state
from core import metrics, metrics_page
from models.api_usage import ApiUsage
from models.conversation_card import ConversationCard
from models.family_account import FamilyAccount
from models.family_question import FamilyQuestion
from models.user_profile import UserProfileModel

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 25, 12, 0, tzinfo=timezone.utc)
SECRET_WORDS = "Ammachi-jasmine-secret"


async def card(db, user_id, status, **extra):
    c = ConversationCard(id=uuid.uuid4(), user_id=user_id, status="draft")
    state.set_text(
        c,
        title=SECRET_WORDS,
        hook=f"Did you know {SECRET_WORDS}?",
        excerpt=SECRET_WORDS,
        call_prompt=SECRET_WORDS,
        readback_text=SECRET_WORDS,
    )
    c.status = "awaiting_parent"
    if status in ("scheduled", "sent"):
        state.record_parent_answer(
            c, "approve", turn_id=None, heard_hash=c.readback_hash
        )
        state.transition(c, "scheduled")
        if status == "sent":
            state.transition(c, "sent")
    else:
        c.status = status
    c.created_at = NOW - timedelta(days=3)
    for k, v in extra.items():
        setattr(c, k, v)
    db.add(c)
    await db.commit()


async def test_card_metrics_count_and_reveal_nothing(real_db):
    user_id = f"met-{uuid.uuid4().hex[:8]}"
    real_db.add(
        UserProfileModel(
            user_id=user_id,
            name="Kamala",
            whatsapp_number=f"+9198{uuid.uuid4().int % 10**8:08d}",
            family_whatsapp_number="+14155550123",
            preferred_language="ta-IN",
            scheduled_time=time(10, 0),
            conversation_cards_enabled=True,
            share_preference="share_with_approval",
        )
    )
    real_db.add(
        FamilyAccount(
            email=f"{user_id}@example.com", user_id=user_id, card_email_enabled=False
        )
    )
    await real_db.commit()
    await card(
        real_db,
        user_id,
        "sent",
        scheduled_for=NOW - timedelta(days=1),
        wa_message_sid="SM1",
        wa_delivery_status="delivered",
        email_status="accepted",
        child_reaction="didnt_know",
        call_tag="brought_up",
        operator_action="approve",
        operator_reviewed_at=NOW - timedelta(days=2, hours=20),
        readback_offers=2,
    )
    await card(real_db, user_id, "kept_private", readback_offers=1)
    await card(real_db, user_id, "failed_fidelity")
    await card(real_db, user_id, "operator_review")
    real_db.add(
        FamilyQuestion(
            user_id=user_id,
            channel="whatsapp",
            raw_text=SECRET_WORDS,
            screening="sensitive",
            status="held",
        )
    )
    for _ in range(6):
        real_db.add(
            ApiUsage(
                user_id=user_id,
                vendor="anthropic",
                operation="card_select",
                model="claude-sonnet-5-5",
                units={"input_tokens": 1, "output_tokens": 1},
                created_at=NOW - timedelta(days=2),
            )
        )
    await real_db.commit()

    m = await metrics.compute_metrics(real_db, now=NOW, excluded_user_ids=frozenset())
    c = m["conversation_cards"]
    assert c["pipeline"]["drafted"]["all_time"] == 4
    assert c["pipeline"]["selection_calls"] == 6
    assert c["pipeline"]["selection_none_rate"] == pytest.approx(2 / 6, abs=0.001)
    assert c["pipeline"]["failed_fidelity"] == 1
    assert c["pipeline"]["waiting_for_review"] == 1
    assert c["pipeline"]["median_hours_in_review"] == pytest.approx(4.0)
    assert c["parent"]["outcomes"]["approved"] == 1
    assert c["parent"]["outcomes"]["kept_private"] == 1
    assert c["parent"]["readback_offers_per_card"] == 1.5
    assert c["delivery"]["whatsapp_by_country"]["+1"]["delivered"] == 1
    assert c["delivery"]["email_turned_off"] == 1
    assert c["child"]["didnt_know"] == 1
    assert c["child"]["questions_by_screening"] == {"sensitive": 1}
    assert c["calls_brought_up"] == 1
    [family] = [f for f in m["families"] if f["user_id"] == user_id]
    assert family["cards_sent_30d"] == 1
    assert family["pending_card_status"] == "operator_review"

    page = metrics_page.render(m)
    blob = json.dumps(m, default=str) + page
    assert SECRET_WORDS not in blob
    assert "4155550123" not in blob
    assert "Calls she brought up" in page
