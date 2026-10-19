"""
Sending approved cards to the child (docs/specs/CONVERSATION_CARDS.md §8).

Every minute the scheduler calls send_due_cards:

1. Any card she approved that has no send time yet is given one
   (cards/schedule.py). Normally that happens the moment she says yes;
   this catches one that did not.
2. Every scheduled card whose time has come is sent, on both channels:
   - WhatsApp, through the approved katha_conversation_card_v1 template,
     never free-form (the child has no open 24-hour window). Skipped when
     the child said "stop", when no template is configured, or for a +1
     number while Meta has the template as Marketing: Meta has paused
     Marketing templates to US numbers, so the send would be charged and
     never arrive.
   - Email, to the address the child logs in with, unless they turned
     card emails off. This is the copy that always arrives.
   A card is marked sent once either channel accepted it.

Nothing here can send a card she has not approved: the card must already
be `scheduled`, which only an approved card can become, and moving it to
`sent` goes through cards/state.py, which checks her approval of the
card's exact current words again. The database's CHECK constraint is
under both.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import email_body, schedule, state
from cards import image as card_image
from cards.candidates import CADENCE_LIMIT, CADENCE_WINDOW
from cards.context import family_of
from config import settings
from core import usage
from core.email import send_operator_alert, send_raw_email_ses
from memory_cards.generator import render_conversation_card
from models.conversation_card import ConversationCard
from models.user_profile import UserProfileModel
from prompts.system_prompt import _language_name

logger = logging.getLogger(__name__)

RETRY_AFTER = timedelta(minutes=30)
NOTHING_TO_SEND_ON = timedelta(days=1)
US_PREFIX = "+1"
WA_OPERATION = "outbound_conversation_card"


def whatsapp_skip_reason(profile, template_sid: str) -> Optional[str]:
    """Why the WhatsApp copy cannot go, or None if it can."""
    if not profile.card_whatsapp_enabled:
        return "child_stopped"
    if not template_sid:
        return "no_template"
    number = profile.family_whatsapp_number
    if not number:
        return "no_number"
    if number.startswith(US_PREFIX) and usage.template_category(template_sid) in (
        "marketing",
        "unknown",
    ):
        # "unknown" too: until Meta's category is recorded we cannot know
        # the send would arrive, and a charged, undelivered message is
        # what this rule exists to avoid.
        return "marketing_us"
    return None


def template_variables(parent_title: str, card: ConversationCard) -> dict:
    """§8.1: {{1}} is the image (added by the adapter), {{2}} what the
    child calls her, {{3}} the call prompt."""
    return {"2": parent_title, "3": card.call_prompt}


async def _sent_recently(user_id: str, now: datetime, db: AsyncSession) -> int:
    rows = await db.execute(
        select(ConversationCard.scheduled_for)
        .where(ConversationCard.user_id == user_id)
        .where(ConversationCard.status == "sent")
        .where(ConversationCard.scheduled_for >= now - CADENCE_WINDOW)
    )
    return len(rows.all())


async def deliver(card: ConversationCard, db: AsyncSession, whatsapp, now: datetime):
    """Send one due card on both channels. Commits. Returns the card's
    status afterwards."""
    if not state.is_approved(card):
        # Unreachable through cards/state.py and refused by the database;
        # if it ever happens, nothing is sent and a person is told.
        logger.error("Card %s is scheduled without her approval — not sent", card.id)
        await send_operator_alert(
            "A card was scheduled without approval and was NOT sent",
            f"card {card.id} for {card.user_id}",
        )
        return card.status

    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == card.user_id)
        )
    ).scalar_one()
    family = await family_of(card.user_id, db)

    if await _sent_recently(card.user_id, now, db) >= CADENCE_LIMIT:
        # D4 at the moment of sending too: the slot it was given may have
        # been overtaken by a card sent in between.
        card.scheduled_for = schedule.send_time(
            now,
            family.call_slots if family else [],
            (family.timezone if family else None) or profile.timezone,
            await schedule.taken_send_times(card.user_id, db, card.id),
        )
        await db.commit()
        logger.info("Card %s moved to %s for cadence", card.id, card.scheduled_for)
        return card.status
    parent_title = (family.parent_title if family else None) or profile.name
    language_name = _language_name(
        card.source_language or card.readback_language or "en-IN"
    )

    if not card.image_s3_key:
        await card_image.render_and_store(card, parent_title)

    # WhatsApp
    template_sid = settings.TWILIO_TEMPLATE_CONVERSATION_CARD
    skip = whatsapp_skip_reason(profile, template_sid)
    if skip:
        card.wa_skipped_reason = skip
    else:
        try:
            card.wa_message_sid = await whatsapp.send_image(
                to_number=profile.family_whatsapp_number,
                s3_key=card.image_s3_key,
                template_sid=template_sid,
                template_variables=template_variables(parent_title, card),
                operation=WA_OPERATION,
            )
            card.wa_skipped_reason = None
        except Exception:
            logger.exception("WhatsApp send failed for card %s", card.id)
            card.wa_error_code = "send_failed"

    # Email
    attempted_email = bool(family and family.card_email_enabled and family.email)
    if attempted_email and card.email_status != "accepted":
        try:
            png = await asyncio.to_thread(
                render_conversation_card,
                hook=card.hook,
                excerpt=card.excerpt,
                call_prompt=card.call_prompt,
                parent_title=parent_title,
                language_name=language_name,
                original_quote=card.original_quote,
                original_quote_gloss=card.original_quote_gloss,
            )
            message = email_body.build(
                card,
                to=family.email,
                parent_title=parent_title,
                language_name=language_name,
                image_png=png,
            )
            card.email_message_id = await send_raw_email_ses(message)
            # Accepted by SES is not delivered (§8.3); bounces are Q3.
            card.email_status = "accepted"
            card.email_sent_at = now
            await usage.record("ses", "card_email", {"emails": 1})
        except Exception:
            logger.exception("Email send failed for card %s", card.id)
            card.email_status = "failed"

    if card.wa_message_sid or card.email_status == "accepted":
        state.transition(card, "sent")
        await db.commit()
        logger.info(
            "Card %s sent (whatsapp=%s, email=%s)",
            card.id,
            card.wa_message_sid or card.wa_skipped_reason or card.wa_error_code,
            card.email_status,
        )
        return card.status

    nothing_possible = skip and not attempted_email
    card.scheduled_for = now + (NOTHING_TO_SEND_ON if nothing_possible else RETRY_AFTER)
    await db.commit()
    if nothing_possible:
        await send_operator_alert(
            "An approved card has nowhere to go",
            f"card {card.id} for {card.user_id}: WhatsApp {skip}, and no card "
            "email for this family. Retrying daily.",
        )
    return card.status


async def send_due_cards(
    db: AsyncSession, whatsapp, now: Optional[datetime] = None
) -> int:
    """Schedule any unscheduled approved card, then send what is due.
    Returns how many were sent. Never raises for one card's failure."""
    now = now or datetime.now(timezone.utc)

    approved = (
        (
            await db.execute(
                select(ConversationCard).where(ConversationCard.status == "approved")
            )
        )
        .scalars()
        .all()
    )
    for card in approved:
        try:
            await schedule.schedule_card(card, db)
            await db.commit()
        except Exception:
            logger.exception("Could not schedule card %s", card.id)
            await db.rollback()

    due = (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.status == "scheduled")
                .where(ConversationCard.scheduled_for <= now)
                .order_by(ConversationCard.scheduled_for)
                .with_for_update(skip_locked=True)
            )
        )
        .scalars()
        .all()
    )
    sent = 0
    for card in due:
        try:
            with usage.usage_scope(user_id=card.user_id):
                if await deliver(card, db, whatsapp, now) == "sent":
                    sent += 1
        except Exception:
            logger.exception("Delivering card %s failed", card.id)
            await db.rollback()
    return sent
