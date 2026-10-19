"""
The small page a card email links to, /c/{token} (docs/specs/CONVERSATION_CARDS.md
§8.3). No login: the token is the card's own, signed, for 30 days
(cards/links.py).

GET shows the card and two buttons; nothing is recorded by a GET, because
mail scanners (Outlook Safe Links and others) open every link in an email
before the person does. Every action is a POST from the page:

    POST /c/{token}/react        "I didn't know this!"
    POST /c/{token}/ask          a typed question for her
    POST /c/{token}/unsubscribe  stop card emails; also the RFC 8058
                                 one-click target of List-Unsubscribe

Served by the API, not the dashboard: katha.life is the landing page and
app.katha.life's /family/* requires a login, which a child reading email
does not have.
"""

from __future__ import annotations

import logging
from html import escape
from typing import Optional

from fastapi import APIRouter, Depends, Form
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import family_inbox, links
from cards.context import family_of
from config import settings
from models.conversation_card import ConversationCard
from models.db import get_db
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

router = APIRouter()

_HEADERS = {
    "Cache-Control": "no-store",
    "X-Robots-Tag": "noindex",
    # The token is in the URL; never hand it to another site.
    "Referrer-Policy": "no-referrer",
}
MAX_QUESTION = 1000

_CSS = """
body { margin:0; background:#FDF6EC; color:#2C2C2C;
  font: 17px/1.5 Georgia, serif; }
main { max-width: 560px; margin: 0 auto; padding: 24px 16px; }
.hook { font-size: 1.3rem; font-weight: bold; }
.box { background:#F3E6D3; border-radius:8px; padding:12px 16px; margin:16px 0; }
.muted { color:#6B5B4E; font-size:.9rem; }
button { font: inherit; padding: 10px 16px; border: 0; border-radius: 6px;
  background:#C8956C; color:#fff; font-weight:600; margin: 4px 0; }
textarea { width: 100%; min-height: 6em; font: inherit; padding: 8px;
  box-sizing: border-box; }
"""


def _page(body: str, status: int = 200) -> HTMLResponse:
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="robots" content="noindex"><title>Katha</title>
<style>{_CSS}</style></head><body><main>{body}</main></body></html>"""
    return HTMLResponse(html, status_code=status, headers=_HEADERS)


def _gone() -> HTMLResponse:
    return _page(
        "<p>This link has expired or is not valid.</p>"
        "<p class=muted>Card links work for 30 days.</p>",
        status=404,
    )


async def _card_for(
    token: str, db: AsyncSession
) -> tuple[Optional[ConversationCard], Optional[UserProfileModel], str]:
    card_id = links.read_token(token)
    if card_id is None:
        return None, None, ""
    card = (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one_or_none()
    # Only a card that was actually sent has a page.
    if card is None or card.status != "sent":
        return None, None, ""
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == card.user_id)
        )
    ).scalar_one_or_none()
    if profile is None:
        return None, None, ""
    family = await family_of(card.user_id, db)
    title = (family.parent_title if family else None) or profile.name
    return card, profile, title


@router.get("/c/{token}")
async def view(token: str, a: str = "", db: AsyncSession = Depends(get_db)):
    card, _, title = await _card_for(token, db)
    if card is None or not settings.CONVERSATION_CARDS_ENABLED:
        return _gone()
    t = escape(token)
    focus = " autofocus" if a == "ask" else ""
    return _page(f"""
<p class=hook>{escape(card.hook)}</p>
<p>&ldquo;{escape(card.excerpt)}&rdquo;</p>
<div class=box><p class=muted>On your next call, you could ask</p>
<p><b>{escape(card.call_prompt)}</b></p></div>
<form method=post action="/c/{t}/react">
<button>I didn&rsquo;t know this!</button></form>
<form method=post action="/c/{t}/ask">
<p><label for=q>Ask {escape(title)} something. Katha will ask when they next
talk, saying it comes from you.</label></p>
<textarea id=q name=question maxlength={MAX_QUESTION} required{focus}></textarea>
<button>Send the question</button></form>
<p class=muted>{escape(title)} agreed to share this. Katha never sends
{escape(title)}'s answers straight back: anything {escape(title)} says becomes
a new card only if {escape(title)} agrees again.</p>""")


@router.post("/c/{token}/react")
async def react(token: str, db: AsyncSession = Depends(get_db)):
    card, profile, _ = await _card_for(token, db)
    if card is None or not settings.CONVERSATION_CARDS_ENABLED:
        return _gone()
    reply = await family_inbox.react(
        profile, card, family_inbox.DIDNT_KNOW, "email", db
    )
    return _page(f"<p>{escape(reply)}</p>")


@router.post("/c/{token}/ask")
async def ask(token: str, question: str = Form(""), db: AsyncSession = Depends(get_db)):
    card, profile, _ = await _card_for(token, db)
    if card is None or not settings.CONVERSATION_CARDS_ENABLED:
        return _gone()
    question = question.strip()[:MAX_QUESTION]
    if not question:
        return _page("<p>Please type a question first.</p>", status=400)
    reply = await family_inbox.receive(profile, question, "email", db, card=card)
    return _page(f"<p>{escape(reply)}</p>")


@router.get("/c/{token}/unsubscribe")
async def unsubscribe_page(token: str):
    if links.read_token(token) is None:
        return _gone()
    return _page(
        f'<form method=post action="/c/{escape(token)}/unsubscribe">'
        "<p>Stop getting these cards by email?</p>"
        "<button>Yes, stop the emails</button></form>"
        "<p class=muted>Cards still come on WhatsApp unless you reply STOP there.</p>"
    )


@router.post("/c/{token}/unsubscribe")
async def unsubscribe(token: str, db: AsyncSession = Depends(get_db)):
    """Works whether or not cards are switched on: turning email off must
    always be possible. Also the RFC 8058 one-click target."""
    card_id = links.read_token(token)
    if card_id is None:
        return _gone()
    user_id = (
        await db.execute(
            select(ConversationCard.user_id).where(ConversationCard.id == card_id)
        )
    ).scalar_one_or_none()
    family = await family_of(user_id, db) if user_id else None
    if family is not None and family.card_email_enabled:
        family.card_email_enabled = False
        await db.commit()
        logger.info("Card emails turned off for %s", user_id)
    return _page("<p>Done. You won&rsquo;t get these cards by email any more.</p>")
