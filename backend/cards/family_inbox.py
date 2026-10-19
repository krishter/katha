"""
What the child sends back (docs/specs/CONVERSATION_CARDS.md §8.1, §10).

On WhatsApp a card arrives with three quick-reply buttons; tapping one, or
replying at all, opens the 24-hour window, so everything here answers
free-form. From email, the card's page (api/routes/card_page.py) calls
receive() with the typed question.

    "I didn't know this!"  recorded on the card; relayed to her as a
                           reaction (§10.3)
    "I knew this"          recorded; nothing relayed
    "Ask her something"    a prompt; whatever they send next is screened
                           like any other message (no "waiting for a
                           question" state to get stuck in)
    anything else          screened (core/question_screen.py):
                           ok         queued for her next session
                           sensitive  held for the operator; the child is
                                      told the same as for ok
                           correction not relayed (§4.6)
                           reaction   relayed as a reaction
                           other      not relayed; "stop" turns cards off
                                      on WhatsApp

Her answer to a question never comes straight back (§4.5): it becomes
conversation, and reaches the child only as a later card she approves.
Voice-note questions are transcribed and relayed as text (Q1); the
child's audio is not kept.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

import cards
from cards.context import family_of
from core import question_screen
from core.email import send_operator_alert
from models.conversation_card import ConversationCard
from models.family_question import FamilyQuestion
from models.user_profile import UserProfileModel

logger = logging.getLogger(__name__)

MAX_QUEUED = 3  # §10.2
_QUEUED = ("pending", "held", "released")

DIDNT_KNOW = "didnt_know"
KNEW = "knew"
ASK = "ask"
# Button payloads as registered on the template, and the button text as a
# fallback when a payload is missing (CONVERSATION_CARDS_STEP0.md).
BUTTONS = {
    "didnt_know": DIDNT_KNOW,
    "i didn't know this!": DIDNT_KNOW,
    "i didn’t know this!": DIDNT_KNOW,
    "knew": KNEW,
    "i knew this": KNEW,
    "ask": ASK,
    "ask her something": ASK,
}


@dataclass(frozen=True)
class Replies:
    """What the child is told (§8.1, §10.2), in their parent's title."""

    title: str

    @property
    def didnt_know(self) -> str:
        return f"Thank you. I'll let {self.title} know you enjoyed it."

    @property
    def knew(self) -> str:
        return f"Lovely. {self.title} will be glad it stuck."

    @property
    def ask(self) -> str:
        return f"What would you like to ask {self.title}? Type it or send a voice note."

    @property
    def queued(self) -> str:
        return (
            f"Thank you. I'll ask {self.title} when we next talk. If "
            f"{self.title} is happy to share the answer, it will come to you as "
            "a card."
        )

    @property
    def full(self) -> str:
        return (
            f"{self.title} has a few of your questions waiting already. I'll ask "
            "those first."
        )

    @property
    def correction(self) -> str:
        return (
            f"Katha keeps {self.title}'s stories in their own words, so we don't "
            "pass on corrections, but it could be a lovely thing to ask about on "
            "your call."
        )

    @property
    def reaction(self) -> str:
        return f"I'll let {self.title} know."

    @property
    def other(self) -> str:
        return "Thank you. I've passed this on to the Katha team."

    def stopped(self, email_on: bool) -> str:
        tail = " They will still come by email." if email_on else ""
        return f"Okay, no more cards on WhatsApp.{tail}"


async def parent_for_family_number(
    number: str, db: AsyncSession
) -> Optional[UserProfileModel]:
    """The parent whose child messages from this number, if cards are on
    for her. Callers check a parent's own number first: on a clash the
    parent wins (§10.1)."""
    rows = (
        (
            await db.execute(
                select(UserProfileModel)
                .where(UserProfileModel.family_whatsapp_number == number)
                .order_by(UserProfileModel.created_at.desc())
            )
        )
        .scalars()
        .all()
    )
    for profile in rows:
        if cards.enabled_for(profile):
            return profile
    return None


async def _latest_sent(
    user_id: str, db: AsyncSession, replied_sid: Optional[str] = None
) -> Optional[ConversationCard]:
    if replied_sid:
        card = (
            await db.execute(
                select(ConversationCard).where(
                    ConversationCard.wa_message_sid == replied_sid
                )
            )
        ).scalar_one_or_none()
        if card is not None and card.user_id == user_id:
            return card
    return (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.user_id == user_id)
                .where(ConversationCard.status == "sent")
                .order_by(ConversationCard.scheduled_for.desc())
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


async def _title(profile: UserProfileModel, db: AsyncSession) -> str:
    family = await family_of(profile.user_id, db)
    return (family.parent_title if family else None) or profile.name


async def react(
    profile: UserProfileModel,
    card: Optional[ConversationCard],
    reaction: str,
    channel: str,
    db: AsyncSession,
) -> str:
    """A "didn't know" / "knew" tap. Returns the reply. Commits."""
    replies = Replies(await _title(profile, db))
    if card is None:
        return replies.didnt_know if reaction == DIDNT_KNOW else replies.knew
    first = card.child_reaction is None
    card.child_reaction = reaction
    card.child_reaction_at = datetime.now(timezone.utc)
    card.child_reaction_channel = channel
    if reaction == DIDNT_KNOW and first:
        # Relayed to her once per card, however many times it is tapped.
        db.add(
            FamilyQuestion(
                user_id=profile.user_id,
                card_id=card.id,
                channel=channel,
                kind="reaction",
                raw_text="I didn't know this!",
                screening="reaction",
                status="pending",
            )
        )
    await db.commit()
    return replies.didnt_know if reaction == DIDNT_KNOW else replies.knew


async def receive(
    profile: UserProfileModel,
    text: str,
    channel: str,
    db: AsyncSession,
    *,
    card: Optional[ConversationCard] = None,
    transcript: str = "",
    message_sid: Optional[str] = None,
) -> str:
    """Screen and file a message from the child. Returns the reply.
    Commits."""
    title = await _title(profile, db)
    replies = Replies(title)
    said = (transcript or text or "").strip()
    screening = await question_screen.screen(said, title)

    def question(kind: str, label: str, status: str) -> FamilyQuestion:
        return FamilyQuestion(
            id=uuid.uuid4(),
            user_id=profile.user_id,
            card_id=card.id if card else None,
            channel=channel,
            kind=kind,
            raw_text=text or "",
            transcript=transcript or "",
            screening=label,
            status=status,
            inbound_message_sid=message_sid,
        )

    if screening.stop:
        db.add(question("question", "other", "not_relayed"))
        if channel == "whatsapp":
            profile.card_whatsapp_enabled = False
        family = await family_of(profile.user_id, db)
        await db.commit()
        logger.info("Child of %s stopped cards on %s", profile.user_id, channel)
        return replies.stopped(bool(family and family.card_email_enabled))

    label = screening.label
    if label in ("ok", "sensitive"):
        queued = (
            await db.execute(
                select(func.count(FamilyQuestion.id))
                .where(FamilyQuestion.user_id == profile.user_id)
                .where(FamilyQuestion.kind == "question")
                .where(FamilyQuestion.status.in_(_QUEUED))
            )
        ).scalar_one()
        if queued >= MAX_QUEUED:
            db.add(question("question", label, "dropped"))
            await db.commit()
            return replies.full
        held = label == "sensitive"
        db.add(question("question", label, "held" if held else "pending"))
        await db.commit()
        if held:
            await send_operator_alert(
                "A child's question was held for review",
                f"user {profile.user_id}: release or drop it at /admin/cards/view.",
            )
        # The child is not told it was held (§10.2).
        return replies.queued

    if label == "correction":
        db.add(question("question", label, "not_relayed"))
        await db.commit()
        return replies.correction

    if label == "reaction":
        db.add(question("reaction", label, "pending"))
        await db.commit()
        return replies.reaction

    db.add(question("question", "other", "not_relayed"))
    await db.commit()
    await send_operator_alert(
        "A message from a child needs a person",
        f"user {profile.user_id} ({channel}): {said[:500]}",
    )
    return replies.other


def button_of(params: dict) -> Optional[str]:
    for key in ("ButtonPayload", "ButtonText"):
        value = str(params.get(key) or "").strip().lower()
        if value in BUTTONS:
            return BUTTONS[value]
    return None


async def handle_whatsapp(
    whatsapp,
    profile: UserProfileModel,
    from_number: str,
    params: dict,
    transcribe,
    db: AsyncSession,
) -> None:
    """One inbound WhatsApp message from the child. Replies free-form."""
    message_sid = params.get("MessageSid") or None
    if message_sid:
        seen = (
            await db.execute(
                select(FamilyQuestion.id).where(
                    FamilyQuestion.inbound_message_sid == message_sid
                )
            )
        ).scalar_one_or_none()
        if seen is not None:
            logger.info("Duplicate family message %s — already handled", message_sid)
            return

    card = await _latest_sent(
        profile.user_id, db, params.get("OriginalRepliedMessageSid")
    )
    button = button_of(params)
    if button in (DIDNT_KNOW, KNEW):
        reply = await react(profile, card, button, "whatsapp", db)
    elif button == ASK:
        reply = Replies(await _title(profile, db)).ask
    else:
        transcript = ""
        media_type = params.get("MediaContentType0", "")
        if params.get("MediaUrl0") and "audio" in media_type:
            transcript = await transcribe(params["MediaUrl0"])
        reply = await receive(
            profile,
            params.get("Body", "") or "",
            "whatsapp",
            db,
            card=card,
            transcript=transcript,
            message_sid=message_sid,
        )
    await whatsapp.send_text(from_number, reply)
