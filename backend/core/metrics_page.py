"""
The operator metrics page: one server-rendered HTML document built from the
dict core.metrics.compute_metrics returns (docs/specs/ADMIN_METRICS.md §8,
laid out by docs/specs/METRICS_PAGE_LAYOUT.md §5).

Four tiers, most urgent first: what needs attention now, the pilot
scorecard, the detail grouped by question (native <details>, open when
something in it needs attention), and the families, quietest first.

Inline CSS, no JavaScript, no external assets, and readable at phone width.
Every value from the database is escaped. The page only ever sees what the
metrics dict holds, which is ids and numbers — it is meant to be safe to
screenshot.
"""

from __future__ import annotations

from datetime import datetime
from html import escape

from core.metrics import (
    FAMILY_STATUS_ORDER,
    IST,
    QUIET_ORANGE_DAYS,
    QUIET_RED_DAYS,
    family_status,
)

# ── lines (METRICS_PAGE_LAYOUT.md §5.1) ───────────────────────────────────────
# An attention tile appears once its number is past its line.
CRISIS_EVENTS_7D_OVER = 0
EMPTY_SESSIONS_7D_OVER = 0
FAILED_MEMORY_CARDS_7D_OVER = 0
WASTED_OUTBOUND_7D_OVER = 0
VOICE_FALLBACK_RATE_OVER = 0.01
RETRIEVAL_FALLBACK_RATE_OVER = 0.01
CONSENT_STALLED_OVER = 0
FAMILY_NUMBER_WAITING_48H_OVER = 0
# A scorecard group smaller than this is called out as a small group.
SMALL_GROUP = 10
# "Close" to a target: at least this share of it.
CLOSE_SHARE = 0.9

_CSS = """
:root { color-scheme: light; --ink:#1d1b2e; --soft:#5b5872; --line:#e4e1ea;
  --bg:#faf8f4; --card:#fff; --bar:#3a3488; --warn:#b3261e;
  --red:#9c1f18; --red-bg:#fbeae8; --orange:#8f4a06; --orange-bg:#fdf0e0;
  --blue:#1f4f99; --blue-bg:#e6eefb; --grey:#5b5872; --grey-bg:#efedf3; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1040px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.3rem; margin: 0 0 4px; }
h2 { font-size: 1.1rem; margin: 28px 0 8px; }
h3 { font-size: .95rem; margin: 18px 0 6px; }
.meta, .note { color: var(--soft); font-size: .85rem; }
nav.top { display: flex; gap: 4px; overflow-x: auto; -webkit-overflow-scrolling: touch;
  margin: 12px 0 4px; padding-bottom: 4px; }
nav.top a { flex: none; display: flex; align-items: center; min-height: 44px;
  padding: 0 12px; border: 1px solid var(--line); border-radius: 22px;
  background: var(--card); color: var(--ink);
  text-decoration: none; font-size: .85rem; }
.strip { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr));
  gap: 8px; margin: 12px 0; }
.attn { display: grid; grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px; margin: 8px 0; }
a.alert { display: block; min-height: 44px; padding: 12px; border-radius: 8px;
  text-decoration: none; border: 1px solid; }
a.alert b { display: block; font-size: 1.5rem; line-height: 1.2; }
a.alert span { display: block; font-size: .85rem; }
a.alert.red { background: var(--red-bg); color: var(--red); border-color: var(--red); }
a.alert.orange { background: var(--orange-bg); color: var(--orange);
  border-color: var(--orange); }
.clear { margin: 6px 0; color: var(--soft); font-size: .85rem; }
.score { display: grid; grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  gap: 8px; }
.sc { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px; }
.sc.dashed { border-style: dashed; }
.sc .head { display: flex; justify-content: space-between; gap: 8px;
  align-items: flex-start; font-size: .85rem; color: var(--soft); }
.sc .big { font-size: 1.6rem; font-weight: 700; margin: 4px 0 0; }
.sc .of { font-size: .8rem; color: var(--soft); }
.track { position: relative; height: 8px; background: var(--grey-bg);
  border-radius: 4px;
  margin: 10px 0 6px; }
.track .fill { height: 8px; background: var(--bar); border-radius: 4px; }
.track .tick { position: absolute; top: -3px; width: 2px; height: 14px;
  background: #000; }
.sc .foot { font-size: .78rem; color: var(--soft); }
.sc a { color: var(--blue); }
.pill { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: .78rem;
  font-weight: 600; white-space: nowrap; }
.pill.red { background: var(--red-bg); color: var(--red); }
.pill.orange { background: var(--orange-bg); color: var(--orange); }
.pill.blue { background: var(--blue-bg); color: var(--blue); }
.pill.grey { background: var(--grey-bg); color: var(--grey); }
details.group { background: var(--card); border: 1px solid var(--line);
  border-radius: 8px; margin: 10px 0; padding: 0 14px; }
details.group > summary { cursor: pointer; min-height: 44px; padding: 12px 0;
  list-style-position: outside; }
details.group > summary b { font-size: 1rem; }
details.group > summary .q { color: var(--soft); font-size: .85rem; }
details.group > summary .line { display: block; font-size: .85rem; margin-top: 2px; }
details.group[open] > summary { border-bottom: 1px solid var(--line);
  margin-bottom: 8px; }
details.group > div { padding-bottom: 14px; }
details.more > summary { cursor: pointer; min-height: 44px; display: flex;
  align-items: center; color: var(--blue); }
.tracks { display: grid; grid-template-columns: 1fr 1fr; gap: 16px; }
@media (max-width: 700px) { .tracks { grid-template-columns: 1fr; } }
.steps { width: 100%; }
.steps td { white-space: normal; }
.steps td.indent { padding-left: 22px; }
.bar { height: 8px; background: var(--bar); border-radius: 4px; min-width: 1px; }
.bar.red { background: var(--red); }
.barcell { width: 40%; min-width: 80px; }
.scroll { overflow-x: auto; -webkit-overflow-scrolling: touch; }
table { border-collapse: collapse; width: 100%; font-size: .85rem;
  background: var(--card); }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line);
  white-space: nowrap; }
th { color: var(--soft); font-weight: 600; }
td.num, th.num { text-align: right; font-variant-numeric: tabular-nums; }
td.wk, th.wk { font-weight: 700; }
.state { display: inline-block; padding: 1px 8px; border-radius: 10px; font-size: .8rem;
  font-weight: 600; }
.state.ok { background: #e3f1e6; color: #1e5e2c; }
.state.warn { background: #fbefd5; color: #7a4b00; }
.state.bad { background: #fbe0de; color: var(--warn); }
td.why { white-space: normal; min-width: 180px; }
ul.flist { list-style: none; padding: 0; margin: 0; display: none; }
ul.flist li { background: var(--card); border: 1px solid var(--line);
  border-radius: 8px;
  padding: 10px 12px; margin: 6px 0; }
ul.flist .id { font-weight: 600; word-break: break-all; }
@media (max-width: 600px) {
  .ftable { display: none; }
  ul.flist { display: block; }
}
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
        f"<td class='num wk'>{fmt(d.get('last_7d'))}</td>"
        f"<td class=num>{fmt(d.get('last_30d'))}</td>"
        f"<td class=num>{fmt(d.get('all_time'))}</td></tr>"
    )


def _windows_table(rows: list[str]) -> str:
    return (
        "<div class=scroll><table><tr><th></th><th class='num wk'>7 days</th>"
        "<th class=num>30 days</th><th class=num>All time</th></tr>"
        + "".join(rows)
        + "</table></div>"
    )


def _bar_rows(items: list[tuple[str, float, str]]) -> str:
    peak = max((v for _, v, _ in items), default=0) or 1
    return "".join(
        f"<tr><td>{_e(label)}</td><td class=num>{shown}</td>"
        f"<td class=barcell><div class=bar style='width:{100 * v / peak:.1f}%'>"
        "</div></td></tr>"
        for label, v, shown in items
    )


def _step(label: str, value, base, of_label: str, total, red=False, indent=False):
    """One funnel row: label, a bar against all families, the count, and its
    share of the step it follows."""
    width = 100 * (value or 0) / total if total else 0
    share = f"{_pct(round(value / base, 3))} of {of_label}" if base else ""
    return (
        f"<tr><td{' class=indent' if indent else ''}>{_e(label)}</td>"
        f"<td class=barcell><div class='bar{' red' if red else ''}' "
        f"style='width:{width:.1f}%'></div></td>"
        f"<td class=num><b>{_e(value)}</b></td>"
        f"<td class=note>{_e(share)}</td></tr>"
    )


def _funnel_tracks(f: dict, signed_in_30d: int) -> str:
    """METRICS_PAGE_LAYOUT.md §5.3: the parent's track and the child's,
    side by side."""
    total = f.get("family_accounts") or 0
    parent_steps = [
        ("Family accounts", "family_accounts"),
        ("Onboarding complete", "onboarding_complete"),
        ("Buyer consented", "buyer_consented"),
        ("Parent consented", "parent_consented"),
        ("First conversation", "first_conversation"),
        ("3 conversations", "reached_3_conversations"),
        ("10 conversations", "reached_10_conversations"),
    ]
    rows, previous, previous_label = [], None, ""
    for label, key in parent_steps:
        value = f.get(key, 0)
        rows.append(_step(label, value, previous, previous_label.lower(), total))
        previous, previous_label = value, label
    n = f.get("family_number") or {}
    given = n.get("given", 0)
    child = [
        _step("Number given", given, total, "family accounts", total),
        _step("Number confirmed", n.get("confirmed", 0), given, "given", total),
        _step(
            "Waiting, code live",
            n.get("waiting_code_live", 0),
            given,
            "given",
            total,
            indent=True,
        ),
        _step(
            "Waiting, no live code",
            n.get("waiting_no_live_code", 0),
            given,
            "given",
            total,
            red=True,
            indent=True,
        ),
        _step("Signed in, 30 days", signed_in_30d, total, "family accounts", total),
    ]
    return (
        "<div class=tracks>"
        "<div><h3>Parent</h3><div class=scroll><table class=steps>"
        + "".join(rows)
        + "</table></div>"
        f"<p class=note>Parent consent stalled after two unclear answers: "
        f"<b>{_e(f.get('parent_consent_stalled'))}</b>.</p></div>"
        "<div><h3>Child</h3><div class=scroll><table class=steps>"
        + "".join(child)
        + "</table></div>"
        "<p class=note>Until the child sends CONFIRM from that phone, nothing of "
        "hers goes to that WhatsApp number; cards go by email only. Codes last 7 "
        "days.</p><p class=note>Numbers that existed before confirmation shipped "
        "count as confirmed.</p></div></div>"
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
        "(target: half by month 3).</p>" + _embeddings(a["embeddings"])
    )


def _embeddings(e: dict) -> str:
    """SEMANTIC_RETRIEVAL.md §7 stage 5: pending should trend to zero."""
    return (
        f"<p>Embedded for Layer 3, all time: <b>{_e(e['atoms_embedded'])}</b> of "
        f"{_e(e['atoms_total'])} ({_pct(e['share_embedded'])}). Pending "
        f"<b>{_e(e['atoms_pending'])}</b>, failed <b>{_e(e['atoms_failed'])}</b> "
        "(the hourly sweep retries both).</p>"
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


def _kv(d: dict) -> str:
    return ", ".join(f"{_e(k)} × {_e(v)}" for k, v in d.items()) or "none"


def _conversation_cards(c: dict) -> str:
    """CONVERSATION_CARDS.md §13."""
    pipe, parent, deliv, child = c["pipeline"], c["parent"], c["delivery"], c["child"]
    rows = [
        _windows_row("Drafted", pipe["drafted"]),
        _windows_row("Sent", deliv["sent"]),
    ]
    wa = deliv["whatsapp"]
    by_country = (
        " · ".join(
            f"{_e(k)}: {_e(v['delivered'])}/{_e(v['sent'])}"
            for k, v in deliv["whatsapp_by_country"].items()
        )
        or "none"
    )
    return (
        _windows_table(rows)
        + "<ul class=note>"
        + f"<li>Selection: {_e(pipe['selection_calls'])} calls, none-rate "
        f"{_pct(pipe['selection_none_rate'])}; failed fidelity "
        f"{_e(pipe['failed_fidelity'])}</li>"
        + f"<li>Operator: approved {_e(pipe['operator_approved'])}, edited "
        f"{_e(pipe['operator_edited'])}, rejected {_e(pipe['operator_rejected'])}, "
        f"waiting {_e(pipe['waiting_for_review'])}; median hours in review "
        f"{_e(pipe['median_hours_in_review'])}</li>"
        + f"<li>Sharing preference: {_kv(parent['share_preference'])}</li>"
        + f"<li>Her answers: {_kv(parent['outcomes'])}; offers per card "
        f"{_e(parent['readback_offers_per_card'])}; median days draft to sent "
        f"{_e(parent['median_days_draft_to_sent'])}</li>"
        + f"<li>WhatsApp delivered {_e(wa['delivered'])}/{_e(wa['sent'])} "
        f"({_pct(wa['rate'])}); by country {by_country}; skipped "
        f"{_kv(deliv['whatsapp_skipped'])}; stopped {_e(deliv['whatsapp_stopped'])}"
        "</li>"
        + f"<li>Email accepted by SES {_e(deliv['email_accepted'])} (not the same "
        f"as delivered), failed {_e(deliv['email_failed'])}, turned off "
        f"{_e(deliv['email_turned_off'])}</li>"
        + f"<li>Child: didn't know {_e(child['didnt_know'])}, knew "
        f"{_e(child['knew'])}; questions {_e(child['questions'])} "
        f"({_kv(child['questions_by_screening'])}), asked to her "
        f"{_e(child['questions_asked_to_her'])}</li>"
        + f"<li><b>Calls she brought up: {_e(c['calls_brought_up'])}</b> of "
        f"{_e(c['calls_tagged'])} tagged (tag on /admin/cards/view)</li>" + "</ul>"
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
        "claude_cards": "Claude · conversation cards",
        "claude_other": "Claude · other",
        "sarvam_stt": "Sarvam · speech to text",
        "sarvam_tts": "Sarvam · text to speech",
        "google_tts": "Google · text to speech",
        "google_embeddings": "Google · embeddings (Mumbai)",
        "twilio": "Twilio",
        "meta": "Meta templates",
        "ses": "AWS SES · card emails",
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


def _gemini_tts(u: dict) -> str:
    """GEMINI_TTS.md §6 stage 4: watch the fallbacks to Sarvam (under 1%)."""
    return (
        f"<p>Gemini voice, 30 days: <b>{_e(u['tts_gemini_clips'])}</b> clips, "
        f"{_e(u['tts_gemini_audio_minutes'])} min. Said by Sarvam instead "
        f"because Gemini failed: <b>{_e(u['tts_vendor_fallbacks'])}</b> "
        f"({_pct(u['tts_vendor_fallback_rate'])}; keep under 1%).</p>"
    )


def _embedding_queries(u: dict) -> str:
    """SEMANTIC_RETRIEVAL.md §7 stage 5: query fallbacks under 1% of sessions."""
    return (
        f"<p>Retrieval queries, 30 days: <b>{_e(u['embedding_query_calls'])}</b> "
        f"embedded, <b>{_e(u['embedding_query_fallbacks'])}</b> fell back to "
        f"recency ({_pct(u['embedding_query_fallback_rate'])}; keep under 1%). "
        f"Atoms embedded: {_e(u['embedding_document_calls'])}; "
        f"{_e(u['embedding_tokens'])} tokens.</p>"
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


def _languages(rows: list[dict]) -> str:
    if not rows:
        return "<p class=note>No families yet.</p>"
    body = "".join(
        f"<tr><td>{_e(r['language'])}</td><td>{_e(r['families'])}</td>"
        f"<td>{'<b>machine</b>' if r['source'] == 'machine' else _e(r['source'])}"
        f"</td><td>{_e(r['reviewer'] or '')}</td><td>{_e(r['date'] or '')}</td></tr>"
        for r in rows
    )
    return (
        "<div class=scroll><table><tr><th>Language</th><th>Families</th>"
        "<th>Fixed messages</th><th>Reviewed by</th><th>Date</th></tr>"
        f"{body}</table></div>"
        "<p class=note>Where the consent, apology and crisis messages for each "
        "language came from. <b>machine</b> means no native speaker has read "
        "them yet (backend/speech/messages/).</p>"
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
        ("cards_sent_30d", "Conv. cards 30d", True),
        ("last_card_sent_at_ist", "Last conv. card", False),
        ("pending_card_status", "Card in progress", False),
        ("cost_30d_inr", "₹ 30d", True),
        ("cost_per_conversation_30d_inr", "₹/conv 30d", True),
        ("family_number", "Family number", False),
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
            f"<td{' class=num' if num else ''}>"
            + (
                _e(_family_number_word(row))
                if key == "family_number"
                else _cell(key, row.get(key))
            )
            + "</td>"
            for key, _, num in cols
        )
        + "</tr>"
        for row in rows
    )
    return f"<div class=scroll><table><tr>{head}</tr>{body}</table></div>"


_STATE_WORDS = {"ok": "Working", "warn": "Check soon", "bad": "Act now"}


def _check(c: dict) -> str:
    if c.get("ok") is None:
        return "not checked"
    word = "passed" if c["ok"] else f"failed ({c.get('status') or '?'})"
    return f"{word}{' · ' + c['detail'] if c.get('detail') else ''}"


def _api_keys(rows: list[dict]) -> str:
    """Each key's health and renew-by date (core/key_health.py). Last four
    characters only."""
    if not rows:
        return "<p class=note>Not available.</p>"
    head = "".join(
        f"<th>{h}</th>"
        for h in (
            "Key",
            "State",
            "Ends",
            "Last success",
            "Last failure",
            "Live check",
            "Renew by",
            "Why",
        )
    )
    body = ""
    for r in rows:
        f = r.get("last_failure")
        failure = f"{_ist(f['at'])} ({_e(f['status'])})" if f else "—"
        state = r["state"]
        word = _STATE_WORDS.get(state, state)
        body += (
            f"<tr><td>{_e(r['label'])}</td>"
            f"<td><span class='state {_e(state)}'>{_e(word)}</span></td>"
            f"<td>{_e(r['fingerprint'] or None)}</td>"
            f"<td>{_ist(r['last_success'])}</td>"
            f"<td>{failure}</td>"
            f"<td>{_e(_check(r['check']))}</td>"
            f"<td>{_e(r['renew_by'])}</td>"
            f"<td class=why>{_e('; '.join(r['reasons']) or None)}</td></tr>"
        )
    return (
        f"<div class=scroll><table><tr>{head}</tr>{body}</table></div>"
        "<p class=note>None of these keys expire by themselves. Renew-by dates "
        "come from KEY_RENEW_BY in backend/.env (name=YYYY-MM-DD, comma "
        "separated); a key turns amber 14 days before and red after, and you "
        "are emailed once a day. Last failure is kept in memory since the last "
        "restart. Sarvam has no free check, so only its last success shows.</p>"
    )


# ── tier 1: needs attention ───────────────────────────────────────────────────


def _over(value, line) -> bool:
    """Past the line. A missing number (None) counts as clear."""
    return value is not None and value > line


def _attention_items(m: dict) -> list[dict]:
    """Every attention check, crossed or not, in the order the page shows
    them. Each has: name, crossed, level, link, big, line, clear."""
    conv, cost = m["conversations"], m["cost"]
    units = cost["units"]["last_30d"]
    crisis = m["safety"]["crisis_events"]["last_7d"]
    empty = conv["empty_sessions"]["last_7d"]
    failed = m["memory_cards"]["failed"]["last_7d"]
    wasted = cost["wasted_outbound"]["last_7d"]
    voice = units.get("tts_vendor_fallback_rate")
    retrieval = units.get("embedding_query_fallback_rate")
    machine = [
        r
        for r in m.get("languages", [])
        if r.get("source") == "machine" and (r.get("families") or 0) > 0
    ]
    stalled = m["funnel"].get("parent_consent_stalled") or 0
    number = m["funnel"].get("family_number") or {}
    waiting = number.get("waiting_over_48h") or 0
    waiting_no_code = number.get("waiting_over_48h_no_live_code") or 0
    keys = m.get("api_keys") or []
    keys_off = [k for k in keys if k.get("state") != "ok"]
    return [
        dict(
            name="crisis events",
            crossed=_over(crisis, CRISIS_EVENTS_7D_OVER),
            level="red",
            link="ops",
            big=_e(crisis),
            line="crisis events, 7 days · follow up per the runbook",
            clear=f"crisis events {_e(crisis)}",
        ),
        dict(
            name="API keys",
            crossed=bool(keys_off),
            level="red" if any(k.get("state") == "bad" for k in keys_off) else "orange",
            link="ops",
            big=_e(len(keys_off)),
            line="API keys to look at",
            clear="API keys ok",
        ),
        dict(
            name="empty sessions",
            crossed=_over(empty, EMPTY_SESSIONS_7D_OVER),
            level="orange",
            link="engagement",
            big=_e(empty),
            line="empty sessions, 7 days · nothing was delivered",
            clear=f"empty sessions {_e(empty)}",
        ),
        dict(
            name="failed memory cards",
            crossed=_over(failed, FAILED_MEMORY_CARDS_7D_OVER),
            level="orange",
            link="family",
            big=_e(failed),
            line="memory cards failed, 7 days",
            clear=f"failed memory cards {_e(failed)}",
        ),
        dict(
            name="wasted outbound",
            crossed=_over(wasted["count"], WASTED_OUTBOUND_7D_OVER),
            level="orange",
            link="cost",
            big=_e(wasted["count"]),
            line=f"outbound messages wasted, 7 days · {_inr(wasted['inr'])}",
            clear=f"wasted outbound {_e(wasted['count'])}",
        ),
        dict(
            name="voice fallback",
            crossed=_over(voice, VOICE_FALLBACK_RATE_OVER),
            level="orange",
            link="ops",
            big=_pct(voice),
            line="of Gemini clips said by Sarvam, 30 days · keep under 1%",
            clear=f"voice fallback {_pct(voice)}",
        ),
        dict(
            name="retrieval fallback",
            crossed=_over(retrieval, RETRIEVAL_FALLBACK_RATE_OVER),
            level="orange",
            link="ops",
            big=_pct(retrieval),
            line="of retrieval queries fell back to recency, 30 days · keep under 1%",
            clear=f"retrieval fallback {_pct(retrieval)}",
        ),
        dict(
            name="unreviewed languages",
            crossed=bool(machine),
            level="orange",
            link="ops",
            big=_e(len(machine)),
            line="languages in use whose fixed messages no native speaker has read",
            clear="languages reviewed",
        ),
        dict(
            name="parent consent stalled",
            crossed=_over(stalled, CONSENT_STALLED_OVER),
            level="orange",
            link="growth",
            big=_e(stalled),
            line="parents stalled after two unclear consent answers",
            clear=f"consent stalled {_e(stalled)}",
        ),
        dict(
            name="family number unconfirmed",
            crossed=_over(waiting, FAMILY_NUMBER_WAITING_48H_OVER),
            level="red" if waiting_no_code else "orange",
            link="growth",
            big=f"{_e(waiting)} unconfirmed",
            line=(
                f"after 48 hours · {_e(waiting_no_code)} with no live code · cards "
                "to them go by email only"
            ),
            clear=f"family numbers unconfirmed after 48h {_e(waiting)}",
        ),
    ]


def _attention(m: dict, items: list[dict]) -> str:
    tiles = "".join(
        f"<a class='alert {i['level']}' href='#{i['link']}'><b>{i['big']}</b>"
        f"<span>{i['line']}</span></a>"
        for i in items
        if i["crossed"]
    )
    calm = [i["clear"] for i in items if not i["crossed"]]
    calm.append(f"active now {_e(m['conversations']['active_now'])}")
    clear = f"<p class=clear>All clear: {' · '.join(calm)}</p>"
    return (f"<div class=attn>{tiles}</div>" if tiles else "") + clear


# ── tier 2: pilot scorecard ───────────────────────────────────────────────────


def _status(value, target) -> tuple[str, str]:
    """(word, level) for a scorecard tile."""
    if target is None:
        return "No target", "grey"
    if value is None:
        return "No data", "grey"
    if value >= target:
        return "On track", "blue"
    if value >= CLOSE_SHARE * target:
        return "Close", "orange"
    return "Behind", "orange"


def _score_tile(
    label,
    value,
    shown,
    group,
    group_n,
    target=None,
    scale=1.0,
    foot="",
    bar=True,
    dashed=False,
) -> str:
    word, level = _status(value, target)
    track = ""
    if bar:
        fill = min(100.0, 100 * (value or 0) / scale)
        tick = (
            f"<div class=tick style='left:calc({100 * target / scale:.1f}% - 1px)'>"
            "</div>"
            if target is not None
            else ""
        )
        track = (
            f"<div class=track><div class=fill style='width:{fill:.1f}%'></div>"
            f"{tick}</div>"
        )
    notes = [n for n in (foot,) if n]
    if target is not None:
        notes.insert(0, f"target {_e(_target_text(target, scale))}")
    if group_n is not None and group_n < SMALL_GROUP:
        notes.append("small group")
    return (
        f"<div class='sc{' dashed' if dashed else ''}'>"
        f"<div class=head><span>{_e(label)}</span>"
        f"<span class='pill {level}'>{_e(word)}</span></div>"
        f"<div class=big>{shown}</div><div class=of>{group}</div>"
        f"{track}<div class=foot>{' · '.join(notes)}</div></div>"
    )


def _target_text(target, scale) -> str:
    return f"{target:g}" if scale != 1.0 else f"{target * 100:.0f}%"


def _scorecard(m: dict) -> str:
    f, d, e = m["funnel"], m["dashboard"], m["engagement"]
    c, a, cost = m["conversations"], m["story_atoms"], m["cost"]
    signed = d["families_signed_in_30d"]
    week4 = e["week4_retention"]
    convs_7d = c["conversations"]["last_7d"]
    atoms_7d = a["atoms"]["last_7d"]
    completeness = a["avg_completeness"]["last_7d"]
    tiles = [
        _score_tile(
            "Parents at 10+ conversations",
            f.get("go_no_go_rate"),
            _pct(f.get("go_no_go_rate")),
            f"{_e(f.get('reached_10_conversations'))} of "
            f"{_e(f.get('parent_consented'))} consented parents",
            f.get("parent_consented"),
            target=0.50,
            foot="go/no-go",
        ),
        _score_tile(
            "Families signed in, 30 days",
            signed["rate"],
            _pct(signed["rate"]),
            f"{_e(signed['count'])} of {_e(f.get('family_accounts'))} families",
            f.get("family_accounts"),
            target=0.40,
            foot="proxy for opened a card",
        ),
        _score_tile(
            "Daily active rate, 7 days",
            e["daily_active_rate_7d"],
            _pct(e["daily_active_rate_7d"]),
            f"{_e(e['active_storytellers_7d'])} active storytellers",
            e["active_storytellers_7d"],
            target=0.60,
        ),
        _score_tile(
            "Conversations 10 min+",
            c["share_10_min_plus"]["last_7d"],
            _pct(c["share_10_min_plus"]["last_7d"]),
            f"of {_e(convs_7d)} conversations, 7 days",
            convs_7d,
            target=0.70,
        ),
        _score_tile(
            "Story completeness (5W)",
            completeness,
            _e(completeness),
            f"{_e(atoms_7d)} atoms, 7 days",
            atoms_7d,
            target=3.5,
            scale=5.0,
        ),
        _score_tile(
            "Week 4 retention",
            week4["rate"],
            _pct(week4["rate"]),
            f"cohort {_e(week4['cohort'])}",
            week4["cohort"],
            foot="read with cohort",
        ),
        _score_tile(
            "Cost per conversation",
            None,
            _inr(cost["inr_per_conversation"]["last_7d"]),
            f"30 days: {_inr(cost['inr_per_conversation']['last_30d'])}",
            None,
            foot="<a href='#cost'>where it goes</a>",
            bar=False,
        ),
        _score_tile(
            "Story extraction accuracy",
            None,
            "—",
            "—",
            None,
            target=0.75,
            foot="manual review, not on this page",
            bar=False,
            dashed=True,
        ),
    ]
    return f"<div class=score>{''.join(tiles)}</div>"


# ── tier 3: detail, by question ───────────────────────────────────────────────


def _group(gid: str, name: str, question: str, summary: str, body: str, open_: bool):
    return (
        f"<details class=group id={gid}{' open' if open_ else ''}><summary>"
        f"<b>{_e(name)}</b> <span class=q>· {_e(question)}</span>"
        f"<span class=line>{summary}</span></summary><div>{body}</div></details>"
    )


def _top_component(cost: dict) -> str:
    parts = cost["by_component"]["last_30d"]
    if not parts:
        return "no spend yet"
    key, value = max(parts.items(), key=lambda kv: kv[1]["inr"])
    return f"top {_e(key)} {_pct(value['share'])}"


def _groups(m: dict, open_ids: set[str]) -> str:
    f, c, a = m["funnel"], m["conversations"], m["story_atoms"]
    cost, units = m["cost"], m["cost"]["units"]["last_30d"]
    n = f.get("family_number") or {}
    given, confirmed = n.get("given", 0), n.get("confirmed", 0)
    signed = m["dashboard"]["families_signed_in_30d"]["count"]
    groups = [
        (
            "growth",
            "Growth",
            "Are families getting through onboarding?",
            f"{_e(f.get('family_accounts'))} families → "
            f"{_e(f.get('first_conversation'))} talked → "
            f"<b>{_e(f.get('reached_10_conversations'))}</b> at 10+ · "
            f"{_e(confirmed)} of {_e(given)} numbers confirmed",
            _funnel_tracks(f, signed),
        ),
        (
            "engagement",
            "Engagement",
            "Are they talking, and coming back?",
            f"{_e(c['conversations']['last_7d'])} conversations, 7 days · median "
            f"{_e(c['median_length_minutes']['last_7d'])} min · "
            f"{_e(m['engagement']['active_storytellers_7d'])} active storytellers",
            _conversations(c)
            + "<h3>Engagement and retention</h3>"
            + _engagement(m["engagement"]),
        ),
        (
            "quality",
            "Story quality",
            "Is the material good enough to become stories?",
            f"{_e(a['atoms']['last_7d'])} atoms, 7 days · "
            f"{_e(a['atoms_per_conversation']['last_7d'])} per conversation · "
            f"{_e(a['domain_coverage']['storytellers_6_plus'])} storytellers at 6+ "
            "chapters",
            _atoms(a),
        ),
        (
            "family",
            "Family value",
            "Is anything reaching the children?",
            f"{_e(m['memory_cards']['delivered']['last_7d'])} memory cards "
            f"delivered, 7 days · "
            f"{_e(m['conversation_cards']['delivery']['sent']['last_7d'])} "
            f"conversation cards sent · {_e(given - confirmed)} families email-only",
            "<h3>Memory cards</h3>"
            + _cards(m["memory_cards"])
            + "<h3>Conversation cards</h3>"
            + _conversation_cards(m["conversation_cards"])
            + "<h3>Family dashboard</h3>"
            + _dashboard(m["dashboard"])
            + f"<h3>Stories</h3><p class=note>{_e(m['stories']['reason'])}</p>",
        ),
        (
            "cost",
            "Cost to serve",
            "What does a conversation cost, and where does it go?",
            f"{_inr(cost['inr_total']['last_30d'])}, 30 days · "
            f"{_top_component(cost)} · cache hit "
            f"{_pct(cost['dialogue_cache_hit_rate'])}",
            _cost(cost),
        ),
        (
            "ops",
            "Safety & operations",
            "Crisis events, vendor health, plans, languages",
            f"{_e(m['safety']['crisis_events']['last_7d'])} crisis events, 7 days · "
            f"voice fallback {_pct(units.get('tts_vendor_fallback_rate'))} · "
            f"{_e(m['plans']['families_deleted'])} families deleted",
            "<h3>Safety and plans</h3>"
            + _safety_and_plans(m["safety"], m["plans"])
            + "<h3>Vendor health</h3>"
            + _gemini_tts(units)
            + _embedding_queries(units)
            + "<h3>API keys</h3>"
            + _api_keys(m.get("api_keys") or [])
            + "<h3>Languages</h3>"
            + _languages(m.get("languages", [])),
        ),
    ]
    return "".join(
        _group(gid, name, q, summary, body, gid in open_ids)
        for gid, name, q, summary, body in groups
    )


# ── tier 4: families ──────────────────────────────────────────────────────────

_STATUS_PILLS = {
    "consent_stalled": ("Consent stalled", "red"),
    "quiet_red": ("Quiet {days} days", "red"),
    "number_no_live_code": ("Number unconfirmed · no live code", "red"),
    "no_parent_consent": ("No parent consent", "orange"),
    "no_conversation_yet": ("No conversation yet", "orange"),
    "quiet_orange": ("Quiet {days} days", "orange"),
    "number_unconfirmed": ("Number unconfirmed", "orange"),
    "active": ("Active", "blue"),
}
assert set(_STATUS_PILLS) == set(FAMILY_STATUS_ORDER)
assert QUIET_ORANGE_DAYS < QUIET_RED_DAYS


def _family_status(row: dict) -> str:
    """The §5.4 status pill for one families[] row."""
    word, level = _STATUS_PILLS[family_status(row)]
    word = word.format(days=row.get("days_since_last_conversation"))
    return f"<span class='pill {level}'>{_e(word)}</span>"


def _family_number_word(row: dict) -> str:
    if not row.get("family_number_given"):
        return "none"
    if row.get("family_number_confirmed"):
        return "confirmed"
    return "waiting" if row.get("confirm_code_live") else "no live code"


def _toward_10(row: dict) -> str:
    n = row.get("conversations_total") or 0
    return "10+ ✓" if n >= 10 else f"{n} / 10"


def _families_compact(rows: list[dict]) -> str:
    """The default eight columns, and the same rows as a list for phones."""
    head = (
        "<tr><th>Storyteller</th><th>Status</th><th>Last conversation</th>"
        "<th class=num>Convs 7d</th><th class=num>Toward 10</th>"
        "<th class=num>Atoms</th><th class=num>Chapters</th>"
        "<th class=num>₹/conv 30d</th></tr>"
    )
    body = "".join(
        f"<tr><td>{_e(r['user_id'])}</td><td>{_family_status(r)}</td>"
        f"<td>{_ist(r.get('last_conversation_at_ist'))}</td>"
        f"<td class=num>{_e(r.get('conversations_7d'))}</td>"
        f"<td class=num>{_e(_toward_10(r))}</td>"
        f"<td class=num>{_e(r.get('atoms_total'))}</td>"
        f"<td class=num>{_e(r.get('domains_covered'))}</td>"
        f"<td class=num>{_inr(r.get('cost_per_conversation_30d_inr'))}</td></tr>"
        for r in rows
    )
    items = "".join(
        f"<li><div class=id>{_e(r['user_id'])}</div>{_family_status(r)}"
        f"<div class=note>Toward 10: {_e(_toward_10(r))} · last "
        f"{_ist(r.get('last_conversation_at_ist'))}</div></li>"
        for r in rows
    )
    return (
        f"<div class='scroll ftable'><table>{head}{body}</table></div>"
        f"<ul class=flist>{items}</ul>"
    )


# ── the page ──────────────────────────────────────────────────────────────────

_NAV = [
    ("attention", "Needs attention"),
    ("scorecard", "Pilot scorecard"),
    ("growth", "Growth"),
    ("engagement", "Engagement"),
    ("quality", "Story quality"),
    ("family", "Family value"),
    ("cost", "Cost"),
    ("ops", "Safety & ops"),
    ("families", "Families"),
]


def render(m: dict) -> str:
    items = _attention_items(m)
    open_ids = {i["link"] for i in items if i["crossed"]}
    nav = "".join(f"<a href='#{gid}'>{_e(label)}</a>" for gid, label in _NAV)
    rows = m["families"]
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Katha · pilot metrics</title><style>{_CSS}</style></head>
<body><main>
<h1>Katha pilot metrics</h1>
<p class=meta>Generated {_ist(m["generated_at"])} · {_e(m["excluded_user_count"])}
account(s) excluded · day boundaries in IST</p>
<nav class=top aria-label="Sections">{nav}</nav>
<section id=attention><h2>Needs attention</h2>{_attention(m, items)}</section>
<section id=scorecard><h2>Pilot scorecard</h2>{_scorecard(m)}</section>
<h2>Detail</h2>
{_groups(m, open_ids)}
<section id=families><h2>Families</h2>
<p class=note>Quietest first, so the family who needs a nudge is at the top.</p>
{_families_compact(rows)}
<details class=more><summary>Show all columns</summary>{_families(rows)}</details>
</section>
</main></body></html>"""
