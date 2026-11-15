"""
The operator's Story review page, /admin/stories/view (docs/specs/STORIES.md
§2.2). Plain server-rendered HTML in the style of cards/review_page.py.

Every value from the database is escaped: a Story is model output drawn from
what a parent said.
"""

from __future__ import annotations

from html import escape

from cards.review_page import _CSS


def _e(value) -> str:
    return escape("—" if value is None or value == "" else str(value))


def _hidden(csrf: str) -> str:
    return f'<input type=hidden name=csrf value="{escape(csrf)}">'


def _sentences(story) -> str:
    rows = "".join(
        f"<tr><td class=verdict-{escape(str(s.get('verdict')))}>"
        f"{_e(s.get('verdict'))}</td><td>{_e(s.get('text'))}</td>"
        f"<td>{_e(', '.join(s.get('turn_ids') or []))}</td></tr>"
        for s in story.sentence_sources or []
    )
    cut = "".join(
        f"<li><s>{_e(s.get('text'))}</s> — {_e(s.get('reason'))}</li>"
        for s in story.removed_sentences or []
    )
    return (
        f"<p><b>Fidelity: {_e(story.fidelity_status)}</b></p>"
        "<div class=scroll><table><tr><th>verdict</th><th>sentence</th>"
        f"<th>turns</th></tr>{rows}</table></div>"
        + (f"<p class=note>Cut as unsaid:</p><ul class=note>{cut}</ul>" if cut else "")
    )


def _readback(story) -> str:
    check = story.readback_check or {}
    passes = check.get("passes") or []
    problems = passes[-1].get("problems", []) if passes else []
    listed = "".join(f"<li>{_e(p)}</li>" for p in problems)
    return (
        f"<p><b>Read-back ({_e(story.readback_language)}): "
        f"{_e(check.get('result'))}</b> · {len(passes)} pass(es)</p>"
        f"<p>{_e(story.readback_text)}</p>"
        + (f"<ul class=note>{listed}</ul>" if listed else "")
    )


def _turns(turns) -> str:
    return "".join(
        f"<div class=turn><div class=asked>{_e(t.id)} · turn {_e(t.turn_number)} · "
        f"{_e(t.detected_language)}</div>{_e(t.transcript)}</div>"
        for t in turns
    )


def _item(item: dict, csrf: str) -> str:
    story = item["story"]
    quotes = "".join(
        f"<li>{_e(q.get('original'))}<br>"
        f"<span class=note>{_e(q.get('gloss'))}</span></li>"
        for q in story.quotes or []
    )
    sid = escape(str(story.id))
    return f"""<section class=card><div>
<h3>{_e(story.title)}</h3>
<p class=meta>{_e(item.get("parent_title"))} · {_e(story.domain)} ·
{_e(story.when_approx)} · told in conversation(s)
{_e(", ".join(str(n) for n in story.sessions_seen or []))}</p>
<p>{_e(story.body_first_person)}</p>
<ul>{quotes}</ul>
{_readback(story)}
<form method=post action="/admin/stories/{sid}/pass">{_hidden(csrf)}
<button>Pass to her</button></form>
<form method=post action="/admin/stories/{sid}/rewrite">{_hidden(csrf)}
<button>Rewrite the read-back</button></form>
<form method=post action="/admin/stories/{sid}/retire">{_hidden(csrf)}
<input type=text name=reason placeholder="Why it is set aside (for tuning)">
<button>Retire</button></form>
</div><div>
{_sentences(story)}
<details><summary>What she said ({len(item["turns"])} turns)</summary>
{_turns(item["turns"])}</details>
</div></section>"""


def render(queue: dict, csrf: str, flash: str = "") -> str:
    waiting = "".join(_item(i, csrf) for i in queue["waiting"]) or (
        "<p class=note>Nothing waiting.</p>"
    )
    recent = "".join(
        f"<tr><td>{_e(s.title)}</td><td>{_e(s.status)}</td><td>{_e(s.review_status)}</td>"
        f"<td>{_e(s.approval_status)}</td><td>{_e(s.retired_reason)}</td>"
        f"<td>{_e(s.updated_at)}</td></tr>"
        for s in queue["recent"]
    )
    banner = f"<p class=flash>{_e(flash)}</p>" if flash else ""
    return f"""<!doctype html><html lang=en><head><meta charset=utf-8>
<meta name=viewport content="width=device-width, initial-scale=1">
<title>Story review</title><style>{_CSS}</style></head><body><main>
<h1>Story review</h1>
<p class=meta>{len(queue["waiting"])} waiting · {queue["threads"]} open thread(s)</p>
{banner}
<h2>Waiting for review</h2>{waiting}
<h2>Recently reviewed</h2>
<div class=scroll><table><tr><th>title</th><th>status</th><th>review</th>
<th>her answer</th><th>retired</th><th>updated</th></tr>{recent}</table></div>
</main></body></html>"""
