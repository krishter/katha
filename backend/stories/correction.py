"""
She corrected a Story after hearing it read back (docs/specs/STORIES.md
§2.3, proposal §6.4).

Her correction turn becomes a source. The Story is composed again from
every telling plus the correction, checked sentence by sentence as before,
and saved as a new version; the version she heard is kept in
story_versions. Its read-back is written and checked again, and it goes
back to the operator (D-a) and then to her for one more read-back.

A second correction, or a correction the pipeline cannot turn into a Story
that passes its checks, goes to the operator to resolve by hand, as for
cards. Nothing here can show the family anything: the Story leaves pending.
Runs as a background task after the turn is saved. Never raises.
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from config import settings
from consolidation.pipeline import Usage, compose_and_check
from core.email import send_operator_alert
from models.session import Session
from models.story import Story, StoryVersion
from models.turn import Turn
from models.user_profile import UserProfileModel
from stories import consolidate, store

logger = logging.getLogger(__name__)

MAX_AUTOMATIC_CORRECTIONS = 1


async def rewrite_for_correction(
    story_id: uuid.UUID, turn_id: uuid.UUID, db: AsyncSession
) -> None:
    try:
        await _rewrite(story_id, turn_id, db)
    except Exception:
        logger.exception("Rewriting corrected story %s failed", story_id)
        await db.rollback()


def _back_to_operator(story: Story, note: str) -> None:
    story.review_status = "operator_review"
    story.operator_reviewed_at = None
    story.notes = [*(story.notes or []), note]


async def _alert(story: Story, what: str) -> None:
    await send_operator_alert(
        "A story needs a correction resolved by hand",
        f'Story "{story.title}" for user {story.user_id}: {what}. '
        "See /admin/stories/view.",
    )


def _keep_version(story: Story, db: AsyncSession) -> None:
    """The version she heard and corrected, kept as it was."""
    db.add(
        StoryVersion(
            id=uuid.uuid4(),
            story_id=story.id,
            user_id=story.user_id,
            version=story.version,
            title=story.title,
            body_first_person=story.body_first_person,
            quotes=list(story.quotes or []),
            sentence_sources=list(story.sentence_sources or []),
            readback_text=story.readback_text or "",
            approval_status=story.approval_status,
        )
    )


async def _turns(turn_ids: set[str], db: AsyncSession) -> list[dict]:
    wanted = [i for i in (store.parse_id(t) for t in turn_ids) if i]
    if not wanted:
        return []
    rows = await db.execute(
        select(Turn, Session.session_number)
        .join(Session, Session.id == Turn.session_id)
        .where(Turn.id.in_(wanted))
        # Not private, and privacy settled: the rewrite is queued after the
        # correction turn's own extraction, which may mark it private.
        .where(consolidate._readable())
        .order_by(Session.session_number, Turn.turn_number)
    )
    return [consolidate._turn_dict(turn, number) for turn, number in rows.all()]


def source_turn_ids(story: Story) -> set[str]:
    """Every turn the Story was told from: its arc, and any turn a sentence
    cites (a corrected Story cites her correction)."""
    ids = set(store.to_thread(story, []).arc.source_turn_ids)
    for sentence in story.sentence_sources or []:
        ids |= {str(t) for t in (sentence.get("turn_ids") or [])}
    return ids


async def _rewrite(story_id: uuid.UUID, turn_id: uuid.UUID, db: AsyncSession):
    story = await db.get(Story, story_id)
    if (
        story is None
        or story.status != "story"
        or story.approval_status != "pending"
        or story.parent_answer != "correction"
    ):
        return
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == story.user_id)
        )
    ).scalar_one()

    # Whatever happens next, her yes to the old words no longer stands and
    # the old words are kept.
    _keep_version(story, db)
    story.approved_hash = None
    story.readback_offers = 0

    if (story.correction_round or 0) > MAX_AUTOMATIC_CORRECTIONS:
        _back_to_operator(story, f"Corrected again in turn {turn_id}: by hand.")
        await db.commit()
        await _alert(story, "she corrected it a second time")
        return

    correction = str(turn_id)
    turns = await _turns(source_turn_ids(story) | {correction}, db)
    if correction not in {t["id"] for t in turns}:
        _back_to_operator(story, f"Correction turn {turn_id} is not readable.")
        await db.commit()
        await _alert(story, "her correction could not be read")
        return

    thread = store.to_thread(story, [])
    incident = {
        "working_title": story.title,
        "when_approx": story.when_approx,
        "people": list(story.people or []),
        "places": list(story.places or []),
    }
    usage = Usage()
    redone = await compose_and_check(
        incident=incident,
        thread=thread,
        all_turns=turns,
        call_llm=consolidate._call_llm,
        usage=usage,
        correction_turn_id=correction,
    )
    if redone.status != "story" or not redone.body_first_person:
        _back_to_operator(
            story,
            f"Correction in turn {turn_id} did not survive the fidelity check: "
            + "; ".join(redone.notes[-2:]),
        )
        await db.commit()
        await _alert(story, "the corrected version did not pass its check")
        return

    story.version = (story.version or 1) + 1
    story.title = redone.title or story.title
    story.body_first_person = redone.body_first_person
    story.quotes = list(redone.quotes)
    story.sentence_sources = list(redone.sentence_sources)
    story.removed_sentences = list(redone.removed_sentences)
    story.fidelity_status = redone.fidelity_status
    story.parent_answer = None
    story.review_status = (
        "operator_review" if settings.STORIES_OPERATOR_REVIEW else "passed"
    )
    story.operator_reviewed_at = None
    note = f"Version {story.version}: her correction."
    if redone.removed_sentences:
        # A cut can take the moment the story turns on with it, and the arc
        # test can still pass: the operator reads what was cut.
        note += (
            f" The check cut {len(redone.removed_sentences)} sentence(s); "
            "read them before passing it."
        )
    story.notes = [*(story.notes or []), note]
    await db.commit()

    # Writes, checks and fingerprints the new read-back. A failed check
    # leaves it with the operator: it cannot be passed until one passes.
    await consolidate.write_readback_for(story, story.user_id, profile, db)
    if (story.readback_check or {}).get("result") != "passed" and (
        story.review_status == "passed"
    ):
        _back_to_operator(story, "The corrected read-back did not pass its check.")
        await db.commit()
    logger.info("Story %s re-composed as version %d", story.id, story.version)
