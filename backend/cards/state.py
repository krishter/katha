"""
Every change to a card's status goes through transition() (CONVERSATION_CARDS.md
§4.1, §6.3, §7).

The rule this module exists for: a card is approved, scheduled or sent
only with her "approve" on the exact text she heard. That is checked here,
in code, and again by a CHECK constraint on conversation_cards, so a path
that bypasses this module still cannot send a card she did not agree to.

Her yes is to the words, not the topic. `fingerprint()` hashes everything
the family will see and everything she heard; the hash is stored as
`readback_hash` when the card's text is set, and copied to
`approved_hash` when she approves. Text can only be set while the card is
with the operator or being corrected (set_text refuses otherwise), so the
card she approves is the card she heard. If the text changes after her
yes, the hashes differ and nothing can send it.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Optional

from models.conversation_card import PARENT_APPROVE, ConversationCard

# The pipeline: cards in these statuses block a new draft (§7.1).
IN_PIPELINE = frozenset({"draft", "operator_review", "awaiting_parent", "correction"})
# On their way to the family, or there already.
LEAVING = frozenset({"approved", "scheduled", "sent"})
# Nothing more happens to a card in one of these.
TERMINAL = frozenset(
    {"rejected", "kept_private", "expired", "withdrawn", "failed_fidelity", "sent"}
)
# A card in any of these can still be withdrawn when a source turn becomes
# private (§5.2). A sent card cannot be unsent.
WITHDRAWABLE = frozenset(
    {
        "draft",
        "operator_review",
        "awaiting_parent",
        "correction",
        "approved",
        "scheduled",
    }
)

ALLOWED: dict[str, frozenset[str]] = {
    "draft": frozenset(
        {"operator_review", "awaiting_parent", "failed_fidelity", "withdrawn"}
    ),
    "operator_review": frozenset(
        {"awaiting_parent", "rejected", "failed_fidelity", "withdrawn"}
    ),
    "awaiting_parent": frozenset(
        {"approved", "correction", "kept_private", "expired", "withdrawn"}
    ),
    "correction": frozenset(
        {"operator_review", "awaiting_parent", "failed_fidelity", "withdrawn"}
    ),
    "approved": frozenset({"scheduled", "withdrawn"}),
    "scheduled": frozenset({"sent", "withdrawn"}),
}

# Fields whose words the family sees or she hears. Changing any of them
# changes the fingerprint.
TEXT_FIELDS = (
    "title",
    "hook",
    "excerpt",
    "original_quote",
    "original_quote_gloss",
    "call_prompt",
    "readback_text",
)
# Operator-only; not part of what anyone agrees to.
NOTE_FIELDS = ("held_back_note", "readback_language", "source_language")
# Text may change only while the card is still being shaped.
EDITABLE = frozenset({"draft", "operator_review", "correction"})


class TransitionError(RuntimeError):
    """A status change the card's state does not allow."""


class NotApproved(TransitionError):
    """The card would leave for the family without her yes to its text."""


def fingerprint(card: ConversationCard) -> str:
    payload = {name: getattr(card, name) or "" for name in TEXT_FIELDS}
    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def is_approved(card: ConversationCard) -> bool:
    """Her yes, to exactly the words the card holds now."""
    return bool(
        card.parent_answer == PARENT_APPROVE
        and card.approved_hash
        and card.readback_hash
        and card.approved_hash == card.readback_hash == fingerprint(card)
    )


def set_text(card: ConversationCard, **fields) -> None:
    """Set the card's words, and its fingerprint with them."""
    if card.status not in EDITABLE:
        raise TransitionError(
            f"card {card.id} is {card.status}: its text can no longer change"
        )
    for name, value in fields.items():
        if name not in TEXT_FIELDS and name not in NOTE_FIELDS:
            raise ValueError(f"{name} is not a card text field")
        setattr(card, name, value)
    card.readback_hash = fingerprint(card)
    # Any earlier agreement was to other words.
    card.approved_hash = None


def transition(card: ConversationCard, to: str) -> None:
    """Move the card to `to`, or raise. Does not commit."""
    allowed = ALLOWED.get(card.status, frozenset())
    if to not in allowed:
        raise TransitionError(f"card {card.id}: {card.status} -> {to} is not allowed")
    if to in LEAVING and not is_approved(card):
        raise NotApproved(
            f"card {card.id}: {card.status} -> {to} without her approval of "
            "this exact text"
        )
    card.status = to


def record_parent_answer(
    card: ConversationCard,
    answer: str,
    *,
    turn_id: Optional[object],
    heard_hash: Optional[str],
) -> None:
    """
    Record her classified answer to the read-back and move the card.

    `heard_hash` is the fingerprint of the card as it was read to her. An
    "approve" counts only if it is still the card's current fingerprint:
    otherwise she agreed to something other than what would be sent, and
    the answer is recorded but the card does not move.
    """
    if answer == PARENT_APPROVE and (
        not heard_hash or heard_hash != card.readback_hash
    ):
        raise NotApproved(
            f"card {card.id}: the approval was for different words "
            "than the card now holds"
        )

    card.parent_answer = answer
    card.parent_answer_turn_id = turn_id
    card.parent_answered_at = datetime.now(timezone.utc)

    if answer == PARENT_APPROVE:
        card.approved_hash = heard_hash
        transition(card, "approved")
    elif answer == "correction":
        card.correction_round = (card.correction_round or 0) + 1
        transition(card, "correction")
    elif answer == "keep_private":
        transition(card, "kept_private")
    # not_now / unclear: no move; the offer counts towards D9 elsewhere.


def withdraw(card: ConversationCard) -> bool:
    """Withdraw a card that has not gone out yet. Returns whether it moved."""
    if card.status not in WITHDRAWABLE:
        return False
    transition(card, "withdrawn")
    return True
