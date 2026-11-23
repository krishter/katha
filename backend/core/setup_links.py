"""
The two links a family needs after setup, for whoever finishes on a laptop.

The done screen's links are wa.me links, which only really work on a phone:
one the child forwards to the parent (she has to message first, Meta 63049),
and one the child sends from their own phone to confirm the family number
(core/family_number.py). On a laptop the screen shows them as QR codes, and
"Email me these links" sends them here, so the child can finish from their
phone later.
"""

from __future__ import annotations

from html import escape

# What the child sends the parent. The frontend shows the same words
# (components/ParentLinkShare.tsx); change both together.
SHARE_MESSAGE = (
    "I set up something I think you'll like, Katha. Tap this and say hello: {link}"
)


def share_message(parent_link: str) -> str:
    return SHARE_MESSAGE.format(link=parent_link)


def build_email(
    parent_name: str, parent_link: str, confirm_link: str
) -> tuple[str, str, str]:
    """(subject, text, html). Either link may be empty: that step is done
    or not needed, and is left out."""
    name = parent_name.strip() or "your parent"
    text = [
        f"Here are the links from setting up Katha for {name}, to use from your phone.",
        "",
    ]
    html = [
        f"<p>Here are the links from setting up Katha for {escape(name)}, "
        "to use from your phone.</p>"
    ]
    step = 1
    if parent_link:
        message = share_message(parent_link)
        text += [
            f"{step}. Send {name} the link to start",
            f"Call {name} first. Then, while you're still on the call, "
            "send them this on WhatsApp:",
            "",
            message,
            "",
            f"Katha will introduce herself, explain that she's an AI, and ask {name} "
            "whether they're happy to go ahead. "
            "Nothing is recorded until they say yes.",
            "",
        ]
        html += [
            f"<p><strong>{step}. Send {escape(name)} the link to start</strong><br>"
            f"Call {escape(name)} first. Then, while you're still on the call, "
            "send them this on WhatsApp:</p>",
            f"<blockquote>{escape(share_message(''))}"
            f'<a href="{escape(parent_link)}">{escape(parent_link)}</a></blockquote>',
            f"<p>Katha will introduce herself, explain that she's an AI, and ask "
            f"{escape(name)} whether they're happy to go ahead. Nothing is recorded "
            "until they say yes.</p>",
        ]
        step += 1
    if confirm_link:
        text += [
            f"{step}. Confirm your WhatsApp number",
            f"Cards carry {name}'s own words, so Katha sends them only to a number "
            "you've confirmed. Open this on your phone and send the message "
            "WhatsApp fills in:",
            "",
            confirm_link,
            "",
            "This link works for 7 days. You can make a new one from your dashboard.",
            "",
        ]
        html += [
            f"<p><strong>{step}. Confirm your WhatsApp number</strong><br>"
            f"Cards carry {escape(name)}'s own words, so Katha sends them only "
            "to a number "
            "you've confirmed. Open this on your phone and send the message "
            "WhatsApp fills in:</p>",
            f'<p><a href="{escape(confirm_link)}">'
            "Confirm my number on WhatsApp</a></p>",
            "<p>This link works for 7 days. "
            "You can make a new one from your dashboard.</p>",
        ]
    text.append("Katha")
    html.append("<p>Katha</p>")
    return f"Your Katha links for {name}", "\n".join(text), "\n".join(html)
