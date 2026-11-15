"""
The one card "ask" a session may carry, and what her answer does
(docs/specs/CONVERSATION_CARDS.md §5.1, §6, §9.2, §10.3).

An ask is one of:
    share            "would you like me to share some of these with Priya?"
    readback_offer   "I've written something … shall I read it to you?"
    readback         the card read to her, ending "shall I send this?"
    child_question   a question from her child, credited by name
    story_readback_offer, story_readback
                     the same two steps for a Story (stories/asks.py,
                     STORIES.md §2.3)
    retell_offer     Katha could not write her correction to a card or
                     Story: tell that part again, or set it aside? (§6c)
    retelling        her next message, which is that part told again

At most one a session, never mid-story (§4.7). The dialogue model writes
the share question, the offer and the relayed question from a Layer 4
instruction, and says it did with an <asked> tag after its reply. The
read-back itself is never written by the model: it is the card's stored
readback_text, exactly what the operator saw, spoken in her pinned voice.

Each turn has two halves:

before_dialogue  If the last reply made an ask, label her answer
                 (core/parent_answers.py) and act on it; then decide what
                 this turn's Layer 4 should carry. When she has just said
                 yes to hearing a card, the turn's reply is the read-back
                 and no dialogue call is made.
after_dialogue   Read the <asked> tag. Only an ask the model actually made
                 becomes the pending ask that her next message answers.
"""

from __future__ import annotations

import logging
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from cards import schedule, state
from cards.context import family_of
from config import settings
from core import parent_answers, sharing
from models.conversation_card import ConversationCard
from models.family_question import FamilyQuestion
from models.session import Session
from models.user_profile import UserProfileModel
from stories import asks as story_asks

logger = logging.getLogger(__name__)

SHARE = "share"
READBACK_OFFER = "readback_offer"
READBACK = "readback"
CHILD_QUESTION = "child_question"
STORY_READBACK_OFFER = "story_readback_offer"
STORY_READBACK = "story_readback"
RETELL_OFFER = "retell_offer"
RETELLING = "retelling"
REACTION = "reaction"  # not an ask (§9.2): it rides along with anything
ASK_KINDS = (SHARE, READBACK_OFFER, CHILD_QUESTION, STORY_READBACK_OFFER, RETELL_OFFER)

MAX_READBACK_OFFERS = 3  # D9
QUESTION_EXPIRY = timedelta(days=21)  # §10.2
FIRST_SHARE_SESSION = 2  # §5.1: session 1 is for trust
SECOND_SHARE_SESSION = 4

_ASKED_RE = re.compile(r"<asked>(.*?)</asked>", re.DOTALL | re.IGNORECASE)


@dataclass
class TurnPlan:
    """What cards add to this turn."""

    layer4: list[str] = field(default_factory=list)
    # The kinds the model may report with <asked>.
    tag_kinds: list[str] = field(default_factory=list)
    # The ask Layer 4 carries this turn, and what it is about.
    offered: Optional[str] = None
    offered_ref: Optional[uuid.UUID] = None
    reaction_ids: list[uuid.UUID] = field(default_factory=list)
    # Set when she said yes to hearing a card or a Story: speak this, skip
    # dialogue.
    readback_text: Optional[str] = None
    readback_card_id: Optional[uuid.UUID] = None
    readback_story_id: Optional[uuid.UUID] = None
    # Set when she corrected a card: rewrite it once the turn is saved.
    correction_card_id: Optional[uuid.UUID] = None
    # The card she answered this turn, and her sharing answer: both are
    # recorded with this turn as their evidence once it is saved.
    answered_card_id: Optional[uuid.UUID] = None
    # The same for a Story (STORIES.md §2.3).
    answered_story_id: Optional[uuid.UUID] = None
    correction_story_id: Optional[uuid.UUID] = None
    share_decision: Optional[str] = None
    # How her answer came out ("readback_approve", "share_no", ...).
    outcome: Optional[str] = None


def split_asked_tag(raw: str) -> tuple[set[str], str]:
    """The kinds named in <asked> tags, and the reply with the tags removed
    so they are never spoken."""
    kinds: set[str] = set()
    for match in _ASKED_RE.finditer(raw or ""):
        for part in re.split(r"[,\s]+", match.group(1)):
            if part.strip():
                kinds.add(part.strip().lower())
    return kinds, _ASKED_RE.sub("", raw or "").strip()


async def _session(session_id: str, db: AsyncSession) -> Session:
    return (
        await db.execute(select(Session).where(Session.id == uuid.UUID(session_id)))
    ).scalar_one()


async def _profile(user_id: str, db: AsyncSession) -> UserProfileModel:
    return (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one()


async def _card(card_id: Optional[uuid.UUID], db: AsyncSession):
    if card_id is None:
        return None
    return (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one_or_none()


def _names(family, profile) -> tuple[str, str]:
    child = (family.child_name if family else None) or "their child"
    title = (family.parent_title if family else None) or profile.name
    return child, title


# ── her answer to the last ask ───────────────────────────────────────────────


async def _answer_share(profile, transcript, child, plan: TurnPlan) -> None:
    result = await parent_answers.classify(
        parent_answers.SHARE,
        f"Would you like me to share some of your stories with {child}? "
        "I would always read them to you first.",
        transcript,
    )
    if result.label == "yes":
        plan.share_decision = sharing.SHARE
        plan.outcome = "share_yes"
        plan.layer4.append(
            f"The user just agreed that some of their stories may be shared with "
            f"{child}. Thank them in a few words and reassure them you will always "
            "read anything to them first, then carry on with the conversation."
        )
    elif result.label == "no":
        plan.share_decision = sharing.NEVER
        plan.outcome = "share_no"
        plan.layer4.append(
            "The user would rather their stories were not shared. Accept that "
            "warmly in a few words, without persuading or asking why, then carry "
            "on with the conversation."
        )
    else:
        # Unclear once: ask again in session 4 or later. Unclear twice: never.
        second = (profile.share_preference_asks or 0) >= 2
        plan.share_decision = sharing.NEVER if second else sharing.UNSURE
        plan.outcome = "share_unclear"


async def _answer_offer(card, transcript, child, plan: TurnPlan, db) -> None:
    result = await parent_answers.classify(
        parent_answers.OFFER,
        f"I've written something from what you told me, to send to {child}. "
        "Shall I read it to you?",
        transcript,
    )
    if result.label == "yes" and card is not None and card.status == "awaiting_parent":
        plan.readback_text = card.readback_text
        plan.readback_card_id = card.id
        plan.outcome = "offer_yes"
        return
    if result.label == "not_now":
        plan.outcome = "offer_not_now"
        plan.layer4.append(
            "The user would rather not hear the card now. Say that is fine in a "
            "few words and carry on with the conversation."
        )
    else:
        plan.outcome = "offer_other"
    if card is not None:
        _expire_if_done(card)


async def _answer_readback(card, transcript, child, plan: TurnPlan) -> None:
    if card is None or card.status != "awaiting_parent":
        return
    result = await parent_answers.classify(
        parent_answers.READBACK, f"Shall I send this to {child}?", transcript
    )
    label = result.label
    # The card's words cannot change while it waits for her (state.EDITABLE),
    # so its fingerprint now is the fingerprint of what was read to her.
    state.record_parent_answer(
        card,
        label,
        turn_id=None,  # set to this turn once it is saved (after_turn_saved)
        heard_hash=card.readback_hash if label == "approve" else None,
    )
    plan.answered_card_id = card.id
    plan.outcome = f"readback_{label}"
    if label == "approve":
        plan.layer4.append(
            f"The user just agreed to send the card to {child}. Confirm in one "
            f"short sentence that you will send it to {child}, then carry on."
        )
    elif label == "correction":
        plan.correction_card_id = card.id
        plan.layer4.append(
            "The user corrected something in the card. Thank them, say you will "
            "put it right and read it to them again next time, then carry on."
        )
    elif label == "keep_private":
        plan.layer4.append(
            "The user does not want the card sent. Confirm in one short sentence "
            "that it stays between the two of you, without asking why, then "
            "carry on."
        )
    elif label == "not_now":
        plan.layer4.append(
            "The user wants to decide about the card later. Say that is fine in "
            "a few words and carry on."
        )
        _expire_if_done(card)
    else:
        _expire_if_done(card)


def _expire_if_done(card: ConversationCard) -> None:
    """D9: offered three times without a clear answer, the card is dropped."""
    if (
        card.status == "awaiting_parent"
        and (card.readback_offers or 0) >= MAX_READBACK_OFFERS
    ):
        state.transition(card, "expired")
        logger.info("Card %s expired after %d offers", card.id, card.readback_offers)


async def _answer_story_offer(story, transcript, child, plan: TurnPlan) -> None:
    label = await story_asks.classify_offer(transcript, child)
    if label == "yes" and story_asks.readable(story):
        plan.readback_text = story.readback_text
        plan.readback_story_id = story.id
        plan.outcome = "story_offer_yes"
        return
    if label == "not_now":
        plan.outcome = "story_offer_not_now"
        plan.layer4.append(
            "The user would rather not hear the story now. Say that is fine in "
            "a few words and carry on with the conversation."
        )
    else:
        plan.outcome = "story_offer_other"


async def _answer_story_readback(story, transcript, child, plan: TurnPlan) -> None:
    if not story_asks.readable(story):
        return
    label = await story_asks.classify_readback(transcript, child)
    try:
        story_asks.record_answer(story, label)
    except story_asks.NotApproved:
        # Her yes was to other words than the Story holds now: record
        # nothing, and it is offered again another day.
        logger.exception("Story %s: approval refused", story.id)
        label = "unclear"
        story.parent_answer = None
    plan.answered_story_id = story.id
    plan.outcome = f"story_readback_{label}"
    if label == "approve":
        plan.layer4.append(
            f"The user just agreed that their story may be shared with {child}. "
            f"Thank them and confirm in one short sentence that you will share "
            f"it with {child}, then carry on."
        )
    elif label == "correction":
        plan.correction_story_id = story.id
        plan.layer4.append(
            "The user corrected something in the story. Thank them, say you "
            "will put it right and read it to them again another day, then "
            "carry on."
        )
    elif label == "keep_private":
        plan.layer4.append(
            "The user does not want the story shared. Confirm in one short "
            "sentence that it stays between the two of you, without asking "
            "why, then carry on."
        )
    elif label == "not_now":
        plan.layer4.append(
            "The user wants to decide about the story later. Say that is fine "
            "in a few words and carry on."
        )


async def _retell_item(ref, db):
    """The Story or card a retelling ask is about."""
    story = await story_asks.get(ref, db)
    if story is not None:
        return "story", story
    card = await _card(ref, db)
    return ("card", card) if card is not None else (None, None)


def _still_waiting(kind, item) -> bool:
    if kind == "story":
        return bool(
            item.status == "story"
            and item.approval_status == "pending"
            and item.needs_retelling
        )
    if kind == "card":
        return bool(item.status == "correction" and item.needs_retelling)
    return False


async def _answer_retell_offer(kind, item, transcript, plan: TurnPlan, db) -> None:
    if not _still_waiting(kind, item):
        return
    label = await story_asks.classify_retell(transcript)
    plan.outcome = f"retell_{label}"
    thing = "story" if kind == "story" else "card"
    if label == "retell":
        plan.offered = RETELLING
        plan.offered_ref = item.id
        plan.tag_kinds.append(RETELLING)
        plan.layer4.append(
            f"The user would like to tell that part of the {thing} again. In one "
            "short question, ask them to tell you how that part went, in their "
            "own words. Ask nothing else in this reply. If you ask, add "
            f"{RETELLING} to the <asked> tag."
        )
    elif label == "set_aside":
        if kind == "story":
            await story_asks.set_aside(item, db)
        else:
            item.needs_retelling = False
            state.transition(item, "kept_private")
        plan.layer4.append(
            f"The user would rather set the {thing} aside. Confirm in one short "
            "sentence that it is set aside and is still theirs, without asking "
            "why, then carry on."
        )
    elif label == "not_now":
        plan.layer4.append(
            "The user wants to leave it for now. Say that is fine in a few words "
            "and carry on."
        )


async def _answer_retelling(kind, item, plan: TurnPlan) -> None:
    """Her message is that part told again: a source for the rewrite, which
    runs once the turn is saved, as for a correction."""
    if not _still_waiting(kind, item):
        return
    if kind == "story":
        item.parent_answer = "retelling"
        item.parent_answered_at = datetime.now(timezone.utc)
        plan.answered_story_id = item.id
        plan.correction_story_id = item.id
    else:
        plan.correction_card_id = item.id
    plan.outcome = "retelling"
    plan.layer4.append(
        "The user just told that part again. Thank them warmly, say you will "
        "write it again and read it to them another day, then carry on."
    )


async def _waiting_retell_card(user_id: str, db) -> Optional[ConversationCard]:
    return (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.user_id == user_id)
                .where(ConversationCard.status == "correction")
                .where(ConversationCard.needs_retelling.is_(True))
                .order_by(ConversationCard.parent_answered_at)
                .limit(1)
            )
        )
        .scalars()
        .first()
    )


# ── what this turn should carry ──────────────────────────────────────────────


def _share_due(profile, session_number: int) -> bool:
    asks = profile.share_preference_asks or 0
    if profile.share_preference == sharing.NOT_ASKED and asks == 0:
        return session_number >= FIRST_SHARE_SESSION
    if profile.share_preference == sharing.UNSURE and asks == 1:
        return session_number >= SECOND_SHARE_SESSION
    return False


async def _waiting_card(user_id: str, db) -> Optional[ConversationCard]:
    cards = (
        (
            await db.execute(
                select(ConversationCard)
                .where(ConversationCard.user_id == user_id)
                .where(ConversationCard.status == "awaiting_parent")
                .order_by(ConversationCard.created_at)
            )
        )
        .scalars()
        .all()
    )
    for card in cards:
        if (card.readback_offers or 0) >= MAX_READBACK_OFFERS:
            _expire_if_done(card)
            continue
        return card
    return None


async def _waiting_questions(user_id: str, db, now: datetime):
    rows = (
        (
            await db.execute(
                select(FamilyQuestion)
                .where(FamilyQuestion.user_id == user_id)
                .where(FamilyQuestion.status.in_(("pending", "released")))
                .order_by(FamilyQuestion.created_at)
            )
        )
        .scalars()
        .all()
    )
    questions, reactions = [], []
    for q in rows:
        created = q.created_at or now
        if q.kind == "question" and now - created > QUESTION_EXPIRY:
            q.status = "expired"
            continue
        if q.kind == "reaction":
            reactions.append(q)
        elif q.status == "released" or q.screening == "ok":
            questions.append(q)
    return questions, reactions


def _timing(exchange_count: int, goal_met: bool, what: str) -> Optional[str]:
    """§6.1: at the opening only after a greeting or small talk; otherwise
    at the closing exchange; never mid-story."""
    if exchange_count == 0:
        return (
            f"If the user's message is a greeting or small talk, end your reply "
            f"with {what}. If they have begun telling a story or something on "
            "their mind, do not do it now."
        )
    if goal_met:
        return (
            f"This is the closing exchange: before you say goodbye, end your "
            f"reply with {what}."
        )
    return None


async def before_dialogue(
    *,
    session_id: str,
    user_id: str,
    transcript: str,
    db: AsyncSession,
) -> TurnPlan:
    """Answer the pending ask, then plan this turn. Commits."""
    plan = TurnPlan()
    session = await _session(session_id, db)
    profile = await _profile(user_id, db)
    family = await family_of(user_id, db)
    child, title = _names(family, profile)
    now = datetime.now(timezone.utc)

    pending, ref = session.pending_ask, session.pending_ask_ref
    if pending:
        session.pending_ask = None
        session.pending_ask_ref = None
        card = await _card(ref, db) if pending in (READBACK_OFFER, READBACK) else None
        story = (
            await story_asks.get(ref, db)
            if pending in (STORY_READBACK_OFFER, STORY_READBACK)
            else None
        )
        if pending == SHARE:
            await _answer_share(profile, transcript, child, plan)
        elif pending == READBACK_OFFER:
            await _answer_offer(card, transcript, child, plan, db)
            if plan.readback_text:
                session.pending_ask = READBACK
                session.pending_ask_ref = card.id
        elif pending == READBACK:
            await _answer_readback(card, transcript, child, plan)
        elif pending == STORY_READBACK_OFFER:
            await _answer_story_offer(story, transcript, child, plan)
            if plan.readback_text:
                session.pending_ask = STORY_READBACK
                session.pending_ask_ref = story.id
        elif pending == STORY_READBACK:
            await _answer_story_readback(story, transcript, child, plan)
        elif pending == RETELL_OFFER:
            kind, item = await _retell_item(ref, db)
            await _answer_retell_offer(kind, item, transcript, plan, db)
        elif pending == RETELLING:
            kind, item = await _retell_item(ref, db)
            await _answer_retelling(kind, item, plan)
        # CHILD_QUESTION: her answer is ordinary conversation (§10.3).
        session.ask_outcome = plan.outcome

    if plan.readback_text:
        await db.commit()
        return plan

    # Reactions ride along with anything (§9.2).
    questions, reactions = await _waiting_questions(user_id, db, now)
    if reactions:
        reaction = reactions[0]
        card = await _card(reaction.card_id, db)
        about = f' about "{card.title}"' if card and card.title else ""
        plan.layer4.append(
            f"{child} read the card{about} and said they never knew this. If it "
            "fits naturally in this reply, tell the user in one sentence, "
            f"crediting {child}. No question is attached. If you do, add "
            f"{REACTION} to the <asked> tag."
        )
        plan.tag_kinds.append(REACTION)
        plan.reaction_ids = [reaction.id]

    if not session.ask_made and not session.pending_ask:
        await _plan_ask(plan, session, profile, questions, child, now, db)

    if session.goal_met:
        # §5.3: the recap closes her loop every session, card or no card.
        plan.layer4.append(
            "In this closing reply, also name in one sentence what the user told "
            'you today and say you have kept it safe (for example: "Today you '
            "told me about the walk to the talkies. I've kept it safe.\"). Do not "
            "add a question for this."
        )

    await db.commit()
    return plan


async def _plan_ask(plan, session, profile, questions, child, now, db) -> None:
    """§9.2 priority: the sharing question when due; then whichever of a
    card, a Story (STORIES.md D-b) and a child's question has waited
    longest."""
    if _share_due(profile, session.session_number):
        if session.exchange_count >= 1 or session.goal_met:
            when = (
                "This is the closing exchange, so ask it before you say goodbye."
                if session.goal_met
                else "If the user has just finished telling a story or a thought, "
                "this is the moment: make this your one question in this reply, "
                "instead of a follow-up question. If they are in the middle of "
                "telling something, do not ask yet; you will be reminded next turn."
            )
            plan.offered = SHARE
            plan.tag_kinds.append(SHARE)
            plan.layer4.append(
                "Sharing question (asked once, ever): ask the user whether they "
                f"would like you to share some of these stories with {child}, and "
                f"say you would always read anything to them first. {when} Ask it "
                "gently and accept whatever they say. If you ask it, add "
                f"{SHARE} to the <asked> tag."
            )
        return

    card = await _waiting_card(profile.user_id, db)
    story = await story_asks.waiting_story(profile, db)
    question = questions[0] if questions else None
    retell_story = await story_asks.waiting_retelling(profile, db)
    retell_card = await _waiting_retell_card(profile.user_id, db)
    waiting = [
        (card.operator_reviewed_at or card.created_at or now, "card") if card else None,
        (story_asks.waiting_since(story) or now, "story") if story else None,
        (question.created_at or now, "question") if question else None,
        (
            (story_asks.waiting_since_correction(retell_story) or now, "retell_story")
            if retell_story
            else None
        ),
        (
            (
                retell_card.parent_answered_at or retell_card.created_at or now,
                "retell_card",
            )
            if retell_card
            else None
        ),
    ]
    # Oldest first; on a tie the earlier kind in this list goes first, so
    # with no Story waiting the choice is the one cards always made.
    chosen = min((w for w in waiting if w), key=lambda w: w[0], default=None)
    kind = chosen[1] if chosen else None
    card = card if kind == "card" else None
    story = story if kind == "story" else None
    question = question if kind == "question" else None

    if kind in ("retell_story", "retell_card"):
        item = retell_story if kind == "retell_story" else retell_card
        _plan_retell_offer(plan, session, item, kind == "retell_story")
        return

    if story is not None:
        what = (
            "an offer to read them their story, the one about "
            f'"{story.title}", written down as they told it, to share with '
            f"{child} — for example: \"I've written down your story about "
            "[the story, in a few words], just as you told it to me, to share "
            f'with {child}. Shall I read it to you?"'
        )
        timing = _timing(session.exchange_count, session.goal_met, what)
        if timing:
            plan.offered = STORY_READBACK_OFFER
            plan.offered_ref = story.id
            plan.tag_kinds.append(STORY_READBACK_OFFER)
            plan.layer4.append(
                f"{timing} Name the story in the user's language. Only offer: "
                "never read, quote or retell the story yourself. If you offer, "
                f"add {STORY_READBACK_OFFER} to the <asked> tag."
            )
        return

    if card is not None:
        told = card.source_cutoff or card.created_at
        day = told.strftime("%A") if told else "earlier"
        what = (
            "an offer to read them something you wrote from what they told you "
            f"on {day}, to send to {child} — for example: \"Before we start, I've "
            f"written something from what you told me on {day}, to send to "
            f'{child}. Shall I read it to you?"'
        )
        timing = _timing(session.exchange_count, session.goal_met, what)
        if timing:
            plan.offered = READBACK_OFFER
            plan.offered_ref = card.id
            plan.tag_kinds.append(READBACK_OFFER)
            plan.layer4.append(
                f"{timing} Only offer: never read, quote or describe the card "
                f"yourself. If you offer, add {READBACK_OFFER} to the <asked> tag."
            )
        return

    if question is not None:
        plan.offered = CHILD_QUESTION
        plan.offered_ref = question.id
        plan.tag_kinds.append(CHILD_QUESTION)
        when = (
            "in this closing exchange"
            if session.goal_met
            else "at a natural pause, when the user has finished telling "
            "something, never in the middle of a story"
        )
        plan.layer4.append(
            f'{child} sent a question for the user: "{question.text}". Pass it on '
            f"{when}, saying it comes from {child}, in the user's language. It is "
            "the only question in this reply: if you pass it on, ask no question "
            "of your own before or after it. Never present it as your own "
            "curiosity. If they would rather not answer, accept that warmly and "
            "move on; never press, and never say anything will be passed back. "
            f"If you ask it, add {CHILD_QUESTION} to the <asked> tag."
        )


def _plan_retell_offer(plan: TurnPlan, session, item, is_story: bool) -> None:
    """§6c: Katha could not write her correction. Its own shortcoming, never
    hers; and from the second failed rewrite, setting it aside is offered as
    an easy choice. She decides, however many times it takes."""
    thing = "story" if is_story else "card"
    hint = ""
    if is_story:
        reasons = [str(r.get("reason") or "") for r in item.removed_sentences or []]
        unclear = story_asks.what_was_unclear(reasons)
        if unclear:
            hint = (
                " Your notes on that part (never say them as a fact, never "
                f"repeat what the story said there): {unclear}. Name the part "
                "as a short question in their language, such as who did "
                "something or what happened next."
            )
    what = (
        f"telling them, as your own shortcoming and never theirs, that you "
        f'could not write one part of their {thing} about "{item.title}" the '
        "way they told it, and asking whether they would like to tell you "
        f"that part again, or set this {thing} aside"
    )
    if (item.failed_rewrites or 0) >= 2:
        what += (
            ". Mention gently that setting it aside is perfectly all right: "
            "it is still their story"
        )
    timing = _timing(session.exchange_count, session.goal_met, what)
    if not timing:
        return
    plan.offered = RETELL_OFFER
    plan.offered_ref = item.id
    plan.tag_kinds.append(RETELL_OFFER)
    plan.layer4.append(
        f"{timing}{hint} If you ask, add {RETELL_OFFER} to the <asked> tag."
    )


async def after_dialogue(
    plan: TurnPlan,
    asked: set[str],
    *,
    session_id: str,
    user_id: str,
    db: AsyncSession,
) -> None:
    """Record the ask the model actually made. Commits."""
    now = datetime.now(timezone.utc)
    session = await _session(session_id, db)
    if plan.offered and plan.offered in asked:
        session.ask_made = True
        session.pending_ask = plan.offered
        session.pending_ask_ref = plan.offered_ref
        if plan.offered == SHARE:
            profile = await _profile(user_id, db)
            profile.share_preference_asks = (profile.share_preference_asks or 0) + 1
        elif plan.offered == READBACK_OFFER:
            card = await _card(plan.offered_ref, db)
            if card is not None:
                card.readback_offers = (card.readback_offers or 0) + 1
        elif plan.offered == STORY_READBACK_OFFER:
            story = await story_asks.get(plan.offered_ref, db)
            if story is not None:
                story.readback_offers = (story.readback_offers or 0) + 1
        elif plan.offered == RETELL_OFFER:
            _, item = await _retell_item(plan.offered_ref, db)
            if item is not None:
                item.readback_offers = (item.readback_offers or 0) + 1
        elif plan.offered == CHILD_QUESTION:
            await _mark_asked(plan.offered_ref, session_id, now, db)
    if REACTION in asked:
        for reaction_id in plan.reaction_ids:
            await _mark_asked(reaction_id, session_id, now, db)
    await db.commit()


async def after_turn_saved(
    plan: TurnPlan, turn_id: uuid.UUID, *, session_id: str, user_id: str, db
) -> None:
    """Record her answers with the turn that carries them. Commits."""
    if plan.readback_text:
        # The read-back was spoken: it is this session's one ask.
        session = await _session(session_id, db)
        session.ask_made = True
    if plan.answered_card_id is not None:
        card = await _card(plan.answered_card_id, db)
        if card is not None:
            card.parent_answer_turn_id = turn_id
            if card.status == "approved":
                # On its way: a send time before the child's next call
                # (cards/schedule.py). If this fails, the delivery job
                # schedules it on its next run.
                try:
                    await schedule.schedule_card(card, db)
                except Exception:
                    logger.exception("Could not schedule card %s", card.id)
    if plan.answered_story_id is not None:
        story = await story_asks.get(plan.answered_story_id, db)
        if story is not None and story.parent_answer:
            # The evidence for her answer. Telling the family about an
            # approved Story is Stage 3 (STORIES.md §2.4).
            story.parent_answer_turn_id = turn_id
    await db.commit()
    if plan.share_decision is not None:
        profile = await _profile(user_id, db)
        await sharing.set_preference(profile, plan.share_decision, db, turn_id=turn_id)


async def _mark_asked(question_id, session_id: str, now: datetime, db) -> None:
    question = (
        await db.execute(select(FamilyQuestion).where(FamilyQuestion.id == question_id))
    ).scalar_one_or_none()
    if question is not None:
        question.status = "asked"
        question.asked_in_session_id = uuid.UUID(session_id)
        question.asked_at = now


async def has_pending_ask(session_id: str, db: AsyncSession) -> bool:
    """While she owes an answer, the session stays open for it."""
    if not settings.CONVERSATION_CARDS_ENABLED:
        return False
    return bool((await _session(session_id, db)).pending_ask)
