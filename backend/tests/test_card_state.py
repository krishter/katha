"""
cards/state.py (CONVERSATION_CARDS.md §4.1, §6.3, §7): the only way a
card's status changes, and the rule that nothing leaves for the family
without her yes to its exact words.
"""

from __future__ import annotations

import itertools
import uuid

import pytest

from cards import state
from models.conversation_card import STATUSES, ConversationCard


def card(status="draft", **fields) -> ConversationCard:
    c = ConversationCard(id=uuid.uuid4(), user_id="u1", status="draft", **fields)
    state.set_text(
        c,
        title="The walk to the talkies",
        hook="Did you know Amma walked 6 km to see her first film?",
        excerpt="We walked all the way, six kilometres, and told nobody.",
        call_prompt="Ask her which film it was.",
        readback_text="நீங்கள் சொன்னது…",
    )
    c.status = status
    return c


def approve(c: ConversationCard) -> None:
    state.record_parent_answer(
        c, "approve", turn_id=uuid.uuid4(), heard_hash=c.readback_hash
    )


def test_every_status_in_the_table_is_a_real_status():
    assert set(state.ALLOWED) <= set(STATUSES)
    for targets in state.ALLOWED.values():
        assert targets <= set(STATUSES)
    assert state.TERMINAL | set(state.ALLOWED) == set(STATUSES)


def test_only_approved_leads_to_scheduled_and_scheduled_to_sent():
    """§4.1 in the transition table itself."""
    for source, targets in state.ALLOWED.items():
        if "scheduled" in targets:
            assert source == "approved"
        if "sent" in targets:
            assert source == "scheduled"
        if "approved" in targets:
            assert source == "awaiting_parent"


@pytest.mark.parametrize("source, target", list(itertools.product(STATUSES, STATUSES)))
def test_every_pair_either_is_allowed_or_raises(source, target):
    c = card(source)
    if target in state.ALLOWED.get(source, ()) and target not in state.LEAVING:
        state.transition(c, target)
        assert c.status == target
    else:
        with pytest.raises(state.TransitionError):
            state.transition(c, target)
        assert c.status == source


@pytest.mark.parametrize("answer", [None, "unclear", "not_now", "correction"])
def test_no_answer_but_approve_lets_a_card_leave(answer):
    c = card("awaiting_parent", parent_answer=answer)
    with pytest.raises(state.NotApproved):
        state.transition(c, "approved")
    c.status = "approved"  # as if something wrote it directly
    with pytest.raises(state.NotApproved):
        state.transition(c, "scheduled")


def test_her_yes_moves_the_card_all_the_way():
    c = card("awaiting_parent")
    approve(c)
    assert c.status == "approved"
    state.transition(c, "scheduled")
    state.transition(c, "sent")
    assert c.status == "sent"


def test_a_yes_to_different_words_is_refused():
    c = card("awaiting_parent")
    with pytest.raises(state.NotApproved):
        state.record_parent_answer(
            c, "approve", turn_id=uuid.uuid4(), heard_hash="what-she-heard-last-week"
        )
    assert c.status == "awaiting_parent"
    assert c.parent_answer is None


def test_changed_words_after_her_yes_cannot_be_sent():
    c = card("awaiting_parent")
    approve(c)
    # Text is frozen once it has gone to her...
    with pytest.raises(state.TransitionError):
        state.set_text(c, hook="Did you know Amma ran away to see a film?")
    # ...and even a direct write is caught by the fingerprint.
    c.hook = "Did you know Amma ran away to see a film?"
    with pytest.raises(state.NotApproved):
        state.transition(c, "scheduled")


def test_setting_text_forgets_any_earlier_agreement():
    c = card("correction", approved_hash="old", parent_answer="approve")
    state.set_text(c, hook="Did you know her uncle took her to her first film?")
    assert c.approved_hash is None
    assert c.readback_hash == state.fingerprint(c)


def test_operator_notes_do_not_change_what_she_agrees_to():
    c = card("operator_review")
    before = c.readback_hash
    state.set_text(c, held_back_note="Left out which film, for the call.")
    assert c.readback_hash == before


@pytest.mark.parametrize(
    "answer, status",
    [
        ("correction", "correction"),
        ("keep_private", "kept_private"),
        ("not_now", "awaiting_parent"),
        ("unclear", "awaiting_parent"),
    ],
)
def test_her_other_answers(answer, status):
    c = card("awaiting_parent")
    state.record_parent_answer(c, answer, turn_id=uuid.uuid4(), heard_hash=None)
    assert c.status == status
    if answer == "correction":
        assert c.correction_round == 1


@pytest.mark.parametrize("status", sorted(state.WITHDRAWABLE))
def test_anything_not_yet_sent_can_be_withdrawn(status):
    c = card(status)
    if status in state.LEAVING:
        approve_status = c.status
        c.status = "awaiting_parent"
        approve(c)
        c.status = approve_status
    assert state.withdraw(c) is True
    assert c.status == "withdrawn"


def test_a_sent_card_cannot_be_withdrawn():
    c = card("sent")
    assert state.withdraw(c) is False
    assert c.status == "sent"
