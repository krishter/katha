"""
Erase everything Katha holds about one family (DPDP; PRE_PILOT_FIX_PLAN P4).

One sweep, three callers:
  - the family, from the dashboard (DELETE /user/{user_id});
  - an operator (scripts/erase_user.py), for a parent who asked someone to
    erase her, or a family who cannot sign in;
  - the scheduler, when a withdrawal's 7-day hold passes
    (core/withdrawal.py).

Best-effort: each step is isolated, so one failure (an S3 delete, say)
does not leave the rest stranded. Every failure is logged and reported,
and nothing here raises. Consent records are kept, anonymised, as the
audit trail the DPDP Act expects; everything else goes. Each erasure
leaves one erasure_log row naming no one.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from core import twilio_cleanup
from media import storage
from models.api_usage import ApiUsage
from models.consent_record import ConsentRecord
from models.conversation_card import ConversationCard
from models.erasure_log import ErasureLog
from models.fact import Fact
from models.family_account import FamilyAccount
from models.family_question import FamilyQuestion
from models.magic_link_token import MagicLinkToken
from models.media_object import MediaObject
from models.memory_card import MemoryCard
from models.operator_alert import TurnFailure
from models.session import Session
from models.story_atom import StoryAtom
from models.turn import Turn
from models.user_profile import UserProfileModel
from models.voice_change import VoiceChange

logger = logging.getLogger(__name__)

ACTOR_FAMILY = "family"
ACTOR_HOLD = "scheduler:withdrawal-hold"


def hash_user_id(user_id: str) -> str:
    return hashlib.sha256(user_id.encode()).hexdigest()


@dataclass
class ErasureReport:
    counts: dict[str, int] = field(default_factory=dict)
    failures: list[str] = field(default_factory=list)

    @property
    def complete(self) -> bool:
        return not self.failures


async def _object_keys(user_id: str, db: AsyncSession, report: ErasureReport):
    """Every S3 key the database knows for this user."""
    keys: list[str] = []
    for label, column, model in (
        ("memory_cards", MemoryCard.image_s3_key, MemoryCard),
        # Voice notes are uploaded inside send_voice_note, after the turn is
        # committed, so the key is a separate follow-up write
        # (orchestrator.set_turn_audio_key).
        ("turn_audio", Turn.response_audio_s3_key, Turn),
        # The session-opening voice note has no Turn of its own (S2.4a);
        # this was the object the S1.2 consent audit found stranded.
        ("session_open_audio", Session.session_open_audio_s3_key, Session),
        # Conversation card images (CONVERSATION_CARDS.md §11).
        ("conversation_cards", ConversationCard.image_s3_key, ConversationCard),
        # Everything else uploaded for her, the consent welcome and other
        # system voice notes among it (P6, M-08).
        ("media_objects", MediaObject.s3_key, MediaObject),
    ):
        try:
            rows = await db.execute(
                select(column)
                .where(model.user_id == user_id)
                .where(column.is_not(None))
            )
            keys += [key for key in rows.scalars().all() if key]
        except Exception:
            logger.exception("Erasure: could not list %s keys for %s", label, user_id)
            report.failures.append(f"list:{label}")
    return keys


async def erase_user(user_id: str, db: AsyncSession, *, actor: str) -> ErasureReport:
    """Erase one family's data. Never raises; read the report."""
    report = ErasureReport()
    logger.info("Erasure of %s requested by %s", user_id, actor)

    # 1. Objects in the bucket, while the rows that name them still exist.
    removed = 0
    for key in sorted(set(await _object_keys(user_id, db, report))):
        try:
            await storage.delete_media(key)
            removed += 1
        except Exception:
            logger.exception("Erasure: could not delete s3 %s for %s", key, user_id)
            report.failures.append(f"s3:{key}")
    report.counts["s3_objects"] = removed

    # 1b. Twilio's copies of her messages (P6, D8). A failure stays queued
    # under "DELETED" and the scheduler keeps trying.
    try:
        gone, failed = await twilio_cleanup.erase_for_user(user_id, db)
        report.counts["twilio_messages"] = gone
        report.failures += [f"twilio:{sid}" for sid in failed]
    except Exception:
        logger.exception("Erasure: could not delete Twilio messages for %s", user_id)
        report.failures.append("twilio")

    # 2. Rows that belong to this user_id. Children before parents.
    for label, stmt in [
        ("memory_cards", delete(MemoryCard).where(MemoryCard.user_id == user_id)),
        ("story_atoms", delete(StoryAtom).where(StoryAtom.user_id == user_id)),
        ("turns", delete(Turn).where(Turn.user_id == user_id)),
        ("facts", delete(Fact).where(Fact.user_id == user_id)),
        # crisis_events go with their sessions (ON DELETE CASCADE).
        ("sessions", delete(Session).where(Session.user_id == user_id)),
        (
            "user_profiles",
            delete(UserProfileModel).where(UserProfileModel.user_id == user_id),
        ),
        # The usage ledger holds counts, not content, but it is keyed to
        # her and outlives nothing (COST_TO_SERVE.md §3.6).
        ("api_usage", delete(ApiUsage).where(ApiUsage.user_id == user_id)),
        # Which voice she was moved between, and by whom (GEMINI_TTS.md §4.2).
        ("voice_changes", delete(VoiceChange).where(VoiceChange.user_id == user_id)),
        # Conversation cards, kept-private ones included, and the child's
        # questions and reactions.
        (
            "conversation_cards",
            delete(ConversationCard).where(ConversationCard.user_id == user_id),
        ),
        (
            "family_questions",
            delete(FamilyQuestion).where(FamilyQuestion.user_id == user_id),
        ),
        ("media_objects", delete(MediaObject).where(MediaObject.user_id == user_id)),
        ("turn_failures", delete(TurnFailure).where(TurnFailure.user_id == user_id)),
    ]:
        try:
            result = await db.execute(stmt)
            await db.commit()
            report.counts[label] = result.rowcount
        except Exception:
            await db.rollback()
            logger.exception("Erasure: could not delete %s for %s", label, user_id)
            report.failures.append(f"table:{label}")

    # 3. Magic link tokens, keyed by email rather than user_id.
    try:
        email = (
            await db.execute(
                select(FamilyAccount.email).where(FamilyAccount.user_id == user_id)
            )
        ).scalar_one_or_none()
        if email is not None:
            result = await db.execute(
                delete(MagicLinkToken).where(MagicLinkToken.email == email)
            )
            await db.commit()
            report.counts["magic_link_tokens"] = result.rowcount
    except Exception:
        await db.rollback()
        logger.exception("Erasure: could not delete magic links for %s", user_id)
        report.failures.append("table:magic_link_tokens")

    # 4. Consent records are kept for audit, never hard-deleted. email_hash
    # stays; everything else that could identify the person is cleared,
    # including the number a parent record was given from (P3).
    try:
        result = await db.execute(
            update(ConsentRecord)
            .where(ConsentRecord.user_id == user_id)
            .values(
                user_id="DELETED",
                ip_address=None,
                user_agent=None,
                whatsapp_number=None,
            )
        )
        await db.commit()
        report.counts["consent_records_anonymised"] = result.rowcount
    except Exception:
        await db.rollback()
        logger.exception("Erasure: could not anonymise consent for %s", user_id)
        report.failures.append("table:consent_records")

    # 5. The family account itself.
    try:
        result = await db.execute(
            delete(FamilyAccount).where(FamilyAccount.user_id == user_id)
        )
        await db.commit()
        report.counts["family_accounts"] = result.rowcount
    except Exception:
        await db.rollback()
        logger.exception("Erasure: could not delete family account for %s", user_id)
        report.failures.append("table:family_accounts")

    # 6. The record that it happened.
    try:
        db.add(
            ErasureLog(
                user_id_hash=hash_user_id(user_id),
                actor=actor,
                counts=report.counts,
                failures=report.failures,
            )
        )
        await db.commit()
    except Exception:
        await db.rollback()
        logger.exception("Erasure: could not write the erasure log for %s", user_id)

    logger.info(
        "Erasure of %s by %s finished: %s%s",
        user_id,
        actor,
        report.counts,
        f" FAILURES {report.failures}" if report.failures else "",
    )
    return report
