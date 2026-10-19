"""
The card email (docs/specs/CONVERSATION_CARDS.md §8.3).

- Subject: "A memory {parent_title} agreed to share: {title}". Specific,
  not teasing.
- The card image inline (CID): the image is private, and a presigned URL
  would stop working in the inbox within the hour.
- The hook, excerpt and call prompt again as text, for screen readers and
  clients that block images.
- Two buttons, each a signed 30-day link to the card's small page.
- Why they are getting this, and a one-click way to stop (also as a
  List-Unsubscribe header, RFC 8058).

Every value is escaped: it is model output drawn from what a parent said.
"""

from __future__ import annotations

from email.message import EmailMessage
from html import escape

from cards import links
from core.email import build_mime
from models.conversation_card import ConversationCard

IMAGE_CID = "card"


def subject(card: ConversationCard, parent_title: str) -> str:
    return f"A memory {parent_title} agreed to share: {card.title}"


def translated_label(parent_title: str, language_name: str) -> str:
    """D8: the card is English; say so when she spoke something else."""
    if not language_name or language_name.lower() == "english":
        return ""
    return f"In {parent_title}'s words, translated from {language_name}."


def build(
    card: ConversationCard,
    *,
    to: str,
    parent_title: str,
    language_name: str,
    image_png: bytes,
) -> EmailMessage:
    token = links.make_token(card.id)
    didnt_know = links.page_url(token, "didnt_know")
    ask = links.page_url(token, "ask")
    unsubscribe = links.unsubscribe_url(token)
    translated = translated_label(parent_title, language_name)

    quote_text = ""
    quote_html = ""
    if card.original_quote:
        quote_text = f"\n{card.original_quote}\n({card.original_quote_gloss or ''})\n"
        gloss = escape(card.original_quote_gloss or "")
        quote_html = (
            '<p style="font-size:18px;margin:8px 0 0">'
            f"{escape(card.original_quote)}</p>"
            f'<p style="color:#6B5B4E;margin:0">({gloss})</p>'
        )

    why = (
        f"You are getting this because {parent_title} talks with Katha and "
        "agreed to share this memory with you. Katha reads every card to "
        f"{parent_title} first, and sends only what {parent_title} says yes to."
    )

    text = f"""{card.hook}

"{card.excerpt}"{quote_text}
{translated}

On your next call, you could ask: {card.call_prompt}

I didn't know this: {didnt_know}
Ask {parent_title} something: {ask}

{why}
Stop these emails: {unsubscribe}
"""

    button = (
        "display:inline-block;padding:10px 16px;margin:4px;border-radius:6px;"
        "background:#C8956C;color:#fff;text-decoration:none;font-weight:600"
    )
    html = f"""<!doctype html><html><body style="margin:0;background:#FDF6EC;\
font-family:Georgia,serif;color:#2C2C2C">
<div style="max-width:560px;margin:0 auto;padding:24px">
<img src="cid:{IMAGE_CID}" alt="{escape(card.hook)}" width="540"
 style="width:100%;max-width:540px;border-radius:8px">
<p style="font-size:20px;font-weight:bold">{escape(card.hook)}</p>
<p style="font-size:17px">&ldquo;{escape(card.excerpt)}&rdquo;</p>
{quote_html}
<p style="color:#6B5B4E;font-size:13px">{escape(translated)}</p>
<div style="background:#F3E6D3;border-radius:8px;padding:12px 16px;margin:16px 0">
<p style="margin:0;color:#6B5B4E;font-size:13px">On your next call, you could ask</p>
<p style="margin:4px 0 0;font-size:18px;font-weight:bold">{escape(card.call_prompt)}</p>
</div>
<p><a href="{escape(didnt_know)}" style="{button}">I didn&rsquo;t know this!</a>
<a href="{escape(ask)}" style="{button}">Ask {escape(parent_title)} something</a></p>
<p style="color:#6B5B4E;font-size:12px">{escape(why)}<br>
<a href="{escape(unsubscribe)}" style="color:#6B5B4E">Stop these emails</a></p>
</div></body></html>"""

    return build_mime(
        to=to,
        subject=subject(card, parent_title),
        body_text=text,
        body_html=html,
        inline_images={IMAGE_CID: image_png},
        headers={
            "List-Unsubscribe": f"<{unsubscribe}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
        },
    )
