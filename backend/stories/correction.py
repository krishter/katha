"""
She corrected a Story after hearing it read back, or told a part of it
again (docs/specs/STORIES.md §2.3, §6c; proposal §6.4).

Her turn becomes a source, kept on the Story. The Story is composed again
from every telling plus that turn, checked sentence by sentence as before,
and saved as a new version; the version she heard is kept in
story_versions. Its read-back is written and checked again, and it goes
back to her for one more read-back.

When the rewrite cannot pass its checks, no one at Katha fixes it (§6c):
the Story waits for her, and in a later session Katha says it could not
write that part the way she told it and asks whether she would like to tell
it again or set the story aside (stories/asks.py). That goes on until she
chooses. With STORIES_OPERATOR_REVIEW on, it goes to the operator instead,
as before.

Nothing here can show the family anything: the Story stays pending. Runs as
a background task after the turn is saved. Never raises.
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

# With operator review on, a second correction goes to the operator.
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
        f"Story {story.id} for user {story.user_id}: {what}.",
    )


async def _cannot_write(story: Story, what: str, db: AsyncSession) -> None:
    """The rewrite did not pass. With no reviewer it waits for her (§6c);
    with one, it goes to the operator as before. The Story keeps the words
    she last heard; nothing is offered for approval until a rewrite passes."""
    story.failed_rewrites = (story.failed_rewrites or 0) + 1
    if settings.STORIES_OPERATOR_REVIEW:
        _back_to_operator(story, what)
        await db.commit()
        await _alert(story, what)
        return
    story.needs_retelling = True
    story.readback_offers = 0
    story.notes = [*(story.notes or []), f"Waiting for her: {what}"]
    await db.commit()


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
    """Every turn the Story was told from: its arc, any turn a sentence
    cites, and every correction or retelling she added."""
    ids = set(store.to_thread(story, []).arc.source_turn_ids)
    for sentence in story.sentence_sources or []:
        ids |= {str(t) for t in (sentence.get("turn_ids") or [])}
    ids |= {str(t) for t in (story.added_source_turn_ids or [])}
    return ids


async def _rewrite(story_id: uuid.UUID, turn_id: uuid.UUID, db: AsyncSession):
    story = await db.get(Story, story_id)
    if (
        story is None
        or story.status != "story"
        or story.approval_status != "pending"
        or story.parent_answer not in ("correction", "retelling")
    ):
        return
    profile = (
        await db.execute(
            select(UserProfileModel).where(UserProfileModel.user_id == story.user_id)
        )
    ).scalar_one()

    correction = str(turn_id)
    # Her yes to the old words no longer stands, and what she said is kept
    # as a source for every later rewrite.
    story.approved_hash = None
    story.readback_offers = 0
    if correction not in [str(t) for t in story.added_source_turn_ids or []]:
        story.added_source_turn_ids = [*(story.added_source_turn_ids or []), correction]

    if (
        settings.STORIES_OPERATOR_REVIEW
        and (story.correction_round or 0) > MAX_AUTOMATIC_CORRECTIONS
    ):
        _back_to_operator(story, f"Corrected again in turn {turn_id}: by hand.")
        await db.commit()
        await _alert(story, "she corrected it a second time")
        return

    turns = await _turns(source_turn_ids(story), db)
    if correction not in {t["id"] for t in turns}:
        # Private, or its privacy not settled: never a source.
        await _cannot_write(story, "her correction could not be used", db)
        return

    incident = {
        "working_title": story.title,
        "when_approx": story.when_approx,
        "people": list(story.people or []),
        "places": list(story.places or []),
    }
    usage = Usage()

    async def compose(cut=None):
        # A fresh thread each try: compose_and_check writes into it.
        return await compose_and_check(
            incident=incident,
            thread=store.to_thread(story, []),
            all_turns=turns,
            call_llm=consolidate._call_llm,
            usage=usage,
            correction_turn_id=correction,
            cut=cut,
        )

    redone = await compose()
    if redone.removed_sentences:
        # TC-41: a cut sentence can take the moment the story turns on with
        # it. One more try, told what was cut and why; kept only if it is a
        # Story with fewer cuts.
        retry = await compose(cut=list(redone.removed_sentences))
        if (
            retry.status == "story"
            and retry.body_first_person
            and (
                redone.status != "story"
                or len(retry.removed_sentences) < len(redone.removed_sentences)
            )
        ):
            redone = retry
    if redone.status != "story" or not redone.body_first_person:
        # What was cut, and why: Katha names that part when it asks her.
        story.removed_sentences = list(redone.removed_sentences)
        await _cannot_write(story, "the rewrite did not pass its check", db)
        return

    # The version she heard, kept as it was, then the new one.
    _keep_version(story, db)
    story.version = (story.version or 1) + 1
    story.title = redone.title or story.title
    story.body_first_person = redone.body_first_person
    story.quotes = list(redone.quotes)
    story.sentence_sources = list(redone.sentence_sources)
    story.removed_sentences = list(redone.removed_sentences)
    story.fidelity_status = redone.fidelity_status
    story.parent_answer = None
    story.needs_retelling = False
    story.review_status = (
        "operator_review" if settings.STORIES_OPERATOR_REVIEW else "passed"
    )
    story.operator_reviewed_at = None
    note = f"Version {story.version}: her correction."
    if redone.removed_sentences:
        note += f" The check cut {len(redone.removed_sentences)} sentence(s)."
    story.notes = [*(story.notes or []), note]
    await db.commit()

    # Writes, checks and fingerprints the new read-back. A failed check
    # means it cannot be read to her: it waits for her as above.
    await consolidate.write_readback_for(story, story.user_id, profile, db)
    if (story.readback_check or {}).get("result") != "passed":
        await _cannot_write(story, "the new read-back did not pass its check", db)
    logger.info("Story %s re-composed as version %d", story.id, story.version)
