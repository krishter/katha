"""The "email me these links" message (core/setup_links.py)."""

from core import setup_links

PARENT = "https://wa.me/910000000000?text=Namaste"
CONFIRM = "https://wa.me/910000000000?text=CONFIRM%20ABC234"


def test_both_steps_are_numbered_and_carry_their_links():
    subject, text, html = setup_links.build_email("Sunanda", PARENT, CONFIRM)
    assert subject == "Your Katha links for Sunanda"
    assert "1. Send Sunanda the link to start" in text
    assert setup_links.share_message(PARENT) in text
    assert "2. Confirm your WhatsApp number" in text
    assert CONFIRM in text
    assert CONFIRM in html and PARENT in html


def test_a_done_step_is_left_out():
    _, text, _ = setup_links.build_email("Sunanda", "", CONFIRM)
    assert "say hello" not in text
    assert "1. Confirm your WhatsApp number" in text


def test_the_name_is_escaped_in_html():
    _, _, html = setup_links.build_email("<b>Amma</b>", PARENT, "")
    assert "<b>Amma</b>" not in html
    assert "&lt;b&gt;Amma&lt;/b&gt;" in html


def test_the_frontend_says_the_same_words():
    # components/ParentLinkShare.tsx builds the same message.
    from pathlib import Path

    tsx = (
        Path(__file__).resolve().parents[2] / "frontend/components/ParentLinkShare.tsx"
    ).read_text()
    assert setup_links.SHARE_MESSAGE.split("{link}")[0] in tsx
