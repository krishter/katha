"""
Pilot metrics for the operator page (docs/specs/ADMIN_METRICS.md, with the
cost section from docs/specs/COST_TO_SERVE.md §12).

Everything is computed in place from the live tables, so a DPDP deletion is
reflected the moment it happens and nothing leaves the database box.

Two rules shape every query here:

- **No personal data.** Only ids, timestamps, statuses, scores, counts and
  enum-like labels are ever selected. Names, numbers, emails, transcripts,
  narratives, quotes and crisis patterns are never read, so they cannot
  leak into the response even by mistake. Families are identified by
  `user_id` alone.
- **One definition of a conversation**, `session_manager.is_conversation`,
  shared with the free-tier counter.

At pilot row counts it is simplest and clearest to select those narrow
columns once and aggregate in Python: medians, IST day buckets and cohort
retention read far more plainly here than in SQL.

No FastAPI imports: `now` and the exclusions are passed in, so tests are
deterministic.
"""

from __future__ import annotations

import statistics
from collections import Counter, defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, Iterable

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from core import delivery, freemium, pricing, session_manager
from models.api_usage import ApiUsage
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.crisis_event import CrisisEvent
from models.family_account import FamilyAccount
from models.family_question import FamilyQuestion
from models.magic_link_token import MagicLinkToken
from models.memory_card import MemoryCard
from models.session import Session
from models.story_atom import StoryAtom
from models.user_profile import UserProfileModel
from prompts.domains import get_domain_sequence
from speech import catalogue

# India has no daylight saving, so a fixed offset is exact and needs no tz
# database in the container.
IST = timezone(timedelta(hours=5, minutes=30), "IST")

WINDOWS = ("all_time", "last_7d", "last_30d")
_WINDOW_DAYS = {"last_7d": 7, "last_30d": 30}

STORIES_PLACEHOLDER = {
    "available": False,
    "reason": "Stories are not persisted yet (consolidation Phase A).",
}


# ── helpers ───────────────────────────────────────────────────────────────────


def _aware(ts: datetime | None) -> datetime | None:
    if ts is None:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def ist_date(ts: datetime) -> date:
    return _aware(ts).astimezone(IST).date()


def ist_iso(ts: datetime | None) -> str | None:
    return _aware(ts).astimezone(IST).isoformat() if ts is not None else None


def rate(numerator: float, denominator: float) -> float | None:
    """A fraction rounded to 3 places; None when there is nothing to divide by."""
    if not denominator:
        return None
    return round(float(numerator) / float(denominator), 3)


def inr(value: Decimal | float | int) -> float:
    return float(Decimal(str(value)).quantize(Decimal("0.01")))


def _median(values: list[float]) -> float | None:
    return float(statistics.median(values)) if values else None


@dataclass(frozen=True)
class _Windows:
    now: datetime

    def contains(self, window: str, ts: datetime | None) -> bool:
        if ts is None:
            return False
        ts = _aware(ts)
        if ts > self.now:
            return False
        days = _WINDOW_DAYS.get(window)
        return days is None or ts >= self.now - timedelta(days=days)

    def split(self, items: Iterable[Any], ts_of) -> dict[str, list]:
        items = list(items)
        return {w: [i for i in items if self.contains(w, ts_of(i))] for w in WINDOWS}

    def count(self, items: Iterable[Any], ts_of) -> dict[str, int]:
        return {w: len(v) for w, v in self.split(items, ts_of).items()}


# ── loading (narrow, non-personal columns only) ───────────────────────────────


@dataclass
class _World:
    storytellers: dict[str, SimpleNamespace]
    families: list[SimpleNamespace]
    consents: list[SimpleNamespace]
    sessions: list[SimpleNamespace]
    atoms: list[SimpleNamespace]
    cards: list[SimpleNamespace]
    crises: list[SimpleNamespace]
    sign_ins: list[SimpleNamespace]
    usage: list[SimpleNamespace]
    families_deleted: int
    # Conversation cards (CONVERSATION_CARDS.md §13). Status and time
    # columns only: no card text, no question text, no phone number.
    conv_cards: list[SimpleNamespace]
    questions: list[SimpleNamespace]
    sharing: list[SimpleNamespace]


def _rows(result) -> list[SimpleNamespace]:
    return [SimpleNamespace(**row._mapping) for row in result]


async def _load(db: AsyncSession, excluded: frozenset[str]) -> _World:
    profiles = _rows(
        await db.execute(
            select(
                UserProfileModel.user_id,
                UserProfileModel.created_at,
                UserProfileModel.parent_consent_asks,
                UserProfileModel.preferred_language,
            )
        )
    )
    storytellers = {p.user_id: p for p in profiles if p.user_id not in excluded}

    families = [
        f
        for f in _rows(
            await db.execute(
                select(
                    FamilyAccount.user_id,
                    FamilyAccount.plan,
                    FamilyAccount.onboarding_complete,
                )
            )
        )
        if f.user_id not in excluded
    ]

    consents = _rows(
        await db.execute(
            select(
                ConsentRecord.user_id,
                ConsentRecord.principal,
                ConsentRecord.consented_at,
            )
        )
    )

    sessions = [
        s
        for s in _rows(
            await db.execute(
                select(
                    Session.id,
                    Session.user_id,
                    Session.session_number,
                    Session.status,
                    Session.exchange_count,
                    Session.goal_met,
                    Session.ended_reason,
                    Session.started_at,
                    Session.last_user_message_at,
                    session_manager.is_conversation().label("is_conversation"),
                )
            )
        )
        if s.user_id in storytellers
    ]

    atoms = [
        a
        for a in _rows(
            await db.execute(
                select(
                    StoryAtom.user_id,
                    StoryAtom.domain,
                    StoryAtom.completeness_score,
                    StoryAtom.created_at,
                )
            )
        )
        if a.user_id in storytellers
    ]

    cards = [
        c
        for c in _rows(
            await db.execute(
                select(
                    MemoryCard.user_id,
                    MemoryCard.delivery_status,
                    MemoryCard.delivery_error_code,
                    MemoryCard.created_at,
                )
            )
        )
        if c.user_id in storytellers
    ]

    # Crisis events hang off a session; only the source and time are read.
    crises = [
        c
        for c in _rows(
            await db.execute(
                select(
                    Session.user_id,
                    CrisisEvent.source,
                    CrisisEvent.created_at,
                ).join(Session, Session.id == CrisisEvent.session_id)
            )
        )
        if c.user_id in storytellers
    ]

    # Joined on email inside the query; the email itself is never selected.
    sign_ins = [
        t
        for t in _rows(
            await db.execute(
                select(FamilyAccount.user_id, MagicLinkToken.created_at)
                .join(FamilyAccount, FamilyAccount.email == MagicLinkToken.email)
                .where(MagicLinkToken.used.is_(True))
            )
        )
        if t.user_id not in excluded
    ]

    usage = [
        u
        for u in _rows(
            await db.execute(
                select(
                    ApiUsage.user_id,
                    ApiUsage.session_id,
                    ApiUsage.vendor,
                    ApiUsage.operation,
                    ApiUsage.model,
                    ApiUsage.units,
                    ApiUsage.status,
                    ApiUsage.error_code,
                    ApiUsage.created_at,
                )
            )
        )
        if u.user_id not in excluded
    ]

    # The DPDP deletion anonymises consent rows rather than deleting them.
    # Counted in SQL so no email hash ever leaves the database.
    families_deleted = (
        await db.execute(
            select(func.count(func.distinct(ConsentRecord.email_hash))).where(
                ConsentRecord.user_id == "DELETED"
            )
        )
    ).scalar_one()

    country = case(
        (UserProfileModel.family_whatsapp_number.like("+91%"), "+91"),
        (UserProfileModel.family_whatsapp_number.like("+1%"), "+1"),
        (UserProfileModel.family_whatsapp_number.is_(None), "none"),
        else_="other",
    ).label("child_country")
    conv_cards = [
        c
        for c in _rows(
            await db.execute(
                select(
                    ConversationCard.user_id,
                    ConversationCard.status,
                    ConversationCard.created_at,
                    ConversationCard.operator_action,
                    ConversationCard.operator_edited,
                    ConversationCard.operator_reviewed_at,
                    ConversationCard.readback_offers,
                    ConversationCard.correction_round,
                    ConversationCard.parent_answer,
                    ConversationCard.scheduled_for,
                    ConversationCard.wa_message_sid.is_not(None).label("wa_sent"),
                    ConversationCard.wa_delivery_status,
                    ConversationCard.wa_skipped_reason,
                    ConversationCard.email_status,
                    ConversationCard.child_reaction,
                    ConversationCard.call_tag,
                    country,
                ).join(
                    UserProfileModel,
                    UserProfileModel.user_id == ConversationCard.user_id,
                    isouter=True,
                )
            )
        )
        if c.user_id in storytellers
    ]
    questions = [
        q
        for q in _rows(
            await db.execute(
                select(
                    FamilyQuestion.user_id,
                    FamilyQuestion.card_id,
                    FamilyQuestion.kind,
                    FamilyQuestion.screening,
                    FamilyQuestion.status,
                    FamilyQuestion.channel,
                    FamilyQuestion.created_at,
                )
            )
        )
        if q.user_id in storytellers
    ]
    sharing = [
        r
        for r in _rows(
            await db.execute(
                select(
                    UserProfileModel.user_id,
                    UserProfileModel.share_preference,
                    UserProfileModel.card_whatsapp_enabled,
                    FamilyAccount.card_email_enabled,
                )
                .join(
                    FamilyAccount,
                    FamilyAccount.user_id == UserProfileModel.user_id,
                    isouter=True,
                )
                .where(UserProfileModel.conversation_cards_enabled.is_(True))
            )
        )
        if r.user_id in storytellers
    ]

    return _World(
        storytellers=storytellers,
        families=families,
        consents=consents,
        sessions=sessions,
        atoms=atoms,
        cards=cards,
        crises=crises,
        sign_ins=sign_ins,
        usage=usage,
        families_deleted=families_deleted,
        conv_cards=conv_cards,
        questions=questions,
        sharing=sharing,
    )


# ── sections ──────────────────────────────────────────────────────────────────


def _parent_consent_dates(world: _World) -> dict[str, datetime]:
    """Earliest parent consent per storyteller."""
    out: dict[str, datetime] = {}
    for c in world.consents:
        if c.principal == "parent" and c.user_id in world.storytellers:
            ts = _aware(c.consented_at)
            if c.user_id not in out or ts < out[c.user_id]:
                out[c.user_id] = ts
    return out


def _conversations(world: _World) -> list[SimpleNamespace]:
    return [s for s in world.sessions if s.is_conversation]


def _funnel(world: _World) -> dict:
    parent = _parent_consent_dates(world)
    buyers = {
        c.user_id
        for c in world.consents
        if c.principal == "buyer" and c.user_id not in ("DELETED",)
    }
    per_user = Counter(s.user_id for s in _conversations(world))
    reached_10 = sum(1 for u in world.storytellers if per_user[u] >= 10)
    return {
        "family_accounts": len(world.families),
        "onboarding_complete": sum(1 for f in world.families if f.onboarding_complete),
        "buyer_consented": sum(1 for f in world.families if f.user_id in buyers),
        "storytellers": len(world.storytellers),
        "parent_consented": len(parent),
        "parent_consent_stalled": sum(
            1
            for u, p in world.storytellers.items()
            if (p.parent_consent_asks or 0) >= 2 and u not in parent
        ),
        "first_conversation": sum(1 for u in world.storytellers if per_user[u] >= 1),
        "reached_3_conversations": sum(
            1 for u in world.storytellers if per_user[u] >= 3
        ),
        "reached_10_conversations": reached_10,
        "go_no_go_rate": rate(reached_10, len(parent)),
    }


def _length_minutes(s) -> float | None:
    if s.last_user_message_at is None:
        return None
    delta = _aware(s.last_user_message_at) - _aware(s.started_at)
    return delta.total_seconds() / 60


def _conversations_section(world: _World, w: _Windows) -> dict:
    started = [s for s in world.sessions if s.status != "consent"]
    convs = _conversations(world)
    by_window = w.split(convs, lambda s: s.started_at)
    empty = [s for s in started if s.exchange_count == 0 and s.status != "active"]

    def _per_window(fn):
        return {win: fn(by_window[win]) for win in WINDOWS}

    def _lengths(sessions):
        return [m for m in (_length_minutes(s) for s in sessions) if m is not None]

    today = w.now.astimezone(IST).date()
    days = [today - timedelta(days=i) for i in range(29, -1, -1)]
    # Bucketed by IST calendar day: the 30 days ending today in India.
    day_set = set(days)
    per_day: dict[date, list] = defaultdict(list)
    for s in convs:
        d = ist_date(s.started_at)
        if d in day_set and _aware(s.started_at) <= w.now:
            per_day[d].append(s)

    return {
        "sessions_started": w.count(started, lambda s: s.started_at),
        "conversations": w.count(convs, lambda s: s.started_at),
        "empty_sessions": w.count(empty, lambda s: s.started_at),
        "active_now": sum(1 for s in world.sessions if s.status == "active"),
        "ended_completed": _per_window(
            lambda ss: sum(1 for s in ss if s.status == "completed")
        ),
        "ended_timeout": _per_window(
            lambda ss: sum(1 for s in ss if s.status == "abandoned")
        ),
        "ended_reason_breakdown": dict(
            sorted(
                Counter(s.ended_reason or "none" for s in by_window["all_time"]).items()
            )
        ),
        "median_exchanges": _per_window(
            lambda ss: _median([s.exchange_count for s in ss])
        ),
        "goal_met_rate": _per_window(
            lambda ss: rate(sum(1 for s in ss if s.goal_met), len(ss))
        ),
        "median_length_minutes": _per_window(
            lambda ss: round(_median(_lengths(ss)), 1) if _lengths(ss) else None
        ),
        "share_10_min_plus": _per_window(
            lambda ss: rate(sum(1 for m in _lengths(ss) if m >= 10), len(_lengths(ss)))
        ),
        "daily_last_30d": [
            {
                "date_ist": d.isoformat(),
                "conversations": len(per_day[d]),
                "storytellers": len({s.user_id for s in per_day[d]}),
            }
            for d in days
        ],
    }


def _engagement(world: _World, w: _Windows) -> dict:
    convs = [s for s in _conversations(world) if _aware(s.started_at) <= w.now]
    parent = _parent_consent_dates(world)
    today = w.now.astimezone(IST).date()
    window_days = [today - timedelta(days=i) for i in range(6, -1, -1)]

    active_pairs = {
        (s.user_id, ist_date(s.started_at))
        for s in convs
        if ist_date(s.started_at) in window_days and s.user_id in parent
    }
    eligible = sum(
        1
        for u, consented in parent.items()
        for d in window_days
        if d >= ist_date(consented)
    )

    first: dict[str, date] = {}
    for s in convs:
        d = ist_date(s.started_at)
        if s.user_id not in first or d < first[s.user_id]:
            first[s.user_id] = d

    def _retention(min_age: int, lo: int, hi: int) -> dict:
        cohort = [u for u, d in first.items() if (today - d).days >= min_age]
        retained = sum(
            1
            for u in cohort
            if any(
                s.user_id == u and lo <= (ist_date(s.started_at) - first[u]).days <= hi
                for s in convs
            )
        )
        return {"rate": rate(retained, len(cohort)), "cohort": len(cohort)}

    return {
        "active_storytellers_7d": len(
            {s.user_id for s in convs if w.contains("last_7d", s.started_at)}
        ),
        "daily_active_rate_7d": rate(len(active_pairs), eligible),
        "week2_retention": _retention(14, 7, 13),
        "week4_retention": _retention(28, 21, 27),
    }


def _story_atoms(world: _World, w: _Windows) -> dict:
    atoms_by_window = w.split(world.atoms, lambda a: a.created_at)
    convs = w.count(_conversations(world), lambda s: s.started_at)
    known_domains = get_domain_sequence()

    recent_talkers = {
        s.user_id for s in _conversations(world) if w.contains("last_30d", s.started_at)
    }
    atoms_30d = Counter(a.user_id for a in atoms_by_window["last_30d"])

    covered: dict[str, set] = defaultdict(set)
    for a in atoms_by_window["all_time"]:
        if a.domain in known_domains:
            covered[a.user_id].add(a.domain)
    histogram = {n: 0 for n in range(len(known_domains) + 1)}
    for u in world.storytellers:
        histogram[len(covered[u])] += 1

    def _avg(atoms):
        return (
            round(statistics.fmean(a.completeness_score for a in atoms), 2)
            if atoms
            else None
        )

    return {
        "atoms": {win: len(atoms_by_window[win]) for win in WINDOWS},
        "atoms_per_conversation": {
            win: (
                round(len(atoms_by_window[win]) / convs[win], 2) if convs[win] else None
            )
            for win in WINDOWS
        },
        "avg_completeness": {win: _avg(atoms_by_window[win]) for win in WINDOWS},
        "completeness_distribution": {
            score: sum(
                1 for a in atoms_by_window["all_time"] if a.completeness_score == score
            )
            for score in range(6)
        },
        "share_completeness_3_plus": {
            win: rate(
                sum(1 for a in atoms_by_window[win] if a.completeness_score >= 3),
                len(atoms_by_window[win]),
            )
            for win in WINDOWS
        },
        "median_atoms_per_storyteller_30d": _median(
            [atoms_30d[u] for u in recent_talkers]
        ),
        "domain_coverage": {
            "domains": len(known_domains),
            "histogram": histogram,
            "storytellers_6_plus": sum(n for k, n in histogram.items() if k >= 6),
        },
    }


def _memory_cards(world: _World, w: _Windows) -> dict:
    by_window = w.split(world.cards, lambda c: c.created_at)
    failed_statuses = {"failed", "undelivered"}

    def _counts(cards):
        delivered = sum(
            1 for c in cards if c.delivery_status in delivery.DELIVERED_STATUSES
        )
        read = sum(1 for c in cards if c.delivery_status == "read")
        failed = sum(1 for c in cards if c.delivery_status in failed_statuses)
        return {
            "generated": len(cards),
            "delivered": delivered,
            "read": read,
            "failed": failed,
            "pending": len(cards) - delivered - failed,
        }

    per_window = {win: _counts(by_window[win]) for win in WINDOWS}
    return {
        key: {win: per_window[win][key] for win in WINDOWS}
        for key in ("generated", "delivered", "read", "failed", "pending")
    } | {
        "failure_codes": dict(
            sorted(
                Counter(
                    c.delivery_error_code or "none"
                    for c in by_window["all_time"]
                    if c.delivery_status in failed_statuses
                ).items()
            )
        )
    }


_SENT_OR_ON_ITS_WAY = {"approved", "scheduled", "sent"}


def _conversation_cards(world: _World, w: _Windows) -> dict:
    """CONVERSATION_CARDS.md §13. All time unless a window is named."""
    cards = [c for c in world.conv_cards if _aware(c.created_at) <= w.now]
    sent = [c for c in cards if c.status == "sent"]
    select_calls = sum(
        1
        for u in world.usage
        if u.vendor == "anthropic" and u.operation == "card_select"
    )
    # A selection that chose a card wrote a row (drafted, or failed its
    # check); the rest chose none.
    chosen = len(cards)
    reviewed = [c for c in cards if c.operator_reviewed_at is not None]
    review_hours = [
        (_aware(c.operator_reviewed_at) - _aware(c.created_at)).total_seconds() / 3600
        for c in reviewed
    ]
    offered = [c for c in cards if (c.readback_offers or 0) > 0]
    days_to_send = [
        (_aware(c.scheduled_for) - _aware(c.created_at)).total_seconds() / 86400
        for c in sent
        if c.scheduled_for
    ]

    def delivered_rate(rows):
        attempted = [c for c in rows if c.wa_sent]
        done = [
            c for c in attempted if c.wa_delivery_status in delivery.DELIVERED_STATUSES
        ]
        return {
            "sent": len(attempted),
            "delivered": len(done),
            "rate": rate(len(done), len(attempted)),
        }

    by_country = defaultdict(list)
    for c in sent:
        by_country[c.child_country].append(c)

    reactions = Counter(c.child_reaction for c in sent if c.child_reaction)
    questions = [q for q in world.questions if q.kind == "question"]
    return {
        "pipeline": {
            "drafted": w.count(cards, lambda c: c.created_at),
            "selection_calls": select_calls,
            "selection_none_rate": rate(select_calls - chosen, select_calls)
            if select_calls
            else None,
            "failed_fidelity": sum(1 for c in cards if c.status == "failed_fidelity"),
            "operator_approved": sum(
                1 for c in cards if c.operator_action == "approve"
            ),
            "operator_edited": sum(1 for c in cards if c.operator_edited),
            "operator_rejected": sum(1 for c in cards if c.status == "rejected"),
            "waiting_for_review": sum(
                1 for c in cards if c.status == "operator_review"
            ),
            "median_hours_in_review": _median(review_hours),
        },
        "parent": {
            "share_preference": dict(
                sorted(Counter(r.share_preference for r in world.sharing).items())
            ),
            "readback_offers_per_card": round(
                statistics.fmean(c.readback_offers for c in offered), 2
            )
            if offered
            else None,
            "outcomes": {
                "approved": sum(1 for c in cards if c.status in _SENT_OR_ON_ITS_WAY),
                "corrected": sum(1 for c in cards if (c.correction_round or 0) > 0),
                "kept_private": sum(1 for c in cards if c.status == "kept_private"),
                "expired": sum(1 for c in cards if c.status == "expired"),
                "withdrawn": sum(1 for c in cards if c.status == "withdrawn"),
                "waiting_for_her": sum(
                    1 for c in cards if c.status == "awaiting_parent"
                ),
            },
            "median_days_draft_to_sent": _median(days_to_send),
        },
        "delivery": {
            "sent": w.count(sent, lambda c: c.scheduled_for),
            "whatsapp": delivered_rate(sent),
            "whatsapp_by_country": {
                k: delivered_rate(v) for k, v in sorted(by_country.items())
            },
            "whatsapp_skipped": dict(
                sorted(
                    Counter(
                        c.wa_skipped_reason for c in sent if c.wa_skipped_reason
                    ).items()
                )
            ),
            "email_accepted": sum(1 for c in sent if c.email_status == "accepted"),
            "email_failed": sum(1 for c in cards if c.email_status == "failed"),
            "email_turned_off": sum(
                1 for r in world.sharing if r.card_email_enabled is False
            ),
            "whatsapp_stopped": sum(
                1 for r in world.sharing if r.card_whatsapp_enabled is False
            ),
        },
        "child": {
            "didnt_know": reactions.get("didnt_know", 0),
            "knew": reactions.get("knew", 0),
            "questions": len(questions),
            "questions_per_sent_card": rate(len(questions), len(sent)),
            "questions_by_screening": dict(
                sorted(Counter(q.screening or "none" for q in questions).items())
            ),
            "questions_asked_to_her": sum(1 for q in questions if q.status == "asked"),
        },
        # Tagged by hand on /admin/cards/view (§13): she brought up a call
        # with her child about the card, unprompted.
        "calls_brought_up": sum(1 for c in sent if c.call_tag == "brought_up"),
        "calls_tagged": sum(1 for c in sent if c.call_tag),
    }


def _dashboard(world: _World, w: _Windows) -> dict:
    signed_in = {
        t.user_id for t in world.sign_ins if w.contains("last_30d", t.created_at)
    }
    family_ids = {f.user_id for f in world.families}
    count = len(signed_in & family_ids)
    return {
        "sign_ins": w.count(world.sign_ins, lambda t: t.created_at),
        "families_signed_in_30d": {
            "count": count,
            "rate": rate(count, len(family_ids)),
        },
    }


def _usage_row(u) -> SimpleNamespace:
    return SimpleNamespace(
        vendor=u.vendor,
        operation=u.operation,
        model=u.model,
        units=u.units or {},
        # Some prices depend on the day of the call (Gemini TTS).
        created_at=u.created_at,
    )


def _units(window_rows) -> dict:
    """Usage counts behind the cost: tokens, minutes, characters, audio
    seconds and messages, from api_usage rows."""
    claude: dict[str, dict] = defaultdict(
        lambda: {
            "calls": 0,
            "input_tokens": 0,
            "output_tokens": 0,
            "cache_creation_input_tokens": 0,
            "cache_read_input_tokens": 0,
        }
    )
    stt_in = stt_billed = 0.0
    tts_chars = messages_in = messages_out = 0
    # Gemini TTS (GEMINI_TTS.md §6 stage 4): clips and seconds billed, and
    # how often a Gemini reply had to be said by Sarvam instead (D3).
    gemini_clips = vendor_fallbacks = 0
    gemini_seconds = 0.0
    for u in window_rows:
        units = u.units or {}
        if u.vendor == "anthropic":
            op = claude[u.operation]
            op["calls"] += 1
            for key in (
                "input_tokens",
                "output_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            ):
                op[key] += int(units.get(key) or 0)
        elif u.vendor == "sarvam" and u.operation == "stt":
            received = float(units.get("seconds_received") or 0)
            stt_in += received
            stt_billed += float(units.get("seconds_billed") or received)
        elif u.vendor == "sarvam":
            tts_chars += int(units.get("characters") or 0)
            if u.operation == "tts_fallback_vendor":
                vendor_fallbacks += 1
        elif u.vendor == "google":
            gemini_clips += 1
            gemini_seconds += float(units.get("audio_seconds") or 0)
        elif u.vendor == "twilio":
            if u.operation == "inbound":
                messages_in += int(units.get("messages") or 1)
            else:
                messages_out += int(units.get("messages") or 1)
    return {
        "claude_tokens_by_operation": dict(sorted(claude.items())),
        "stt_minutes_received": round(stt_in / 60, 2),
        "stt_minutes_billed": round(stt_billed / 60, 2),
        "tts_characters": tts_chars,
        "tts_gemini_clips": gemini_clips,
        "tts_gemini_audio_minutes": round(gemini_seconds / 60, 2),
        "tts_vendor_fallbacks": vendor_fallbacks,
        "tts_vendor_fallback_rate": rate(
            vendor_fallbacks, gemini_clips + vendor_fallbacks
        ),
        "messages_in": messages_in,
        "messages_out": messages_out,
    }


def _cost(world: _World, w: _Windows, conversations: dict[str, int]) -> dict:
    rows = [u for u in world.usage if _aware(u.created_at) <= w.now]
    by_window = w.split(rows, lambda u: u.created_at)

    def _components(window_rows):
        totals = {c: Decimal(0) for c in pricing.COMPONENTS}
        for u in window_rows:
            parts = pricing.cost_breakdown(_usage_row(u))
            for component, value in (parts or {}).items():
                totals[component] += value
        return totals

    component_totals = {win: _components(by_window[win]) for win in WINDOWS}
    inr_total = {
        win: sum(component_totals[win].values(), Decimal(0)) for win in WINDOWS
    }

    # Dialogue cache hit rate, last 7 days.
    dialogue_7d = [
        u
        for u in by_window["last_7d"]
        if u.vendor == "anthropic" and u.operation == "dialogue"
    ]
    reads = sum(
        int((u.units or {}).get("cache_read_input_tokens") or 0) for u in dialogue_7d
    )
    writes = sum(
        int((u.units or {}).get("cache_creation_input_tokens") or 0)
        for u in dialogue_7d
    )
    uncached = sum(int((u.units or {}).get("input_tokens") or 0) for u in dialogue_7d)

    # Median dialogue input tokens per call by how far into the relationship
    # the session was. After the Layer 3 bound this should stay flat.
    session_numbers = {s.id: s.session_number for s in world.sessions}
    buckets: dict[str, list[int]] = {k: [] for k in ("1-10", "11-30", "31-100", "100+")}
    for u in rows:
        if u.vendor != "anthropic" or u.operation != "dialogue":
            continue
        n = session_numbers.get(u.session_id)
        if n is None:
            continue
        units = u.units or {}
        prompt = sum(
            int(units.get(k) or 0)
            for k in (
                "input_tokens",
                "cache_creation_input_tokens",
                "cache_read_input_tokens",
            )
        )
        key = (
            "1-10"
            if n <= 10
            else "11-30"
            if n <= 30
            else "31-100"
            if n <= 100
            else "100+"
        )
        buckets[key].append(prompt)

    def _wasted(window_rows):
        wasted = [
            u
            for u in window_rows
            if u.vendor == "twilio"
            and u.operation != "inbound"
            and u.status in ("failed", "undelivered")
        ]
        by_code: dict[str, dict] = defaultdict(lambda: {"count": 0, "inr": Decimal(0)})
        for u in wasted:
            entry = by_code[u.error_code or "none"]
            entry["count"] += 1
            entry["inr"] += pricing.cost_inr(_usage_row(u)) or Decimal(0)
        total = sum((e["inr"] for e in by_code.values()), Decimal(0))
        return {
            "count": len(wasted),
            "inr": inr(total),
            "by_error_code": {
                code: {"count": e["count"], "inr": inr(e["inr"])}
                for code, e in sorted(by_code.items())
            },
        }

    unpriced = Counter(
        (u.vendor, u.model)
        for u in rows
        if pricing.cost_breakdown(_usage_row(u)) is None
    )

    return {
        "inr_total": {win: inr(inr_total[win]) for win in WINDOWS},
        "inr_per_conversation": {
            win: (
                inr(inr_total[win] / conversations[win]) if conversations[win] else None
            )
            for win in WINDOWS
        },
        "by_component": {
            win: {
                component: {
                    "inr": inr(value),
                    "share": rate(value, inr_total[win]) if inr_total[win] else None,
                }
                for component, value in component_totals[win].items()
            }
            for win in WINDOWS
        },
        "units": {win: _units(by_window[win]) for win in WINDOWS},
        "dialogue_cache_hit_rate": rate(reads, reads + writes + uncached),
        "dialogue_input_tokens_by_tenure": {
            key: (int(statistics.median(values)) if values else None)
            for key, values in buckets.items()
        },
        "wasted_outbound": {
            "last_7d": _wasted(by_window["last_7d"]),
            "last_30d": _wasted(by_window["last_30d"]),
        },
        "unpriced": [
            {"vendor": vendor, "model": model, "rows": n}
            for (vendor, model), n in sorted(
                unpriced.items(), key=lambda kv: (kv[0][0], kv[0][1] or "")
            )
        ],
        "prices_as_of": pricing.PRICES_AS_OF,
        "usd_inr_rate": float(pricing.usd_inr_rate()),
        "note": "Vendor API calls only; fixed hosting is excluded.",
    }


def _safety(world: _World, w: _Windows) -> dict:
    return {
        "crisis_events": w.count(world.crises, lambda c: c.created_at),
        "by_source": dict(
            sorted(
                Counter(
                    c.source for c in world.crises if _aware(c.created_at) <= w.now
                ).items()
            )
        ),
    }


def _languages(world: _World) -> list[dict]:
    """
    Each language a family uses, with where its fixed messages came from
    (speech/catalogue.py). Decision D1 put machine translations live
    before anyone read them; this keeps that a known quantity.
    """
    counts = Counter(
        p.preferred_language or catalogue.SOURCE_LANGUAGE
        for p in world.storytellers.values()
    )
    rows = []
    for language, families in sorted(counts.items()):
        review = catalogue.review(language)
        rows.append(
            {
                "language": language,
                "families": families,
                "source": review.get("source"),
                "reviewer": review.get("reviewer"),
                "date": review.get("date"),
            }
        )
    return rows


def _plans(world: _World) -> dict:
    plan_of = {f.user_id: f.plan for f in world.families}
    per_user = Counter(s.user_id for s in _conversations(world))
    return {
        "plans": dict(sorted(Counter(f.plan for f in world.families).items())),
        # No family row reads as free, the same way freemium.is_session_allowed
        # treats it.
        "at_free_limit": sum(
            1
            for u in world.storytellers
            if plan_of.get(u, "free") == "free"
            and per_user[u] >= freemium.FREE_SESSION_LIMIT
        ),
        "families_deleted": world.families_deleted,
    }


def _families(world: _World, w: _Windows) -> list[dict]:
    plan_of = {f.user_id: f.plan for f in world.families}
    parent = _parent_consent_dates(world)
    known_domains = get_domain_sequence()
    convs = [s for s in _conversations(world) if _aware(s.started_at) <= w.now]
    cost_rows = [u for u in world.usage if w.contains("last_30d", u.created_at)]

    out = []
    for user_id, profile in world.storytellers.items():
        mine = [s for s in convs if s.user_id == user_id]
        atoms = [a for a in world.atoms if a.user_id == user_id]
        cards = [c for c in world.cards if c.user_id == user_id]
        last = max((_aware(s.started_at) for s in mine), default=None)
        cost_30d = sum(
            (
                pricing.cost_inr(_usage_row(u)) or Decimal(0)
                for u in cost_rows
                if u.user_id == user_id
            ),
            Decimal(0),
        )
        conversations_30d = sum(1 for s in mine if w.contains("last_30d", s.started_at))
        domains = {a.domain for a in atoms if a.domain in known_domains}
        out.append(
            {
                "user_id": user_id,
                "onboarded_at_ist": ist_iso(profile.created_at),
                "parent_consented": user_id in parent,
                "parent_consent_asks": profile.parent_consent_asks or 0,
                "plan": plan_of.get(user_id),
                "conversations_total": len(mine),
                "conversations_7d": sum(
                    1 for s in mine if w.contains("last_7d", s.started_at)
                ),
                "last_conversation_at_ist": ist_iso(last),
                "atoms_total": len(atoms),
                "avg_completeness": (
                    round(statistics.fmean(a.completeness_score for a in atoms), 2)
                    if atoms
                    else None
                ),
                "domains_covered": f"{len(domains)}/{len(known_domains)}",
                "cards_delivered": sum(
                    1 for c in cards if c.delivery_status in delivery.DELIVERED_STATUSES
                ),
                "cards_failed": sum(
                    1 for c in cards if c.delivery_status in ("failed", "undelivered")
                ),
                "cards_sent_30d": sum(
                    1
                    for c in world.conv_cards
                    if c.user_id == user_id
                    and c.status == "sent"
                    and w.contains("last_30d", c.scheduled_for)
                ),
                "last_card_sent_at_ist": ist_iso(
                    max(
                        (
                            _aware(c.scheduled_for)
                            for c in world.conv_cards
                            if c.user_id == user_id
                            and c.status == "sent"
                            and c.scheduled_for
                        ),
                        default=None,
                    )
                ),
                "pending_card_status": next(
                    (
                        c.status
                        for c in world.conv_cards
                        if c.user_id == user_id
                        and c.status
                        in (
                            "draft",
                            "operator_review",
                            "awaiting_parent",
                            "correction",
                            "approved",
                            "scheduled",
                        )
                    ),
                    None,
                ),
                "cost_30d_inr": inr(cost_30d),
                "cost_per_conversation_30d_inr": (
                    inr(cost_30d / conversations_30d) if conversations_30d else None
                ),
                "_sort": last,
            }
        )
    # Most recent conversation first; never-talked last, then by id so the
    # order is stable.
    out.sort(
        key=lambda r: (
            r["_sort"] is None,
            -(r["_sort"].timestamp()) if r["_sort"] else 0,
            r["user_id"],
        )
    )
    for row in out:
        del row["_sort"]
    return out


# ── entry point ───────────────────────────────────────────────────────────────


async def compute_metrics(
    db: AsyncSession, *, now: datetime, excluded_user_ids: frozenset[str]
) -> dict:
    """Every pilot metric as one JSON-ready dict. Reads only."""
    now = _aware(now)
    w = _Windows(now)
    world = await _load(db, excluded_user_ids)
    conversations = _conversations_section(world, w)
    return {
        "generated_at": now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "timezone": "Asia/Kolkata",
        "excluded_user_count": len(excluded_user_ids),
        "funnel": _funnel(world),
        "conversations": conversations,
        "engagement": _engagement(world, w),
        "story_atoms": _story_atoms(world, w),
        "memory_cards": _memory_cards(world, w),
        "conversation_cards": _conversation_cards(world, w),
        "dashboard": _dashboard(world, w),
        "stories": dict(STORIES_PLACEHOLDER),
        "cost": _cost(world, w, conversations["conversations"]),
        "safety": _safety(world, w),
        "plans": _plans(world),
        "languages": _languages(world),
        "families": _families(world, w),
    }


def parse_excluded(raw: str) -> frozenset[str]:
    return frozenset(part.strip() for part in (raw or "").split(",") if part.strip())
