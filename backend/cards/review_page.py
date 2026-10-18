"""
The operator's card review page, /admin/cards/view (CONVERSATION_CARDS.md
§7.6). Plain server-rendered HTML in the style of core/metrics_page.py.

Every value from the database is escaped: card text is model output drawn
from what a parent said, and a child's question is whatever they typed.
"""

from __future__ import annotations

from html import escape

from cards.review import CALL_TAGS, EDITABLE_FIELDS

_CSS = """
:root { color-scheme: light; --ink:#1d1b2e; --soft:#5b5872; --line:#e4e1ea;
  --bg:#faf8f4; --card:#fff; --ok:#1d6b3a; --warn:#b3261e; }
* { box-sizing: border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font: 15px/1.45 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; }
main { max-width: 1100px; margin: 0 auto; padding: 16px; }
h1 { font-size: 1.3rem; margin: 0 0 4px; }
h2 { font-size: 1.05rem; margin: 28px 0 8px; padding-top: 12px;
  border-top: 1px solid var(--line); }
.meta, .note { color: var(--soft); font-size: .85rem; }
.flash { padding: 8px 12px; border-radius: 6px; background: #fdecea;
  color: var(--warn); }
.card { background: var(--card); border: 1px solid var(--line); border-radius: 8px;
  padding: 12px; margin: 12px 0; display: grid; gap: 12px;
  grid-template-columns: minmax(0, 1fr); }
@media (min-width: 900px) { .card { grid-template-columns: 340px minmax(0, 1fr); } }
.card img { width: 100%; border: 1px solid var(--line); border-radius: 6px; }
dl { margin: 0; } dt { color: var(--soft); font-size: .8rem; margin-top: 6px; }
dd { margin: 0; }
.turn { border-left: 3px solid var(--line); padding: 2px 8px; margin: 6px 0;
  font-size: .9rem; }
.turn .asked { color: var(--soft); font-size: .8rem; }
.verdict-supported, .verdict-connective { color: var(--ok); }
.verdict-unsupported { color: var(--warn); font-weight: 600; }
textarea, input[type=text] { width: 100%; font: inherit; padding: 4px; }
textarea { min-height: 3.2em; }
form { margin: 6px 0; }
button { font: inherit; padding: 4px 12px; }
details { margin: 6px 0; }
table { border-collapse: collapse; width: 100%; font-size: .85rem;
  background: var(--card); }
th, td { text-align: left; padding: 5px 8px; border-bottom: 1px solid var(--line); }
.scroll { overflow-x: auto; }
"""


def _e(value) -> str:
    return escape("—" if value is None or value == "" else str(value))


def _hidden(csrf: str) -> str:
    return f'<input type=hidden name=csrf value="{escape(csrf)}">'


def _fidelity(card) -> str:
    result = (card.fidelity_json or {}).get("result", "—")
    sentences = (card.fidelity_json or {}).get("sentences") or []
    passes = (card.fidelity_json or {}).get("passes") or []
    problems = passes[-1].get("problems", []) if passes else []
    rows = "".join(
        f"<tr><td>{_e(s.get('field'))}</td>"
        f"<td class=verdict-{escape(str(s.get('verdict')))}>{_e(s.get('verdict'))}</td>"
        f"<td>{_e(s.get('text'))}</td><td>{_e(', '.join(s.get('turn_ids') or []))}</td>"
        f"<td>{_e(s.get('reason'))}</td></tr>"
        for s in sentences
    )
    problem_list = "".join(f"<li>{_e(p)}</li>" for p in problems)
    rewritten = (
        " · rewritten once" if (card.fidelity_json or {}).get("rewritten") else ""
    )
    edited = " · edited by operator" if card.operator_edited else ""
    return (
        f"<p><b>Fidelity: {_e(result)}</b>{rewritten}{edited}</p>"
        + (f"<ul class=note>{problem_list}</ul>" if problem_list else "")
        + "<div class=scroll><table><tr><th>field</th><th>verdict</th><th>sentence</th>"
        f"<th>turns</th><th>reason</th></tr>{rows}</table></div>"
    )


def _turns(turns: list[dict]) -> str:
    if not turns:
        return (
            "<p class=note>No source turns readable "
            "(a source may have become private).</p>"
        )
    return "".join(
        f"<div class=turn><div class=asked>[{_e(t['id'])}] conversation "
        f"{_e(t.get('session_number'))}, {_e(t.get('date'))} · Katha had asked: "
        f"{_e(t.get('katha_asked'))}</div>{_e(t['transcript'])}</div>"
        for t in turns
    )


def _waiting(item: dict, image_url: str | None, csrf: str) -> str:
    card = item["card"]
    image = (
        f'<img src="{escape(image_url)}" alt="card image">'
        if image_url
        else "<p class=note>No image yet; it is made on approve.</p>"
    )
    fields = "".join(
        f"<dt>{_e(name)}</dt><dd>{_e(getattr(card, name))}</dd>"
        for name in EDITABLE_FIELDS
    )
    edit_inputs = "".join(
        f"<label>{_e(name)}<textarea name={name}>"
        f"{escape(getattr(card, name) or '')}</textarea></label>"
        for name in EDITABLE_FIELDS
    )
    correction = (
        "<p class=flash>Second correction from her: resolve by hand.</p>"
        if (card.correction_round or 0) >= 2
        else ""
    )
    answers = (
        f"<p class=note>Answers the child's question {_e(card.answers_question_id)}</p>"
        if card.answers_question_id
        else ""
    )
    base = f"/admin/cards/{escape(str(card.id))}"
    return f"""<section class=card id="card-{escape(str(card.id))}">
<div>{image}</div>
<div>
<p class=meta>{_e(card.user_id)} · for {_e(item["parent_title"])} · drafted
{_e(card.created_at)} · correction round {_e(card.correction_round or 0)}</p>
{correction}{answers}
<dl>{fields}</dl>
<h3>What she said</h3>{_turns(item["turns"])}
{_fidelity(card)}
<form method=post action="{base}/approve">{_hidden(csrf)}
<button>Approve: she hears it next session</button></form>
<form method=post action="{base}/reject">{_hidden(csrf)}
<input type=text name=reason placeholder="Why (kept for prompt tuning)" required>
<button>Reject</button></form>
<details><summary>Edit (checked again and re-rendered)</summary>
<form method=post action="{base}/edit">{_hidden(csrf)}{edit_inputs}
<button>Save and check</button></form></details>
</div></section>"""


def _held(questions, csrf: str) -> str:
    if not questions:
        return "<p class=note>None.</p>"
    rows = []
    for q in questions:
        base = f"/admin/questions/{escape(str(q.id))}"
        rows.append(
            f"<tr><td>{_e(q.user_id)}</td><td>{_e(q.channel)}</td><td>{_e(q.text)}</td>"
            f"<td>{_e(q.created_at)}</td><td>"
            f'<form method=post action="{base}/release">{_hidden(csrf)}'
            "<button>Release: ask her</button></form>"
            f'<form method=post action="{base}/drop">{_hidden(csrf)}'
            "<button>Drop</button></form></td></tr>"
        )
    return (
        "<div class=scroll><table><tr><th>user</th><th>via</th><th>question</th>"
        f"<th>received</th><th></th></tr>{''.join(rows)}</table></div>"
    )


def _recent(cards, csrf: str) -> str:
    if not cards:
        return "<p class=note>None yet.</p>"
    rows = []
    for c in cards:
        tag = ""
        if c.status == "sent":
            options = "".join(
                f"<option{' selected' if c.call_tag == t else ''}>{t}</option>"
                for t in CALL_TAGS
            )
            tag = (
                f'<form method=post action="/admin/cards/{escape(str(c.id))}/tag">'
                f"{_hidden(csrf)}<select name=tag>{options}</select>"
                "<button>Tag</button></form>"
            )
        rows.append(
            f"<tr><td>{_e(c.updated_at)}</td><td>{_e(c.user_id)}</td>"
            f"<td>{_e(c.status)}</td><td>{_e(c.title)}</td>"
            f"<td>{_e(c.parent_answer)}</td><td>{_e(c.operator_reason)}</td>"
            f"<td>{tag}</td></tr>"
        )
    return (
        "<div class=scroll><table><tr><th>updated</th><th>user</th><th>status</th>"
        "<th>title</th><th>her answer</th><th>operator reason</th>"
        f"<th>did the call happen?</th></tr>{''.join(rows)}</table></div>"
    )


def render(queue: dict, image_urls: dict, csrf: str, flash: str = "") -> str:
    waiting = (
        "".join(
            _waiting(item, image_urls.get(item["card"].id), csrf)
            for item in queue["waiting"]
        )
        or "<p class=note>Nothing waiting for review.</p>"
    )
    flash_html = f"<p class=flash>{_e(flash)}</p>" if flash else ""
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex">
<title>Katha · card review</title><style>{_CSS}</style></head>
<body><main>
<h1>Conversation cards: review</h1>
<p class=meta>Approving sends nothing to the family: the parent hears each card
read back and decides. Target: reviewed within 24 hours.</p>
{flash_html}
<h2>Waiting for review ({len(queue["waiting"])})</h2>{waiting}
<h2>Child questions held by screening ({len(queue["held_questions"])})</h2>
{_held(queue["held_questions"], csrf)}
<h2>Recent cards</h2>{_recent(queue["recent"], csrf)}
</main></body></html>"""
