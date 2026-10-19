"""
The card email and its links (CONVERSATION_CARDS.md §8.3, §12 "email MIME:
inline image, plain-text part, List-Unsubscribe, signed-link expiry"), and
the WhatsApp template mapping (§8.1).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from cards import deliver, email_body, links, state
from models.conversation_card import ConversationCard


def card(**text):
    c = ConversationCard(id=uuid.uuid4(), user_id="u1", status="draft")
    state.set_text(
        c,
        title="The walk to the talkies",
        hook=text.get("hook", "Did you know Amma walked six kilometres to a film?"),
        excerpt="We walked and told <nobody>.",
        original_quote="யாருக்கும் சொல்லல",
        original_quote_gloss="I told no one",
        call_prompt="What do you remember about that first film?",
        readback_text="…",
    )
    return c


def build(c=None):
    return email_body.build(
        c or card(),
        to="priya@example.com",
        parent_title="Amma",
        language_name="Tamil",
        image_png=b"\x89PNG-fake",
    )


def test_subject_is_specific():
    assert (
        build()["Subject"] == "A memory Amma agreed to share: The walk to the talkies"
    )


def test_plain_text_and_html_with_the_image_inline():
    message = build()
    assert message.get_content_type() == "multipart/alternative"
    plain, html = message.get_payload()
    assert plain.get_content_type() == "text/plain"
    assert html.get_content_type() == "multipart/related"
    html_part, image = html.get_payload()
    assert image.get_content_type() == "image/png"
    assert image["Content-ID"] == "<card>"
    assert "inline" in image["Content-Disposition"]
    assert 'src="cid:card"' in html_part.get_content()
    assert image.get_payload(decode=True) == b"\x89PNG-fake"


def test_the_text_is_there_for_screen_readers_and_escaped_in_html():
    message = build()
    plain = message.get_payload()[0].get_content()
    html = message.get_payload()[1].get_payload()[0].get_content()
    for part in ("Did you know Amma", "What do you remember", "யாருக்கும் சொல்லல"):
        assert part in plain
    assert "translated from Tamil" in plain
    assert "&lt;nobody&gt;" in html and "<nobody>" not in html


def test_one_click_unsubscribe_headers():
    message = build()
    assert message["List-Unsubscribe-Post"] == "List-Unsubscribe=One-Click"
    url = message["List-Unsubscribe"].strip("<>")
    assert url.endswith("/unsubscribe") and "/c/" in url


def test_every_link_carries_a_token_for_this_card_only():
    c = card()
    plain = build(c).get_payload()[0].get_content()
    tokens = {
        line.split("/c/")[1].split("?")[0].split("/")[0]
        for line in plain.splitlines()
        if "/c/" in line
    }
    assert tokens and all(links.read_token(t) == c.id for t in tokens)


def test_a_token_expires_after_30_days():
    card_id = uuid.uuid4()
    old = links.make_token(card_id, now=datetime.now(timezone.utc) - timedelta(days=31))
    fresh = links.make_token(
        card_id, now=datetime.now(timezone.utc) - timedelta(days=29)
    )
    assert links.read_token(old) is None
    assert links.read_token(fresh) == card_id


def test_a_forged_or_dashboard_token_is_not_a_card_token():
    from core.auth import create_jwt

    assert links.read_token("not-a-token") is None
    assert links.read_token(create_jwt("priya@example.com", "u1")) is None
    tampered = links.make_token(uuid.uuid4())[:-2] + "xx"
    assert links.read_token(tampered) is None


# ── WhatsApp (§8.1) ──────────────────────────────────────────────────────────


def test_template_variables_match_the_registered_template():
    c = card()
    assert deliver.template_variables("Amma", c) == {
        "2": "Amma",
        "3": "What do you remember about that first film?",
    }


def _profile(number="+919000000001", enabled=True):
    return SimpleNamespace(family_whatsapp_number=number, card_whatsapp_enabled=enabled)


@pytest.mark.parametrize(
    "number, category, expected",
    [
        ("+919000000001", "marketing", None),
        ("+919000000001", "utility", None),
        ("+14155550100", "utility", None),
        ("+14155550100", "marketing", "marketing_us"),
        # Until Meta's category is recorded, assume the worst for +1.
        ("+14155550100", None, "marketing_us"),
    ],
)
def test_the_us_marketing_skip(monkeypatch, number, category, expected):
    mapping = '{"HXcard": "%s"}' % category if category else "{}"
    monkeypatch.setattr("config.settings.META_TEMPLATE_CATEGORIES", mapping)
    assert deliver.whatsapp_skip_reason(_profile(number), "HXcard") == expected


def test_other_whatsapp_skips():
    assert (
        deliver.whatsapp_skip_reason(_profile(enabled=False), "HX") == "child_stopped"
    )
    assert deliver.whatsapp_skip_reason(_profile(), "") == "no_template"
    assert deliver.whatsapp_skip_reason(_profile(number=None), "HX") == "no_number"
