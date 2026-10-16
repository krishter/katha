"""
The operator metrics page: one server-rendered HTML document built from the
dict core.metrics.compute_metrics returns (docs/specs/ADMIN_METRICS.md §8).

Inline CSS, no JavaScript, no external assets, and readable at phone width.
Every value from the database is escaped. The page only ever sees what the
metrics dict holds, which is ids and numbers — it is meant to be safe to
screenshot.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from core.metrics import IST

_CSS = """
:root { color-scheme: light; --ink:#1d1b2e; --soft:#5b5872; --line:#e4e1ea;
  --bg:#faf8f4; --card:#fff; --bar:#3a3488; --warn:#b3261e; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 960px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.3rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 28px 0 8px; padding-top: 12px;
  border-top: 1px solid var(--line); }
.meta, .note { color: var(--soft); font-size: .85rem; }
.strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px; margin: 12px 0; }
.tile { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 10px; }
.tile b { display: block; font-size: 1.4rem; }
.tile.alert b { color: var(--warn); }
.tile span { color: var(--soft); font-size: .8rem; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: .85rem;
  background: var(--card); }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
th { color: var(--soft); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
.bar { height: 8px; background: var(--bar); border-radius: 4px; min-width: 1px; }
.barcell { width: 40%; min-width: 80px; }
ol.funnel { padding-left: 1.2em; }
ol.funnel li { margin: 2px 0; }
"""


def _e(value) -> str:
    return escape("—" if value is None else str(value))


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _inr(value) -> str:
    return "—" if value is None else f"₹{value:,.2f}"


def _ist(stamp: str | None) -> str:
    if not stamp:
        return "—"
    try:
        dt = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return _e(stamp)
    return _e(dt.astimezone(IST).strftime("%d %b %Y, %H:%M IST"))


def _windows_row(label: str, d: dict, fmt=_e) -> str:
    return (
        f"<tr><td>{_e(label)}</td>"
        f"<td class=num>{fmt(d.get('all_time'))}</td>"
        f"<td class=num>{fmt(d.get('last_7d'))}</td>"
        f"<td class=num>{fmt(d.get('last_30d'))}</td></tr>"
    )


def _windows_table(rows: list[str]) -> str:
    return (
        "<div class=scroll><table><tr><th></th><th class=num>All time</th>"
        "<th class=num>7 days</th><th class=num>30 days</th></tr>"
        + "".join(rows)
        + "</table></div>"
    )


def _tile(label: str, value: str, alert: bool = False) -> str:
    cls = "tile alert" if alert else "tile"
    return f"<div class='{cls}'><b>{value}</b><span>{_e(label)}</span></div>"


def _bar_rows(items: list[tuple[str, float, str]]) -> str:
    peak = max((v for _, v, _ in items), default=0) or 1
    return "".join(
        f"<tr><td>{_e(label)}</td><td class=num>{shown}</td>"
        f"<td class=barcell><div class=bar style='width:{100 * v / peak:.1f}%'>"
        "</div></td></tr>"
        for label, v, shown in items
    )


def _funnel(f: dict) -> str:
    steps = [
        ("Family accounts", "family_accounts"),
        ("Onboarding complete", "onboarding_complete"),
        ("Buyer consented", "buyer_consented"),
        ("Storytellers", "storytellers"),
        ("Parent consented", "parent_consented"),
        ("First conversation", "first_conversation"),
        ("Reached 3 conversations", "reached_3_conversations"),
        ("Reached 10 conversations", "reached_10_conversations"),
    ]
    items, previous = [], None
    for label, key in steps:
        value = f.get(key, 0)
        step = ""
        if previous:
            share = _pct(round(value / previous, 3))
            step = f" <span class=note>({share} of previous)</span>"
        items.append(f"<li>{_e(label)}: <b>{_e(value)}</b>{step}</li>")
        previous = value
    return (
        "<ol class=funnel>"
        + "".join(items)
        + "</ol>"
        + f"<p>Go/no-go (10+ conversations ÷ parent consented): "
        f"<b>{_pct(f.get('go_no_go_rate'))}</b> — PRD §12.1 asks for 50%. "
        f"Parent consent stalled after two unclear answers: "
        f"<b>{_e(f.get('parent_consent_stalled'))}</b>.</p>"
    )


def _conversations(c: dict) -> str:
    rows = [
        _windows_row("Sessions started", c["sessions_started"]),
        _windows_row("Conversations", c["conversations"]),
        _windows_row("Empty sessions (nothing delivered)", c["empty_sessions"]),
        _windows_row("Ended: completed", c["ended_completed"]),
        _windows_row("Ended: timed out", c["ended_timeout"]),
        _windows_row("Median exchanges", c["median_exchanges"]),
        _windows_row("Goal met", c["goal_met_rate"], _pct),
        _windows_row("Median length (min)", c["median_length_minutes"]),
        _windows_row("10 min or longer (target 70%)", c["share_10_min_plus"], _pct),
    ]
    reasons = (
        ", ".join(f"{_e(k)} {_e(v)}" for k, v in c["ended_reason_breakdown"].items())
        or "—"
    )
    daily = _bar_rows(
        [
            (
                d["date_ist"],
                d["conversations"],
                f"{d['conversations']} · {d['storytellers']} storyteller(s)",
            )
            for d in c["daily_last_30d"]
        ]
    )
    return (
        _windows_table(rows) + f"<p class=note>Active now: {_e(c['active_now'])}. "
        f"End reasons (all time): {reasons}.</p>"
        + "<h3 class=meta>Last 30 days, by IST date</h3>"
        + f"<div class=scroll><table>{daily}</table></div>"
    )


def _engagement(e: dict) -> str:
    def _retention(r):
        return f"{_pct(r['rate'])} (cohort {_e(r['cohort'])})"

    return (
        "<div class=scroll><table>"
        f"<tr><td>Active storytellers, 7 days</td>"
        f"<td class=num>{_e(e['active_storytellers_7d'])}</td></tr>"
        f"<tr><td>Daily active rate, 7 days (target 60%)</td>"
        f"<td class=num>{_pct(e['daily_active_rate_7d'])}</td></tr>"
        f"<tr><td>Week 2 retention</td>"
        f"<td class=num>{_retention(e['week2_retention'])}</td></tr>"
        f"<tr><td>Week 4 retention</td>"
        f"<td class=num>{_retention(e['week4_retention'])}</td></tr>"
        "</table></div>"
        "<p class=note>Week 4 is the closest honest proxy for the PRD's 30-day "
        "retention. With a pilot this small, read every rate with its cohort.</p>"
    )


def _atoms(a: dict) -> str:
    rows = [
        _windows_row("Story atoms", a["atoms"]),
        _windows_row("Atoms per conversation", a["atoms_per_conversation"]),
        _windows_row("Average completeness (target 3.5)", a["avg_completeness"]),
        _windows_row("Completeness 3 or more", a["share_completeness_3_plus"], _pct),
    ]
    dist = " · ".join(
        f"{k}: {_e(v)}" for k, v in a["completeness_distribution"].items()
    )
    cov = a["domain_coverage"]
    hist = " · ".join(f"{k}: {_e(v)}" for k, v in cov["histogram"].items())
    return (
        _windows_table(rows)
        + f"<p class=note>Completeness scores, all time — {dist}</p>"
        + f"<p>Median atoms per storyteller who talked in the last 30 days "
        f"(target 20+): <b>{_e(a['median_atoms_per_storyteller_30d'])}</b></p>"
        + f"<p>Life chapters covered, storytellers per count of {_e(cov['domains'])} "
        f"— {hist}. Six or more: <b>{_e(cov['storytellers_6_plus'])}</b> "
        "(target: half by month 3).</p>"
    )


def _cards(m: dict) -> str:
    rows = [
        _windows_row(label, m[key])
        for label, key in (
            ("Generated", "generated"),
            ("Delivered", "delivered"),
            ("Read", "read"),
            ("Failed", "failed"),
            ("Pending", "pending"),
        )
    ]
    codes = (
        ", ".join(f"{_e(k)} × {_e(v)}" for k, v in m["failure_codes"].items()) or "none"
    )
    return (
        _windows_table(rows)
        + f"<p class=note>Failure codes, all time: {codes} (63016 = outside the "
        "24-hour window, needs an approved template). WhatsApp reports “read” only "
        "when the recipient has read receipts on, so Read is a floor.</p>"
    )


def _dashboard(d: dict) -> str:
    s = d["families_signed_in_30d"]
    return (
        _windows_table([_windows_row("Sign-ins", d["sign_ins"])])
        + f"<p>Families signed in, last 30 days: <b>{_e(s['count'])}</b> "
        f"({_pct(s['rate'])}; PRD target 40%).</p>"
        "<p class=note>A proxy: magic-link sign-ins, not dashboard visits.</p>"
    )


def _cost(c: dict) -> str:
    rows = [
        _windows_row("Total", c["inr_total"], _inr),
        _windows_row("Per conversation", c["inr_per_conversation"], _inr),
    ]
    labels = {
        "claude_dialogue": "Claude · dialogue",
        "claude_extraction": "Claude · extraction",
        "claude_entities": "Claude · entities",
        "claude_other": "Claude · other",
        "sarvam_stt": "Sarvam · speech to text",
        "sarvam_tts": "Sarvam · text to speech",
        "twilio": "Twilio",
        "meta": "Meta templates",
    }
    parts = c["by_component"]["last_30d"]
    bars = _bar_rows(
        [
            (
                labels.get(k, k),
                v["inr"],
                f"{_inr(v['inr'])} · {_pct(v['share'])}",
            )
            for k, v in parts.items()
        ]
    )
    tenure = " · ".join(
        f"sessions {_e(k)}: {_e(v)}"
        for k, v in c["dialogue_input_tokens_by_tenure"].items()
    )
    unpriced = (
        ", ".join(
            f"{_e(u['vendor'])} {_e(u['model'])} ({_e(u['rows'])} rows)"
            for u in c["unpriced"]
        )
        or "none"
    )
    wasted = c["wasted_outbound"]["last_30d"]
    codes = (
        ", ".join(
            f"{_e(k)} × {_e(v['count'])} ({_inr(v['inr'])})"
            for k, v in wasted["by_error_code"].items()
        )
        or "none"
    )
    return (
        _windows_table(rows)
        + "<h3 class=meta>By component, last 30 days</h3>"
        + f"<div class=scroll><table>{bars}</table></div>"
        + f"<p>Dialogue cache hit rate, 7 days (target above 60%): "
        f"<b>{_pct(c['dialogue_cache_hit_rate'])}</b></p>"
        + f"<p>Median dialogue prompt tokens by session number — {tenure}. "
        "Should stay flat as relationships lengthen.</p>"
        + f"<p>Wasted outbound, 30 days: <b>{_e(wasted['count'])}</b> messages, "
        f"{_inr(wasted['inr'])} — {codes}</p>"
        + f"<p class=note>Unpriced: {unpriced}. Prices as of "
        f"{_e(c['prices_as_of'])} (core/pricing.py), USD→INR {_e(c['usd_inr_rate'])}. "
        f"{_e(c['note'])}</p>"
    )


def _safety_and_plans(s: dict, p: dict) -> str:
    sources = ", ".join(f"{_e(k)} {_e(v)}" for k, v in s["by_source"].items()) or "none"
    plans = ", ".join(f"{_e(k)} {_e(v)}" for k, v in p["plans"].items()) or "none"
    return (
        _windows_table([_windows_row("Crisis events", s["crisis_events"])])
        + f"<p class=note>By source, all time: {sources}. Follow-up is in the "
        "runbook.</p>"
        + f"<p>Plans: {plans}. At the free limit: <b>{_e(p['at_free_limit'])}</b>. "
        f"Families deleted (DPDP): <b>{_e(p['families_deleted'])}</b>.</p>"
    )


def _families(rows: list[dict]) -> str:
    cols = [
        ("user_id", "Storyteller", False),
        ("onboarded_at_ist", "Onboarded", False),
        ("parent_consented", "Parent consent", False),
        ("parent_consent_asks", "Asks", True),
        ("plan", "Plan", False),
        ("conversations_total", "Convs", True),
        ("conversations_7d", "Convs 7d", True),
        ("last_conversation_at_ist", "Last conversation", False),
        ("atoms_total", "Atoms", True),
        ("avg_completeness", "Avg 5W", True),
        ("domains_covered", "Chapters", True),
        ("cards_delivered", "Cards ✓", True),
        ("cards_failed", "Cards ✗", True),
        ("cost_30d_inr", "₹ 30d", True),
        ("cost_per_conversation_30d_inr", "₹/conv 30d", True),
    ]
    head = "".join(
        f"<th{' class=num' if num else ''}>{_e(label)}</th>" for _, label, num in cols
    )

    def _cell(key, value):
        if key.endswith("_ist"):
            return _ist(value)
        if key.endswith("_inr"):
            return _inr(value)
        if key == "parent_consented":
            return "yes" if value else "no"
        return _e(value)

    body = "".join(
        "<tr>"
        + "".join(
            f"<td{' class=num' if num else ''}>{_cell(key, row.get(key))}</td>"
            for key, _, num in cols
        )
        + "</tr>"
        for row in rows
    )
    return f"<div class=scroll><table><tr>{head}</tr>{body}</table></div>"


def render(m: dict) -> str:
    conv = m["conversations"]
    cards = m["memory_cards"]
    cost = m["cost"]
    empty_7d = conv["empty_sessions"]["last_7d"]
    failed_7d = cards["failed"]["last_7d"]
    crisis_7d = m["safety"]["crisis_events"]["last_7d"]
    wasted_7d = cost["wasted_outbound"]["last_7d"]
    strip = "".join(
        [
            _tile("Empty sessions, 7d", _e(empty_7d), alert=bool(empty_7d)),
            _tile("Failed cards, 7d", _e(failed_7d), alert=bool(failed_7d)),
            _tile("Crisis events, 7d", _e(crisis_7d), alert=bool(crisis_7d)),
            _tile("Active now", _e(conv["active_now"])),
            _tile(
                "₹ per conversation, 7d", _inr(cost["inr_per_conversation"]["last_7d"])
            ),
            _tile(
                "Wasted outbound, 7d",
                f"{_e(wasted_7d['count'])} · {_inr(wasted_7d['inr'])}",
                alert=bool(wasted_7d["count"]),
            ),
        ]
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Katha · pilot metrics</title><style>{_CSS}</style></head>
<body><main>
<h1>Katha pilot metrics</h1>
<p class=meta>Generated {_ist(m["generated_at"])} · {_e(m["excluded_user_count"])}
account(s) excluded · day boundaries in IST</p>
<div class=strip>{strip}</div>
<h2>Onboarding funnel</h2>{_funnel(m["funnel"])}
<h2>Conversations</h2>{_conversations(conv)}
<h2>Engagement and retention</h2>{_engagement(m["engagement"])}
<h2>Story atoms</h2>{_atoms(m["story_atoms"])}
<h2>Memory cards</h2>{_cards(cards)}
<h2>Family dashboard</h2>{_dashboard(m["dashboard"])}
<h2>Stories</h2><p class=note>{_e(m["stories"]["reason"])}</p>
<h2>Cost to serve</h2>{_cost(cost)}
<h2>Safety and plans</h2>{_safety_and_plans(m["safety"], m["plans"])}
<h2>Families</h2>{_families(m["families"])}
</main></body></html>"""
