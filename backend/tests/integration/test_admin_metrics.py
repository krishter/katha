"""
Admin metrics against real Postgres (docs/specs/ADMIN_METRICS.md §9, with
the cost amendments in docs/specs/COST_TO_SERVE.md §12).

One small, fixed world is seeded and every key is asserted against values
worked out by hand in the comments beside them. `now` is frozen at
2026-10-16 12:00 UTC (17:30 IST), so:

    last_7d  covers  2026-10-09 12:00 UTC .. now
    last_30d covers  2026-09-16 12:00 UTC .. now
    IST "today" is 2026-10-16; the 30-day daily table runs 09-17 .. 10-16.

Three storytellers (a, b, c) plus one excluded storyteller whose rows must
change nothing. Storyteller c carries the PII markers.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import datetime, time, timedelta, timezone

import httpx
import pytest
from sqlalchemy import delete, select, text

from core import metrics
from main import app
from models.api_usage import ApiUsage
from models.consent_record import ConsentRecord
from models.crisis_event import CrisisEvent
from models.db import AsyncSessionLocal, get_db
from models.family_account import FamilyAccount
from models.magic_link_token import MagicLinkToken
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.user_profile import UserProfileModel
from tests.integration.conftest import _TABLES_IN_DELETE_ORDER

pytestmark = pytest.mark.integration

NOW = datetime(2026, 10, 16, 12, 0, tzinfo=timezone.utc)
EXCLUDED = frozenset({"user_excl", "user_not_present"})
TOKEN = "a" * 40

PII_MARKERS = [
    "Kamala Zz-Marker",
    "+910000000001",
    "marker@example.invalid",
    "NARRATIVE-MARKER",
    "PATTERN-MARKER",
    "CONTEXT-MARKER",
]


def utc(month, day, hour=0, minute=0):
    return datetime(2026, month, day, hour, minute, tzinfo=timezone.utc)


# ── the world ─────────────────────────────────────────────────────────────────


async def _seed(db) -> dict[str, uuid.UUID]:
    for model in _TABLES_IN_DELETE_ORDER:
        await db.execute(delete(model))
    await db.commit()

    profiles = [
        ("user_a", "Lakshmi", "+910000000010", utc(9, 1), 1, ""),
        ("user_b", "Meena", "+910000000020", utc(10, 1), 0, ""),
        # c: asked twice, never consented — stalled. Carries the PII markers.
        (
            "user_c",
            "Kamala Zz-Marker",
            "+910000000001",
            utc(10, 10),
            2,
            "CONTEXT-MARKER",
        ),
        ("user_excl", "Krish Self Trial", "+910000000099", utc(9, 1), 0, ""),
    ]
    for user_id, name, phone, created, asks, context in profiles:
        db.add(
            UserProfileModel(
                user_id=user_id,
                name=name,
                whatsapp_number=phone,
                preferred_language="hi-IN",
                pronouns="she/her",
                onboarding_context=context,
                family_whatsapp_number="+919999999999",
                scheduled_time=time(10, 30),
                timezone="Asia/Kolkata",
                parent_consent_asks=asks,
                created_at=created,
            )
        )
    for user_id, email, plan, complete in [
        ("user_a", "a@example.invalid", "free", True),
        ("user_b", "marker@example.invalid", "family", True),
        ("user_c", "c@example.invalid", "free", False),
        ("user_excl", "krish@example.invalid", "free", True),
    ]:
        db.add(
            FamilyAccount(
                user_id=user_id, email=email, plan=plan, onboarding_complete=complete
            )
        )
    for user_id, principal, at, email_hash in [
        ("user_a", "buyer", utc(9, 1, 1), "ha"),
        ("user_a", "parent", utc(9, 2, 3), "ha"),
        ("user_b", "buyer", utc(10, 1, 1), "hb"),
        # 20:00 UTC is 01:30 IST the next day: b's consent date is 10-13.
        ("user_b", "parent", utc(10, 12, 20), "hb"),
        ("user_excl", "buyer", utc(9, 1, 1), "hx"),
        ("user_excl", "parent", utc(9, 1, 2), "hx"),
        # A family that exercised DPDP deletion: anonymised, not deleted.
        ("DELETED", "buyer", utc(8, 1), "hdeleted"),
    ]:
        db.add(
            ConsentRecord(
                user_id=user_id,
                principal=principal,
                email_hash=email_hash,
                consent_version="1.1",
                consented_at=at,
                ip_address="203.0.113.7",
                user_agent="pytest",
            )
        )
    await db.commit()

    # name, user, number, status, exchanges, goal_met, ended_reason,
    # started_at, minutes until last message (None = never replied)
    sessions = [
        ("a1", "user_a", 1, "completed", 6, True, "goal_met", utc(9, 3, 4), 15),
        ("a2", "user_a", 2, "abandoned", 4, False, "stale", utc(9, 11, 4), 5),
        ("a3", "user_a", 3, "completed", 7, True, "llm_suggested", utc(9, 25, 4), 12),
        ("a4", "user_a", 4, "abandoned", 2, False, "stale", utc(10, 1, 4), None),
        ("a5", "user_a", 5, "completed", 5, False, "llm_suggested", utc(10, 5, 4), 20),
        ("a6", "user_a", 6, "abandoned", 3, False, "stale", utc(10, 7, 4), 8),
        ("a7", "user_a", 7, "completed", 6, True, "goal_met", utc(10, 9, 4), 10),
        ("a8", "user_a", 8, "abandoned", 1, False, "stale", utc(10, 11, 4), 2),
        # Either side of IST midnight: 23:50 on 10-14 and 00:10 on 10-15.
        (
            "a9",
            "user_a",
            15,
            "completed",
            4,
            False,
            "llm_suggested",
            utc(10, 14, 18, 20),
            11,
        ),
        ("a10", "user_a", 16, "abandoned", 5, False, "stale", utc(10, 14, 18, 40), 9),
        # Empty: Katha delivered nothing.
        ("a_empty", "user_a", 17, "abandoned", 0, False, "stale", utc(10, 15, 4), None),
        ("b1", "user_b", 1, "completed", 6, True, "goal_met", utc(10, 13, 5), 25),
        ("b2", "user_b", 2, "active", 2, False, None, utc(10, 16, 10), 3),
        ("c_consent", "user_c", 0, "consent", 0, False, None, utc(10, 10, 5), None),
        ("x1", "user_excl", 1, "active", 9, True, None, utc(10, 15, 4), 40),
    ]
    ids: dict[str, uuid.UUID] = {}
    for name, user, number, status, exch, goal, reason, started, mins in sessions:
        ids[name] = uuid.uuid4()
        db.add(
            Session(
                id=ids[name],
                user_id=user,
                session_number=number,
                domain="consent" if status == "consent" else "childhood",
                exchange_count=exch,
                goal_met=goal,
                ended_reason=reason,
                status=status,
                started_at=started,
                last_user_message_at=(
                    started + timedelta(minutes=mins) if mins is not None else None
                ),
            )
        )
    await db.commit()

    atoms = [
        ("user_a", "a1", "childhood", 5, utc(9, 3, 5)),
        ("user_a", "a2", "family_ancestors", 4, utc(9, 11, 5)),
        ("user_a", "a3", "education", 3, utc(9, 25, 5)),
        ("user_a", "a5", "career", 2, utc(10, 5, 5)),
        ("user_a", "a8", "love_marriage", 4, utc(10, 11, 5)),
        ("user_a", "a9", "historical_events", 1, utc(10, 14, 19)),
        ("user_a", "a10", "not_a_real_domain", 0, utc(10, 15, 5)),
        ("user_b", "b1", "childhood", 4, utc(10, 13, 6)),
        ("user_b", "b2", "childhood", 3, utc(10, 16, 11)),
        ("user_c", "c_consent", "childhood", 3, utc(10, 10, 5)),
        ("user_excl", "x1", "wisdom", 5, utc(10, 15, 5)),
    ]
    atom_ids = []
    for user, session, domain, score, created in atoms:
        atom = StoryAtom(
            session_id=ids[session],
            user_id=user,
            domain=domain,
            title="NARRATIVE-MARKER title",
            narrative="NARRATIVE-MARKER: the river behind the house.",
            who=["Kamala Zz-Marker"],
            what="NARRATIVE-MARKER what",
            when_approx="1958",
            where_approx="NARRATIVE-MARKER place",
            why="NARRATIVE-MARKER why",
            completeness_score=score,
            verbatim_quote="NARRATIVE-MARKER quote",
            open_threads=["NARRATIVE-MARKER thread"],
            created_at=created,
        )
        db.add(atom)
        atom_ids.append(atom)
    await db.commit()

    for user, session, status, code, created in [
        ("user_a", "a1", "delivered", None, utc(9, 3, 6)),
        ("user_a", "a8", "read", None, utc(10, 11, 6)),
        ("user_a", "a9", "undelivered", "63016", utc(10, 14, 20)),
        ("user_b", "b1", None, None, utc(10, 13, 7)),
        ("user_excl", "x1", "failed", "63016", utc(10, 15, 6)),
    ]:
        db.add(
            MemoryCard(
                session_id=ids[session],
                user_id=user,
                verbatim_quote="NARRATIVE-MARKER quote",
                domain="childhood",
                image_s3_key=f"cards/{ids[session]}.png",
                delivery_status=status,
                delivery_error_code=code,
                created_at=created,
            )
        )

    for session, created in [("a9", utc(10, 14, 18, 25)), ("x1", utc(10, 15, 4, 30))]:
        db.add(
            CrisisEvent(
                session_id=ids[session],
                source="user_transcript",
                matched_pattern="PATTERN-MARKER",
                created_at=created,
            )
        )

    for email, used, created in [
        ("a@example.invalid", True, utc(10, 12)),
        ("marker@example.invalid", True, utc(9, 1)),  # outside 30 days
        ("marker@example.invalid", False, utc(10, 15)),  # never used
        ("krish@example.invalid", True, utc(10, 14)),  # excluded family
    ]:
        db.add(
            MagicLinkToken(
                email=email,
                token_hash=uuid.uuid4().hex,
                expires_at=created + timedelta(minutes=15),
                used=used,
                created_at=created,
            )
        )
    await db.commit()

    # The ledger. Sonnet 4.6 at $3/$15, Sonnet 5.5 at $2/$10 with $0.10
    # cache reads, Haiku 5.5 at $0.10/$0.50; USD→INR 95.06.
    ledger = [
        # r1 dialogue 4.6: (1000*3 + 100*15)/1e6 = $0.0045 → ₹0.42777
        (
            "user_a",
            "a9",
            "anthropic",
            "dialogue",
            "claude-sonnet-4-6",
            {"input_tokens": 1000, "output_tokens": 100},
            None,
            None,
            None,
            utc(10, 14, 18, 21),
        ),
        # r2 dialogue 5.5 cached: (200*2 + 100*10 + 2000*0.10)/1e6 = $0.0016 → ₹0.152096
        (
            "user_a",
            "a9",
            "anthropic",
            "dialogue",
            "claude-sonnet-5-5",
            {
                "input_tokens": 200,
                "output_tokens": 100,
                "cache_read_input_tokens": 2000,
                "cache_creation_input_tokens": 0,
                "cache_ttl": "1h",
            },
            None,
            None,
            None,
            utc(10, 14, 18, 22),
        ),
        # r3 extraction Haiku: (2000*0.10 + 1000*0.50)/1e6 = $0.0007 → ₹0.066542
        (
            "user_a",
            "a9",
            "anthropic",
            "extraction",
            "claude-haiku-5-5",
            {"input_tokens": 2000, "output_tokens": 1000},
            None,
            None,
            None,
            utc(10, 14, 18, 23),
        ),
        # r4 entities 4.6: (500*3 + 100*15)/1e6 = $0.003 → ₹0.28518
        (
            "user_a",
            "a9",
            "anthropic",
            "entities",
            "claude-sonnet-4-6",
            {"input_tokens": 500, "output_tokens": 100},
            None,
            None,
            None,
            utc(10, 15, 2),
        ),
        # r5 STT 10 minutes at ₹29.40/h → ₹4.90
        (
            "user_a",
            "a9",
            "sarvam",
            "stt",
            "saaras:v3",
            {"seconds_received": 600, "seconds_billed": 600, "chunks": 22},
            None,
            None,
            None,
            utc(10, 14, 18, 20),
        ),
        # r6 TTS 1,000 chars at ₹30/10K → ₹3.00
        (
            "user_a",
            "a9",
            "sarvam",
            "tts_reply",
            "bulbul:v3",
            {"characters": 1000},
            None,
            None,
            None,
            utc(10, 14, 18, 22),
        ),
        # r7 inbound, r8 outbound voice: $0.005 each → ₹0.4753 each
        (
            "user_a",
            "a9",
            "twilio",
            "inbound",
            None,
            {"messages": 1},
            "SMin1",
            None,
            None,
            utc(10, 14, 18, 20),
        ),
        (
            "user_a",
            "a9",
            "twilio",
            "outbound_voice",
            None,
            {"messages": 1},
            "SMout1",
            "delivered",
            None,
            utc(10, 14, 18, 22),
        ),
        # r9 card, utility template, undelivered: ₹0.4753 + ₹0.115 = ₹0.5903
        (
            "user_a",
            "a10",
            "twilio",
            "outbound_card",
            None,
            {"messages": 1, "meta_category": "utility"},
            "SMcard1",
            "undelivered",
            "63016",
            utc(10, 15, 5),
        ),
        # r10 dialogue 4.6: (3000*3 + 200*15)/1e6 = $0.012 → ₹1.14072
        (
            "user_b",
            "b1",
            "anthropic",
            "dialogue",
            "claude-sonnet-4-6",
            {"input_tokens": 3000, "output_tokens": 200},
            None,
            None,
            None,
            utc(10, 13, 5, 10),
        ),
        # r11 dialogue 4.6, 30d but not 7d: (800*3 + 50*15)/1e6 = $0.00315 → ₹0.299439
        (
            "user_a",
            "a3",
            "anthropic",
            "dialogue",
            "claude-sonnet-4-6",
            {"input_tokens": 800, "output_tokens": 50},
            None,
            None,
            None,
            utc(9, 25, 4, 5),
        ),
        # r12 consolidation, all-time only: (1000*3)/1e6 = $0.003 → ₹0.28518
        (
            "user_a",
            "a1",
            "anthropic",
            "consolidation",
            "claude-sonnet-4-6",
            {"input_tokens": 1000, "output_tokens": 0},
            None,
            None,
            None,
            utc(9, 3, 6),
        ),
        # r13 a model with no price: listed as unpriced, never ₹0
        (
            "user_b",
            "b2",
            "anthropic",
            "dialogue",
            "claude-opus-9",
            {"input_tokens": 100, "output_tokens": 10},
            None,
            None,
            None,
            utc(10, 16, 10, 1),
        ),
        # r14 no user (startup preload): 500 chars → ₹1.50
        (
            None,
            None,
            "sarvam",
            "tts_fallback",
            "bulbul:v3",
            {"characters": 500},
            None,
            None,
            None,
            utc(10, 16, 1),
        ),
        # r15 the excluded account — must change nothing
        (
            "user_excl",
            "x1",
            "anthropic",
            "dialogue",
            "claude-sonnet-4-6",
            {"input_tokens": 90000, "output_tokens": 9000},
            None,
            None,
            None,
            utc(10, 15, 4),
        ),
    ]
    for user, session, vendor, op, model, units, sid, status, code, created in ledger:
        db.add(
            ApiUsage(
                user_id=user,
                session_id=ids[session] if session else None,
                vendor=vendor,
                operation=op,
                model=model,
                units=units,
                message_sid=sid,
                status=status,
                error_code=code,
                created_at=created,
            )
        )
    await db.commit()
    return ids


@pytest.fixture
async def world(real_db):
    await _seed(real_db)
    return await metrics.compute_metrics(real_db, now=NOW, excluded_user_ids=EXCLUDED)


# ── every key ─────────────────────────────────────────────────────────────────


async def test_top_level(world):
    assert world["generated_at"] == "2026-10-16T12:00:00Z"
    assert world["timezone"] == "Asia/Kolkata"
    assert world["excluded_user_count"] == 2
    assert world["stories"] == {
        "available": False,
        "reason": "Stories are not persisted yet (consolidation Phase A).",
    }


async def test_funnel(world):
    assert world["funnel"] == {
        "family_accounts": 3,
        "onboarding_complete": 2,
        "buyer_consented": 2,
        "storytellers": 3,
        "parent_consented": 2,
        "parent_consent_stalled": 1,  # c: two asks, no consent
        "first_conversation": 2,
        "reached_3_conversations": 1,
        "reached_10_conversations": 1,  # a: a1..a10
        "go_no_go_rate": 0.5,
    }


async def test_conversations(world):
    c = world["conversations"]
    # Started (not consent): a1..a10, a_empty, b1, b2 = 13.
    # 7d from 10-09 12:00: a8, a9, a10, a_empty, b1, b2. (a7 is 10-09 04:00.)
    assert c["sessions_started"] == {"all_time": 13, "last_7d": 6, "last_30d": 11}
    assert c["conversations"] == {"all_time": 12, "last_7d": 5, "last_30d": 10}
    assert c["empty_sessions"] == {"all_time": 1, "last_7d": 1, "last_30d": 1}
    assert c["active_now"] == 1  # b2; the excluded account's active one is not counted
    assert c["ended_completed"] == {"all_time": 6, "last_7d": 2, "last_30d": 5}
    assert c["ended_timeout"] == {"all_time": 5, "last_7d": 2, "last_30d": 4}
    assert c["ended_reason_breakdown"] == {
        "goal_met": 3,
        "llm_suggested": 3,
        "none": 1,
        "stale": 5,
    }
    # all: 1,2,2,3,4,4,5,5,6,6,6,7 → 4.5; 7d: 1,2,4,5,6 → 4; 30d → 4.5
    assert c["median_exchanges"] == {"all_time": 4.5, "last_7d": 4.0, "last_30d": 4.5}
    assert c["goal_met_rate"] == {"all_time": 0.333, "last_7d": 0.2, "last_30d": 0.3}
    # Lengths (a4 never replied): 15,5,12,20,8,10,2,11,9,25,3
    assert c["median_length_minutes"] == {
        "all_time": 10.0,
        "last_7d": 9.0,
        "last_30d": 10.0,
    }
    assert c["share_10_min_plus"] == {
        "all_time": 0.545,
        "last_7d": 0.4,
        "last_30d": 0.556,
    }

    daily = c["daily_last_30d"]
    assert len(daily) == 30
    assert daily[0]["date_ist"] == "2026-09-17"
    assert daily[-1]["date_ist"] == "2026-10-16"
    by_date = {d["date_ist"]: d for d in daily}
    # a9 started 23:50 IST on the 14th, a10 at 00:10 IST on the 15th.
    assert by_date["2026-10-14"] == {
        "date_ist": "2026-10-14",
        "conversations": 1,
        "storytellers": 1,
    }
    assert by_date["2026-10-15"]["conversations"] == 1
    assert by_date["2026-10-16"]["conversations"] == 1  # b2
    assert sum(d["conversations"] for d in daily) == 10
    assert by_date["2026-10-02"] == {
        "date_ist": "2026-10-02",
        "conversations": 0,
        "storytellers": 0,
    }


async def test_engagement(world):
    assert world["engagement"] == {
        "active_storytellers_7d": 2,
        # Pairs in IST 10-10..10-16: a on 11, 14, 15; b on 13, 16 = 5.
        # Eligible: a 7 days; b from its IST consent date 10-13 = 4. 5/11.
        "daily_active_rate_7d": 0.455,
        # a's first conversation 09-03; a2 on day 8, a3 on day 22.
        "week2_retention": {"rate": 1.0, "cohort": 1},
        "week4_retention": {"rate": 1.0, "cohort": 1},
    }


async def test_story_atoms(world):
    a = world["story_atoms"]
    assert a["atoms"] == {"all_time": 10, "last_7d": 6, "last_30d": 8}
    assert a["atoms_per_conversation"] == {
        "all_time": 0.83,
        "last_7d": 1.2,
        "last_30d": 0.8,
    }
    assert a["avg_completeness"] == {"all_time": 2.9, "last_7d": 2.5, "last_30d": 2.5}
    assert a["completeness_distribution"] == {0: 1, 1: 1, 2: 1, 3: 3, 4: 3, 5: 1}
    assert a["share_completeness_3_plus"] == {
        "all_time": 0.7,
        "last_7d": 0.667,
        "last_30d": 0.625,
    }
    # a has 5 atoms in 30 days, b has 2.
    assert a["median_atoms_per_storyteller_30d"] == 3.5
    assert a["domain_coverage"] == {
        "domains": 8,
        # a covers 6 (its unknown domain does not count); b and c one each.
        "histogram": {0: 0, 1: 2, 2: 0, 3: 0, 4: 0, 5: 0, 6: 1, 7: 0, 8: 0},
        "storytellers_6_plus": 1,
    }


async def test_memory_cards(world):
    assert world["memory_cards"] == {
        "generated": {"all_time": 4, "last_7d": 3, "last_30d": 3},
        "delivered": {"all_time": 2, "last_7d": 1, "last_30d": 1},
        "read": {"all_time": 1, "last_7d": 1, "last_30d": 1},
        "failed": {"all_time": 1, "last_7d": 1, "last_30d": 1},
        "pending": {"all_time": 1, "last_7d": 1, "last_30d": 1},
        "failure_codes": {"63016": 1},
    }


async def test_dashboard(world):
    assert world["dashboard"] == {
        "sign_ins": {"all_time": 2, "last_7d": 1, "last_30d": 1},
        "families_signed_in_30d": {"count": 1, "rate": 0.333},
    }


async def test_safety_and_plans(world):
    assert world["safety"] == {
        "crisis_events": {"all_time": 1, "last_7d": 1, "last_30d": 1},
        "by_source": {"user_transcript": 1},
    }
    assert world["plans"] == {
        "plans": {"family": 1, "free": 2},
        "at_free_limit": 1,  # a: free plan, 10 conversations
        "families_deleted": 1,
    }


async def test_cost(world):
    cost = world["cost"]
    # 7d:  dialogue r1+r2+r10 1.720586, extraction 0.066542, entities
    #      0.28518, STT 4.90, TTS r6+r14 4.50, Twilio 3×0.4753, Meta 0.115
    #      = 13.013208.  30d adds r11 0.299439; all-time adds r12 0.28518.
    assert cost["inr_total"] == {"all_time": 13.60, "last_7d": 13.01, "last_30d": 13.31}
    # ÷ 12, 5 and 10 conversations.
    assert cost["inr_per_conversation"] == {
        "all_time": 1.13,
        "last_7d": 2.60,
        "last_30d": 1.33,
    }
    seven = cost["by_component"]["last_7d"]
    assert {k: v["inr"] for k, v in seven.items()} == {
        "claude_dialogue": 1.72,
        "claude_extraction": 0.07,
        "claude_entities": 0.29,
        "claude_other": 0.0,
        "sarvam_stt": 4.90,
        "sarvam_tts": 4.50,
        # Gemini TTS; none in this fixture (see tests/test_metrics_tts.py).
        "google_tts": 0.0,
        "twilio": 1.43,
        "meta": 0.12,
    }
    assert seven["sarvam_stt"]["share"] == 0.377  # 4.90 / 13.013208
    assert cost["by_component"]["all_time"]["claude_other"]["inr"] == 0.29
    assert cost["by_component"]["last_30d"]["claude_dialogue"]["inr"] == 2.02

    units = cost["units"]["last_7d"]
    assert units["claude_tokens_by_operation"]["dialogue"] == {
        "calls": 4,  # r1, r2, r10 and the unpriced r13
        "input_tokens": 4300,
        "output_tokens": 410,
        "cache_creation_input_tokens": 0,
        "cache_read_input_tokens": 2000,
    }
    assert units["claude_tokens_by_operation"]["extraction"]["output_tokens"] == 1000
    assert units["stt_minutes_received"] == 10.0
    assert units["stt_minutes_billed"] == 10.0
    assert units["tts_characters"] == 1500
    assert units["tts_gemini_clips"] == 0
    assert units["tts_vendor_fallbacks"] == 0
    assert units["tts_vendor_fallback_rate"] is None
    assert units["messages_in"] == 1
    assert units["messages_out"] == 2

    # 2000 read / (2000 + 0 written + 4300 uncached)
    assert cost["dialogue_cache_hit_rate"] == 0.317
    # Sessions 1-10: b1 3000, a3 800, b2 100 → 800.
    # Sessions 11-30: a9 is number 15: 1000 and 200+2000 → 1600.
    assert cost["dialogue_input_tokens_by_tenure"] == {
        "1-10": 800,
        "11-30": 1600,
        "31-100": None,
        "100+": None,
    }
    wasted = {
        "count": 1,
        "inr": 0.59,
        "by_error_code": {"63016": {"count": 1, "inr": 0.59}},
    }
    assert cost["wasted_outbound"] == {"last_7d": wasted, "last_30d": wasted}
    assert cost["unpriced"] == [
        {"vendor": "anthropic", "model": "claude-opus-9", "rows": 1}
    ]
    assert cost["prices_as_of"] == "2026-10-16"
    assert cost["usd_inr_rate"] == 95.06


async def test_families(world):
    rows = world["families"]
    assert [r["user_id"] for r in rows] == ["user_b", "user_a", "user_c"]
    b, a, c = rows
    assert a == {
        "user_id": "user_a",
        "onboarded_at_ist": "2026-09-01T05:30:00+05:30",
        "parent_consented": True,
        "parent_consent_asks": 1,
        "plan": "free",
        "conversations_total": 10,
        "conversations_7d": 3,
        "last_conversation_at_ist": "2026-10-15T00:10:00+05:30",
        "atoms_total": 7,
        "avg_completeness": 2.71,  # 19 / 7
        "domains_covered": "6/8",
        "cards_delivered": 2,
        "cards_failed": 1,
        # r1..r9 + r11 = 10.671927 over 8 conversations in 30 days
        "cost_30d_inr": 10.67,
        "cost_per_conversation_30d_inr": 1.33,
    }
    assert b == {
        "user_id": "user_b",
        "onboarded_at_ist": "2026-10-01T05:30:00+05:30",
        "parent_consented": True,
        "parent_consent_asks": 0,
        "plan": "family",
        "conversations_total": 2,
        "conversations_7d": 2,
        "last_conversation_at_ist": "2026-10-16T15:30:00+05:30",
        "atoms_total": 2,
        "avg_completeness": 3.5,
        "domains_covered": "1/8",
        "cards_delivered": 0,
        "cards_failed": 0,
        "cost_30d_inr": 1.14,  # r10; r13 is unpriced
        "cost_per_conversation_30d_inr": 0.57,
    }
    assert c == {
        "user_id": "user_c",
        "onboarded_at_ist": "2026-10-10T05:30:00+05:30",
        "parent_consented": False,
        "parent_consent_asks": 2,
        "plan": "free",
        "conversations_total": 0,
        "conversations_7d": 0,
        "last_conversation_at_ist": None,
        "atoms_total": 1,
        "avg_completeness": 3.0,
        "domains_covered": "1/8",
        "cards_delivered": 0,
        "cards_failed": 0,
        "cost_30d_inr": 0.0,
        "cost_per_conversation_30d_inr": None,
    }


async def test_excluded_account_changes_nothing(real_db):
    await _seed(real_db)
    with_excl = await metrics.compute_metrics(
        real_db, now=NOW, excluded_user_ids=EXCLUDED
    )
    await real_db.execute(
        delete(CrisisEvent).where(
            CrisisEvent.session_id.in_(
                select(Session.id).where(Session.user_id == "user_excl")
            )
        )
    )
    for model in (ApiUsage, MemoryCard, StoryAtom):
        await real_db.execute(delete(model).where(model.user_id == "user_excl"))
    await real_db.commit()
    without_rows = await metrics.compute_metrics(
        real_db, now=NOW, excluded_user_ids=EXCLUDED
    )
    assert with_excl == without_rows


# ── privacy and read-only ─────────────────────────────────────────────────────


async def test_no_personal_data_in_json_or_html(real_db, monkeypatch):
    await _seed(real_db)
    monkeypatch.setattr("config.settings.ADMIN_API_TOKEN", TOKEN)
    monkeypatch.setattr("config.settings.METRICS_EXCLUDED_USER_IDS", "user_excl")

    async def _override():
        async with AsyncSessionLocal() as session:
            yield session

    prev = app.dependency_overrides.get(get_db)
    app.dependency_overrides[get_db] = _override
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            as_json = await client.get(
                "/admin/metrics", headers={"Authorization": f"Bearer {TOKEN}"}
            )
            basic = base64.b64encode(f"krish:{TOKEN}".encode()).decode()
            as_html = await client.get(
                "/admin/metrics/view", headers={"Authorization": f"Basic {basic}"}
            )
    finally:
        if prev is not None:
            app.dependency_overrides[get_db] = prev
        else:
            app.dependency_overrides.pop(get_db, None)

    assert as_json.status_code == 200 and as_html.status_code == 200
    body = json.dumps(as_json.json()) + as_html.text
    for marker in PII_MARKERS:
        assert marker not in body, marker
    # Nor any phone number, email hash, or Twilio message SID from the ledger.
    for leaked in ("+9100000", "hdeleted", "SMin1", "SMout1", "SMcard1", "203.0.113.7"):
        assert leaked not in body, leaked
    # The excluded account is counted, never named.
    assert "user_excl" not in body
    # And the page did render the real numbers.
    assert "user_a" in as_html.text and "Katha pilot metrics" in as_html.text


async def test_metrics_run_inside_a_read_only_transaction(real_db):
    await _seed(real_db)
    async with AsyncSessionLocal() as db:
        await db.execute(text("SET TRANSACTION READ ONLY"))
        result = await metrics.compute_metrics(db, now=NOW, excluded_user_ids=EXCLUDED)
        assert result["funnel"]["storytellers"] == 3
        with pytest.raises(Exception, match="read-only transaction"):
            await db.execute(
                text(
                    "INSERT INTO api_usage (id, vendor, operation, units) "
                    "VALUES (gen_random_uuid(), 'x', 'y', '{}'::jsonb)"
                )
            )
        await db.rollback()
