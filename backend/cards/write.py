"""
Drafting a card after a session (docs/specs/CONVERSATION_CARDS.md §7.1–§7.4):
choose, write, check.

1. Selection (card_select) picks one moment from the candidates, or none.
2. Writing (card_write) makes the card from the raw transcripts of the
   chosen atoms' turns.
3. Fidelity (card_fidelity) checks hook, excerpt, call prompt and quote
   sentence by sentence against those transcripts. Any unsupported
   sentence, or a field over its length, gets one rewrite and one more
   check (the consolidation pass's two-pass budget). Still failing, the
   card is dropped as failed_fidelity and its atoms go back into the pool.

A card that passes goes to the operator (D3), or straight to her when
CARDS_OPERATOR_REVIEW is off. Nothing here sends anything to anyone but
the operator.

Runs off the critical path after a session. Logs and never raises.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

import cards
from adapters import llm
from adapters.llm import Message
from cards import candidates, state
from cards import image as card_image
from cards import prompts as card_prompts
from cards.context import family_of
from config import settings
from consolidation.pipeline import parse_json_object
from core.email import send_operator_alert
from models.conversation_card import ConversationCard
from models.family_question import FamilyQuestion
from models.session import Session
from models.turn import Turn
from models.user_profile import UserProfileModel
from prompts.system_prompt import _language_name

logger = logging.getLogger(__name__)

SELECT_MAX_TOKENS = 1000
WRITE_MAX_TOKENS = 2000
FIDELITY_MAX_TOKENS = 3000
# Same budget as consolidation/pipeline._MAX_FIDELITY_PASSES: the first
# check, one rewrite, one re-check.
MAX_FIDELITY_PASSES = 2

FIELDS = (
    "title",
    "hook",
    "excerpt",
    "original_quote",
    "original_quote_gloss",
    "call_prompt",
    "held_back_note",
    "readback_text",
)
_HOOK_OPENINGS = ("did you know", "you asked")


@dataclass
class DraftOutcome:
    """What happened, for logs and tests. `card_id` is set when a card
    row was written (passed or failed)."""

    result: str
    reason: str = ""
    card_id: Optional[uuid.UUID] = None


async def _ask(prompt: str, purpose: str, max_tokens: int) -> dict:
    response = await llm.chat(
        [Message(role="user", content=prompt)],
        max_tokens=max_tokens,
        model=settings.LLM_CARD_MODEL,
        purpose=purpose,
    )
    return parse_json_object(response.content)


def _words(text: Optional[str]) -> int:
    return len((text or "").split())


def shape_problems(fields: dict) -> list[str]:
    """The §7.3 rules code can check."""
    problems = []
    for name in ("title", "hook", "excerpt", "call_prompt", "readback_text"):
        if not str(fields.get(name) or "").strip():
            problems.append(f"{name} is empty")
    hook = str(fields.get("hook") or "").strip()
    if hook and not hook.lower().startswith(_HOOK_OPENINGS):
        problems.append('hook must start "Did you know"')
    if _words(hook) > card_prompts.HOOK_MAX_WORDS:
        problems.append(
            f"hook is {_words(hook)} words; at most {card_prompts.HOOK_MAX_WORDS}"
        )
    if _words(fields.get("excerpt")) > card_prompts.EXCERPT_MAX_WORDS:
        problems.append(
            f"excerpt is {_words(fields.get('excerpt'))} words; at most "
            f"{card_prompts.EXCERPT_MAX_WORDS}"
        )
    if _words(fields.get("call_prompt")) > card_prompts.CALL_PROMPT_MAX_WORDS:
        problems.append(
            f"call_prompt is {_words(fields.get('call_prompt'))} words; at most "
            f"{card_prompts.CALL_PROMPT_MAX_WORDS}"
        )
    if bool(fields.get("original_quote")) != bool(fields.get("original_quote_gloss")):
        problems.append("original_quote and its gloss go together")
    return problems


def _clean(raw: dict) -> dict:
    fields = {}
    for name in FIELDS:
        value = raw.get(name)
        fields[name] = str(value).strip() if value not in (None, "") else None
    for required in ("title", "hook", "excerpt", "call_prompt", "readback_text"):
        fields[required] = fields[required] or ""
    fields["held_back_note"] = fields["held_back_note"] or ""
    return fields


async def load_turns(turn_ids: list[uuid.UUID], db: AsyncSession) -> list[dict]:
    """Her turns, oldest first, each with the Katha question it answered
    (the previous turn's reply) and its conversation number."""
    if not turn_ids:
        return []
    rows = (
        await db.execute(
            select(Turn, Session.session_number)
            .join(Session, Session.id == Turn.session_id)
            .where(Turn.id.in_(turn_ids))
            .where(Turn.is_private.is_(False))
            .order_by(Turn.created_at)
        )
    ).all()
    turns = []
    for turn, session_number in rows:
        previous = (
            await db.execute(
                select(Turn.response_text)
                .where(Turn.session_id == turn.session_id)
                .where(Turn.turn_number == turn.turn_number - 1)
            )
        ).scalar_one_or_none()
        turns.append(
            {
                "id": str(turn.id),
                "session_number": session_number,
                "date": turn.created_at.date().isoformat() if turn.created_at else "",
                "detected_language": turn.detected_language,
                "transcript": turn.transcript,
                "katha_asked": previous or "",
            }
        )
    return turns


async def _answered_questions(user_id: str, db: AsyncSession) -> list[dict]:
    """Questions already raised with her that no card has answered yet."""
    answered = {
        qid
        for qid in (
            await db.execute(
                select(ConversationCard.answers_question_id)
                .where(ConversationCard.user_id == user_id)
                .where(ConversationCard.answers_question_id.is_not(None))
                .where(ConversationCard.status != "failed_fidelity")
            )
        )
        .scalars()
        .all()
    }
    rows = (
        await db.execute(
            select(FamilyQuestion, Session.session_number)
            .outerjoin(Session, Session.id == FamilyQuestion.asked_in_session_id)
            .where(FamilyQuestion.user_id == user_id)
            .where(FamilyQuestion.status == "asked")
            .order_by(FamilyQuestion.asked_at.desc())
            .limit(5)
        )
    ).all()
    return [
        {"id": str(q.id), "text": q.text, "session_number": n}
        for q, n in rows
        if q.id not in answered
    ]


async def _recent_titles(user_id: str, db: AsyncSession) -> list[str]:
    rows = await db.execute(
        select(ConversationCard.title)
        .where(ConversationCard.user_id == user_id)
        .where(ConversationCard.title != "")
        .order_by(ConversationCard.created_at.desc())
        .limit(10)
    )
    return [t for t in rows.scalars().all() if t]


async def check_fidelity(
    fields: dict, turns: list[dict], parent_title: str
) -> tuple[list[dict], list[str]]:
    """Returns (sentences, problems). An empty or unreadable check is a
    failure, never a pass."""
    checked = await _ask(
        card_prompts.build_fidelity_prompt(
            card=fields, turns=turns, parent_title=parent_title
        ),
        "card_fidelity",
        FIDELITY_MAX_TOKENS,
    )
    known = {t["id"] for t in turns}
    sentences = []
    for s in checked.get("sentences") or []:
        if not isinstance(s, dict) or not str(s.get("text", "")).strip():
            continue
        sentences.append(
            {
                "field": str(s.get("field", "")),
                "text": str(s.get("text", "")).strip(),
                "verdict": str(s.get("verdict", "unsupported")),
                # A citation to a turn the check was not shown is no citation.
                "turn_ids": [
                    str(t) for t in (s.get("turn_ids") or []) if str(t) in known
                ],
                "reason": str(s.get("reason", "")),
            }
        )
    if not sentences:
        return [], ["the fidelity check returned no sentences"]
    problems = []
    for s in sentences:
        verdict = s["verdict"]
        if verdict not in ("supported", "connective"):
            problems.append(
                f'{s["field"] or "sentence"} "{s["text"]}" is not supported by what '
                f"was said: {s['reason'] or 'no reason given'}"
            )
        elif verdict == "supported" and not s["turn_ids"]:
            problems.append(f'"{s["text"]}" cites no turn it came from')
    fields_checked = {s["field"] for s in sentences}
    for name in ("hook", "excerpt"):
        if fields.get(name) and name not in fields_checked:
            problems.append(f"the fidelity check did not cover the {name}")
    return sentences, problems


async def _check_and_rewrite(
    card: ConversationCard,
    fields: dict,
    write_args: dict,
    source_turns: list[dict],
    parent_title: str,
) -> tuple[bool, list[dict]]:
    """Check; on any problem rewrite once and check again (§7.4)."""
    passes = []
    for attempt in range(MAX_FIDELITY_PASSES):
        sentences, problems = await check_fidelity(fields, source_turns, parent_title)
        problems = shape_problems(fields) + problems
        passes.append(
            {"fields": dict(fields), "sentences": sentences, "problems": problems}
        )
        if not problems:
            return True, passes
        if attempt + 1 == MAX_FIDELITY_PASSES:
            break
        # Keep any standing instruction (her correction) alongside what the
        # check found.
        fixes = [*(write_args.get("fixes") or []), *problems]
        fields = _clean(
            await _ask(
                card_prompts.build_writing_prompt(
                    **{**write_args, "fixes": fixes}, previous=fields
                ),
                "card_write",
                WRITE_MAX_TOKENS,
            )
        )
        state.set_text(card, **fields)
    return False, passes


async def draft_for_session(
    session_id: str, user_id: str, db: AsyncSession
) -> DraftOutcome:
    """Draft at most one card after a session. Never raises."""
    try:
        return await _draft(session_id, user_id, db)
    except Exception:
        logger.exception("Card drafting failed after session %s", session_id)
        await db.rollback()
        return DraftOutcome("error")


async def _draft(session_id: str, user_id: str, db: AsyncSession) -> DraftOutcome:
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == user_id)
        )
    ).scalar_one_or_none()
    if not cards.enabled_for(profile):
        return DraftOutcome("off")

    why_not = await candidates.blocked(profile, db)
    if why_not:
        logger.info("No card for %s after %s: %s", user_id, session_id, why_not)
        return DraftOutcome("blocked", why_not)

    atoms = await candidates.candidate_atoms(profile, db)
    if not atoms:
        return DraftOutcome("no_candidates")

    family = await family_of(user_id, db)
    parent_title = (family.parent_title if family else None) or profile.name
    child_name = (family.child_name if family else None) or "their child"
    pronouns = profile.pronouns or ""

    turns = await load_turns([a.turn_id for a in atoms], db)
    by_turn = {t["id"] for t in turns}
    atoms = [a for a in atoms if str(a.turn_id) in by_turn]
    if not atoms:
        return DraftOutcome("no_candidates", "no readable source turns")
    questions = await _answered_questions(user_id, db)

    selected = await _ask(
        card_prompts.build_selection_prompt(
            candidates=[
                {
                    "id": str(a.id),
                    "turn_id": str(a.turn_id),
                    "title": a.title,
                    "narrative": a.narrative,
                }
                for a in atoms
            ],
            turns=turns,
            private_topics=list(profile.private_topics or []),
            recent_titles=await _recent_titles(user_id, db),
            answered_questions=questions,
            parent_title=parent_title,
            child_name=child_name,
            pronouns=pronouns,
        ),
        "card_select",
        SELECT_MAX_TOKENS,
    )
    by_id = {str(a.id): a for a in atoms}
    chosen = [
        by_id[str(i)] for i in (selected.get("atom_ids") or []) if str(i) in by_id
    ]
    if not chosen:
        logger.info(
            "Card selection chose none for %s: %s", user_id, selected.get("why", "")
        )
        return DraftOutcome("none_selected", str(selected.get("why", "")))

    question_ids = {q["id"]: q for q in questions}
    answers = str(selected.get("answers_question_id") or "")
    question = question_ids.get(answers)

    chosen_turn_ids = {str(a.turn_id) for a in chosen}
    source_turns = [t for t in turns if t["id"] in chosen_turn_ids]
    language_name = _language_name(profile.preferred_language)

    write_args = dict(
        turns=source_turns,
        parent_title=parent_title,
        child_name=child_name,
        language_name=language_name,
        pronouns=pronouns,
        question=question["text"] if question else "",
    )
    fields = _clean(
        await _ask(
            card_prompts.build_writing_prompt(**write_args),
            "card_write",
            WRITE_MAX_TOKENS,
        )
    )

    card = ConversationCard(
        id=uuid.uuid4(),
        user_id=user_id,
        status="draft",
        source_atom_ids=[a.id for a in chosen],
        source_turn_ids=[uuid.UUID(t) for t in sorted(chosen_turn_ids)],
        source_cutoff=max(a.created_at for a in chosen),
        answers_question_id=uuid.UUID(question["id"]) if question else None,
        source_language=source_turns[0].get("detected_language")
        if source_turns
        else None,
        readback_language=profile.preferred_language,
    )
    state.set_text(card, **fields)

    passed, passes = await _check_and_rewrite(
        card, fields, write_args, source_turns, parent_title
    )

    card.fidelity_json = {
        "result": "passed" if passed else "failed",
        "rewritten": len(passes) > 1,
        "sentences": passes[-1]["sentences"],
        "passes": passes,
        "selection_why": str(selected.get("why", "")),
    }

    if not passed:
        state.transition(card, "failed_fidelity")
        db.add(card)
        await db.commit()
        logger.info(
            "Card for %s failed its fidelity check: %s", user_id, passes[-1]["problems"]
        )
        return DraftOutcome(
            "failed_fidelity", "; ".join(passes[-1]["problems"]), card.id
        )

    await card_image.try_render(card, parent_title)
    review = settings.CARDS_OPERATOR_REVIEW
    state.transition(card, "operator_review" if review else "awaiting_parent")
    db.add(card)
    await db.commit()
    logger.info("Drafted card %s for %s (%s)", card.id, user_id, card.status)
    if review:
        await send_operator_alert(
            "A conversation card is waiting for review",
            f'Card "{card.title}" for user {user_id} is ready at /admin/cards/view. '
            "Target: reviewed within 24 hours.",
        )
    return DraftOutcome("drafted", card.status, card.id)


async def rewrite_for_correction(
    card_id: uuid.UUID, turn_id: uuid.UUID, db: AsyncSession
) -> None:
    """
    She corrected the card after hearing it (§6.3). Her correction turn
    becomes a source, the card is rewritten and checked, and it goes back
    to the operator and then to her once more. A second correction goes to
    the operator to resolve by hand. Never raises.
    """
    try:
        await _rewrite_for_correction(card_id, turn_id, db)
    except Exception:
        logger.exception("Rewriting corrected card %s failed", card_id)
        await db.rollback()


async def _rewrite_for_correction(card_id, turn_id, db) -> None:
    card = (
        await db.execute(select(ConversationCard).where(ConversationCard.id == card_id))
    ).scalar_one_or_none()
    if card is None or card.status != "correction":
        return
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == card.user_id)
        )
    ).scalar_one()
    family = await family_of(card.user_id, db)
    parent_title = (family.parent_title if family else None) or profile.name
    child_name = (family.child_name if family else None) or "their child"

    card.source_turn_ids = [*(card.source_turn_ids or []), turn_id]
    # Read back once more after this: the offer count starts again.
    card.readback_offers = 0
    card.parent_answer = None
    card.approved_hash = None

    if (card.correction_round or 0) >= 2:
        card.fidelity_json = {
            **(card.fidelity_json or {}),
            "note": "Second correction from her: resolve by hand.",
        }
        state.transition(card, "operator_review")
        await db.commit()
        await send_operator_alert(
            "A card needs a correction resolved by hand",
            f'Card "{card.title}" for user {card.user_id} was corrected twice. '
            "See /admin/cards/view.",
        )
        return

    source_turns = await load_turns(list(card.source_turn_ids), db)
    correction = next((t for t in source_turns if t["id"] == str(turn_id)), None)
    said = correction["transcript"] if correction else "(not readable)"
    current = {name: getattr(card, name) for name in FIELDS}
    write_args = dict(
        turns=source_turns,
        parent_title=parent_title,
        child_name=child_name,
        language_name=_language_name(profile.preferred_language),
        pronouns=profile.pronouns or "",
        fixes=[
            f'{parent_title} heard the card read back and corrected it: "{said}". '
            "Change what the correction requires and nothing else."
        ],
    )
    fields = _clean(
        await _ask(
            card_prompts.build_writing_prompt(**write_args, previous=current),
            "card_write",
            WRITE_MAX_TOKENS,
        )
    )
    state.set_text(card, **fields)
    passed, passes = await _check_and_rewrite(
        card, fields, write_args, source_turns, parent_title
    )
    card.fidelity_json = {
        "result": "passed" if passed else "failed",
        "rewritten": len(passes) > 1,
        "sentences": passes[-1]["sentences"],
        "passes": passes,
        "correction": said,
    }
    if not passed:
        state.transition(card, "failed_fidelity")
        await db.commit()
        logger.info("Corrected card %s failed its fidelity check", card.id)
        return

    card.image_s3_key = None
    await card_image.try_render(card, parent_title)
    review = settings.CARDS_OPERATOR_REVIEW
    state.transition(card, "operator_review" if review else "awaiting_parent")
    await db.commit()
    if review:
        await send_operator_alert(
            "A corrected conversation card is waiting for review",
            f'Card "{card.title}" for user {card.user_id} was corrected by her and '
            "rewritten. See /admin/cards/view.",
        )
