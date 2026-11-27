"""
The metrics page layout (docs/specs/METRICS_PAGE_LAYOUT.md §7, unit part).

render() is fed hand-built metrics dicts: one all-clear world, with each
test changing the field it is about.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime, timedelta, timezone

import pytest

from core import metrics, metrics_page

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=timezone.utc)


def _win(last_7d=0, last_30d=0, all_time=0):
    return {"last_7d": last_7d, "last_30d": last_30d, "all_time": all_time}


def _all_clear() -> dict:
    """A complete metrics dict in which nothing needs attention."""
    return {
        "generated_at": "2026-11-26T12:00:00Z",
        "timezone": "Asia/Kolkata",
        "excluded_user_count": 1,
        "funnel": {
            "family_accounts": 20,
            "onboarding_complete": 20,
            "buyer_consented": 20,
            "storytellers": 20,
            "parent_consented": 20,
            "parent_consent_stalled": 0,
            "first_conversation": 18,
            "reached_3_conversations": 15,
            "reached_10_conversations": 12,
            "go_no_go_rate": 0.6,
            "family_number": {
                "given": 20,
                "confirmed": 20,
                "waiting_code_live": 0,
                "waiting_no_live_code": 0,
                "waiting_over_48h": 0,
                "waiting_over_48h_no_live_code": 0,
                "confirmed_rate": 1.0,
            },
        },
        "conversations": {
            "sessions_started": _win(40, 120, 300),
            "conversations": _win(38, 110, 280),
            "empty_sessions": _win(0, 0, 0),
            "ended_completed": _win(30, 90, 250),
            "ended_timeout": _win(8, 20, 30),
            "median_exchanges": _win(7, 7, 7),
            "goal_met_rate": _win(0.8, 0.8, 0.8),
            "median_length_minutes": _win(14, 14, 14),
            "share_10_min_plus": _win(0.75, 0.75, 0.75),
            "ended_reason_breakdown": {},
            "daily_last_30d": [],
            "active_now": 2,
        },
        "engagement": {
            "active_storytellers_7d": 15,
            "daily_active_rate_7d": 0.65,
            "week2_retention": {"rate": 0.8, "cohort": 12},
            "week4_retention": {"rate": 0.7, "cohort": 10},
        },
        "story_atoms": {
            "atoms": _win(90, 300, 900),
            "atoms_per_conversation": _win(2.4, 2.7, 3.2),
            "avg_completeness": _win(3.6, 3.6, 3.6),
            "share_completeness_3_plus": _win(0.7, 0.7, 0.7),
            "completeness_distribution": {},
            "domain_coverage": {
                "domains": 8,
                "histogram": {},
                "storytellers_6_plus": 6,
            },
            "median_atoms_per_storyteller_30d": 22,
            "embeddings": {
                "atoms_embedded": 900,
                "atoms_total": 900,
                "share_embedded": 1.0,
                "atoms_pending": 0,
                "atoms_failed": 0,
            },
        },
        "memory_cards": {
            "generated": _win(5, 20, 60),
            "delivered": _win(5, 20, 60),
            "read": _win(3, 10, 30),
            "failed": _win(0, 0, 0),
            "pending": _win(0, 0, 0),
            "failure_codes": {},
        },
        "conversation_cards": {
            "pipeline": {
                "drafted": _win(2, 6, 10),
                "selection_calls": 10,
                "selection_none_rate": 0.1,
                "failed_fidelity": 0,
                "operator_approved": 0,
                "operator_edited": 0,
                "operator_rejected": 0,
                "waiting_for_review": 0,
                "median_hours_in_review": None,
            },
            "parent": {
                "share_preference": {},
                "outcomes": {},
                "readback_offers_per_card": None,
                "median_days_draft_to_sent": None,
            },
            "delivery": {
                "sent": _win(2, 5, 8),
                "whatsapp": {"delivered": 5, "sent": 5, "rate": 1.0},
                "whatsapp_by_country": {},
                "whatsapp_skipped": {},
                "whatsapp_stopped": 0,
                "email_accepted": 8,
                "email_failed": 0,
                "email_turned_off": 0,
            },
            "child": {
                "didnt_know": 2,
                "knew": 1,
                "questions": 1,
                "questions_by_screening": {},
                "questions_asked_to_her": 1,
            },
            "calls_brought_up": 0,
            "calls_tagged": 0,
        },
        "dashboard": {
            "sign_ins": _win(5, 12, 40),
            "families_signed_in_30d": {"count": 10, "rate": 0.5},
        },
        "stories": {"available": False, "reason": "Not persisted yet."},
        "cost": {
            "inr_total": _win(80, 300, 900),
            "inr_per_conversation": _win(2.1, 2.7, 3.2),
            "by_component": {
                "last_30d": {"claude_dialogue": {"inr": 200, "share": 0.66}}
            },
            "dialogue_input_tokens_by_tenure": {},
            "unpriced": [],
            "wasted_outbound": {
                "last_7d": {"count": 0, "inr": 0.0},
                "last_30d": {"count": 0, "inr": 0.0, "by_error_code": {}},
            },
            "dialogue_cache_hit_rate": 0.7,
            "units": {
                "last_30d": {
                    "tts_gemini_clips": 0,
                    "tts_gemini_audio_minutes": 0,
                    "tts_vendor_fallbacks": 0,
                    "tts_vendor_fallback_rate": None,
                    "embedding_query_calls": 100,
                    "embedding_query_fallbacks": 0,
                    "embedding_query_fallback_rate": 0.0,
                    "embedding_document_calls": 300,
                    "embedding_tokens": 9000,
                }
            },
            "prices_as_of": "2026-11-01",
            "usd_inr_rate": 95.0,
            "note": "",
        },
        "safety": {"crisis_events": _win(0, 0, 0), "by_source": {}},
        "plans": {"plans": {"pilot": 20}, "at_free_limit": 0, "families_deleted": 0},
        "languages": [
            {
                "language": "hi-IN",
                "families": 5,
                "source": "native",
                "reviewer": "a reviewer",
                "date": "2026-10-01",
            }
        ],
        "families": [],
        "api_keys": [],
    }


def _row(**overrides) -> dict:
    """A families[] row for an active, confirmed family."""
    row = {
        "user_id": "user_x",
        "onboarded_at_ist": "2026-11-01T10:00:00+05:30",
        "parent_consented": True,
        "parent_consent_asks": 1,
        "plan": "pilot",
        "conversations_total": 6,
        "conversations_7d": 3,
        "last_conversation_at_ist": "2026-11-26T09:00:00+05:30",
        "atoms_total": 12,
        "avg_completeness": 3.4,
        "domains_covered": "3/8",
        "cards_delivered": 0,
        "cards_failed": 0,
        "cards_sent_30d": 0,
        "last_card_sent_at_ist": None,
        "pending_card_status": None,
        "cost_30d_inr": 12.0,
        "cost_per_conversation_30d_inr": 2.0,
        "family_number_given": True,
        "family_number_confirmed": True,
        "confirm_code_live": False,
        "days_since_last_conversation": 0,
    }
    row.update(overrides)
    return row


def _attention_band(html: str) -> str:
    start = html.index("<section id=attention>")
    return html[start : html.index("</section>", start)]


# ── tier 1 ────────────────────────────────────────────────────────────────────


def test_all_clear_shows_no_tiles_and_says_so():
    html = metrics_page.render(_all_clear())
    band = _attention_band(html)
    assert "class='alert" not in band
    assert "All clear:" in band
    assert "active now 2" in band
    # Nothing needs attention, so every detail group starts closed.
    assert not re.search(r"<details class=group id=\w+ open", html)


def test_a_crisis_event_shows_a_red_tile_and_opens_ops():
    m = _all_clear()
    m["safety"]["crisis_events"]["last_7d"] = 1
    html = metrics_page.render(m)
    band = _attention_band(html)
    assert "<a class='alert red' href='#ops'><b>1</b>" in band
    assert "<details class=group id=ops open>" in html
    assert "<details class=group id=growth>" in html


@pytest.mark.parametrize(
    "change, link",
    [
        (
            lambda m: m["conversations"]["empty_sessions"].update(last_7d=2),
            "engagement",
        ),
        (lambda m: m["memory_cards"]["failed"].update(last_7d=1), "family"),
        (
            lambda m: m["cost"]["wasted_outbound"]["last_7d"].update(count=3),
            "cost",
        ),
        (
            lambda m: m["cost"]["units"]["last_30d"].update(
                tts_vendor_fallback_rate=0.02
            ),
            "ops",
        ),
        (
            lambda m: m["cost"]["units"]["last_30d"].update(
                embedding_query_fallback_rate=0.05
            ),
            "ops",
        ),
        (
            lambda m: m["languages"].append(
                {
                    "language": "as-IN",
                    "families": 1,
                    "source": "machine",
                    "reviewer": None,
                    "date": None,
                }
            ),
            "ops",
        ),
        (lambda m: m["funnel"].update(parent_consent_stalled=1), "growth"),
    ],
)
def test_each_orange_tile_appears_past_its_line(change, link):
    m = _all_clear()
    change(m)
    band = _attention_band(metrics_page.render(m))
    assert f"<a class='alert orange' href='#{link}'>" in band


def test_a_rate_exactly_at_one_percent_is_not_past_the_line():
    m = _all_clear()
    m["cost"]["units"]["last_30d"]["tts_vendor_fallback_rate"] = 0.01
    assert "class='alert" not in _attention_band(metrics_page.render(m))


def test_unconfirmed_family_numbers_are_orange_while_codes_are_live():
    m = _all_clear()
    m["funnel"]["family_number"].update(waiting_over_48h=2)
    band = _attention_band(metrics_page.render(m))
    assert "<a class='alert orange' href='#growth'><b>2 unconfirmed</b>" in band
    assert "cards to them go by email only" in band


def test_unconfirmed_family_numbers_turn_red_when_a_code_has_lapsed():
    m = _all_clear()
    m["funnel"]["family_number"].update(
        waiting_over_48h=2, waiting_over_48h_no_live_code=1
    )
    band = _attention_band(metrics_page.render(m))
    assert "<a class='alert red' href='#growth'><b>2 unconfirmed</b>" in band
    assert "1 with no live code" in band


# ── tier 2 ────────────────────────────────────────────────────────────────────


def _go_no_go_pill(rate) -> str:
    m = _all_clear()
    m["funnel"]["go_no_go_rate"] = rate
    html = metrics_page.render(m)
    tile = html[html.index("Parents at 10+ conversations") :]
    return re.search(r"<span class='pill \w+'>([^<]+)</span>", tile).group(1)


@pytest.mark.parametrize(
    "rate, word",
    [(0.5, "On track"), (0.45, "Close"), (0.449, "Behind"), (None, "No data")],
)
def test_scorecard_status_at_the_edges(rate, word):
    assert _go_no_go_pill(rate) == word


def test_scorecard_calls_out_small_groups():
    m = _all_clear()
    m["funnel"].update(parent_consented=4, reached_10_conversations=1)
    html = metrics_page.render(m)
    tile = html[html.index("Parents at 10+ conversations") :]
    foot = re.search(r"<div class=foot>([^<]*)</div>", tile).group(1)
    assert "small group" in foot


# ── tier 3 ────────────────────────────────────────────────────────────────────


def test_window_tables_read_7_days_then_30_then_all_time():
    html = metrics_page.render(_all_clear())
    head = html[html.index("<th></th>") :][:200]
    assert head.index("7 days") < head.index("30 days") < head.index("All time")


# ── tier 4 ────────────────────────────────────────────────────────────────────

STATUS_CASES = [
    (
        "consent_stalled",
        "Consent stalled",
        "red",
        dict(
            parent_consented=False,
            parent_consent_asks=2,
            days_since_last_conversation=None,
        ),
    ),
    ("quiet_red", "Quiet 9 days", "red", dict(days_since_last_conversation=9)),
    (
        "number_no_live_code",
        "Number unconfirmed · no live code",
        "red",
        dict(family_number_confirmed=False, confirm_code_live=False),
    ),
    (
        "no_parent_consent",
        "No parent consent",
        "orange",
        dict(
            parent_consented=False,
            parent_consent_asks=0,
            days_since_last_conversation=None,
        ),
    ),
    (
        "no_conversation_yet",
        "No conversation yet",
        "orange",
        dict(days_since_last_conversation=None, conversations_total=0),
    ),
    ("quiet_orange", "Quiet 4 days", "orange", dict(days_since_last_conversation=4)),
    (
        "number_unconfirmed",
        "Number unconfirmed",
        "orange",
        dict(family_number_confirmed=False, confirm_code_live=True),
    ),
    ("active", "Active", "blue", dict()),
]


@pytest.mark.parametrize("status, word, level, overrides", STATUS_CASES)
def test_each_family_status_rule(status, word, level, overrides):
    row = _row(**overrides)
    assert metrics.family_status(row) == status
    assert (
        metrics_page._family_status(row) == f"<span class='pill {level}'>{word}</span>"
    )


def test_status_rules_cover_every_status_in_severity_order():
    assert [c[0] for c in STATUS_CASES] == list(metrics.FAMILY_STATUS_ORDER)


def test_quiet_lines_sit_at_3_and_7_days():
    assert metrics.family_status(_row(days_since_last_conversation=2)) == "active"
    assert metrics.family_status(_row(days_since_last_conversation=3)) == "quiet_orange"
    assert metrics.family_status(_row(days_since_last_conversation=6)) == "quiet_orange"
    assert metrics.family_status(_row(days_since_last_conversation=7)) == "quiet_red"


def test_families_render_in_the_order_they_arrive_with_list_and_table():
    m = _all_clear()
    m["families"] = [
        _row(user_id=f"user_{i}", **overrides)
        for i, (_, _, _, overrides) in enumerate(STATUS_CASES)
    ]
    html = metrics_page.render(m)
    compact = html[html.index("<section id=families>") :]
    table = compact[: compact.index("<ul class=flist>")]
    listed = compact[compact.index("<ul class=flist>") : compact.index("</ul>")]
    words = [c[1] for c in STATUS_CASES]
    for part in (table, listed):
        positions = [part.index(f">{w}</span>") for w in words]
        assert positions == sorted(positions)
    assert "10+ ✓" not in table and "6 / 10" in table
    assert "Family number" in compact  # the full table's new column


def test_toward_ten_shows_a_tick_at_ten():
    assert metrics_page._toward_10(_row(conversations_total=10)) == "10+ ✓"
    assert metrics_page._toward_10(_row(conversations_total=7)) == "7 / 10"


# ── the page as a whole ───────────────────────────────────────────────────────


def test_no_javascript_and_no_links_off_the_page():
    m = _all_clear()
    m["families"] = [_row()]
    m["safety"]["crisis_events"]["last_7d"] = 1
    html = metrics_page.render(m)
    assert "<script" not in html.lower()
    assert not re.search(r"""(href|src)=['"]?https?:""", html)
    for gid in (
        "attention",
        "scorecard",
        "growth",
        "engagement",
        "quality",
        "family",
        "cost",
        "ops",
        "families",
    ):
        assert f"href='#{gid}'" in html
        assert re.search(rf"id={gid}[ >]", html), gid


def test_all_clear_is_not_changed_by_render():
    m = _all_clear()
    before = copy.deepcopy(m)
    metrics_page.render(m)
    assert m == before


# ── the family-number counts (metrics._family_number) ─────────────────────────


def test_family_number_counts_each_case_once():
    from types import SimpleNamespace as NS

    old = NOW - timedelta(days=5)
    recent = NOW - timedelta(hours=10)
    profiles = {
        "confirmed": NS(family_number_given=True, family_number_confirmed_at=old),
        "live": NS(family_number_given=True, family_number_confirmed_at=None),
        "lapsed": NS(family_number_given=True, family_number_confirmed_at=None),
        "none": NS(family_number_given=False, family_number_confirmed_at=None),
    }
    families = [
        NS(user_id="confirmed", created_at=old, family_confirm_code_expires_at=None),
        NS(
            user_id="live",
            created_at=recent,
            family_confirm_code_expires_at=NOW + timedelta(days=6),
        ),
        NS(
            user_id="lapsed",
            created_at=old,
            family_confirm_code_expires_at=NOW - timedelta(minutes=1),
        ),
        NS(user_id="none", created_at=old, family_confirm_code_expires_at=None),
    ]
    world = NS(storytellers=profiles, families=families)
    got = metrics._family_number(world, metrics._Windows(NOW))
    assert got == {
        "given": 3,
        "confirmed": 1,
        "waiting_code_live": 1,
        "waiting_no_live_code": 1,
        "waiting_over_48h": 1,
        "waiting_over_48h_no_live_code": 1,
        "confirmed_rate": 0.333,
    }
    assert got["waiting_code_live"] + got["waiting_no_live_code"] == (
        got["given"] - got["confirmed"]
    )
