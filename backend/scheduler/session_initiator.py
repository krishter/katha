from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from adapters.whatsapp_stub import get_whatsapp_adapter
from cards import deliver as card_delivery
from config import settings
from core import freemium, orchestrator, parent_consent, session_manager, usage
from media import tts
from media.audio_convert import convert_wav_to_ogg
from models.session import Session
from models.user_profile import UserProfileModel as UserProfile
from prompts.domains import get_domain

logger = logging.getLogger(__name__)

_IST = pytz.timezone("Asia/Kolkata")


async def initiate_sessions(db_session_factory) -> None:
    """
    Runs every minute (cron: * * * * *).

    The parent starts every conversation (SCHEDULED_OPENER_ENABLED=false,
    the default): Katha cannot message first — Meta rejects a free-form
    message outside the 24-hour window and throttled the first-contact
    template (63049) — so a session opens when her voice note arrives, on
    the webhook's on-demand path. With the opener off, only steps 0 and 4
    run: the stale sweep, and outputs for the sessions it abandoned.

    With it on (rollback), the old daily opener runs too:

    1. Query user_profiles WHERE scheduled_time matches current minute in IST.
    2. For each user:
       a. Skip if they already have an active session today.
       b. Create a new session.
       c. Synthesize opening voice note.
       d. Send voice note via WhatsApp.
       e. Record session_open_message_id.
    3. Also check for 30-minute no-response and send follow-up.
    """
    now_ist = datetime.now(timezone.utc).astimezone(_IST)
    current_hour = now_ist.hour
    current_minute = now_ist.minute

    async with db_session_factory() as db:
        # 0. A session left active with no reply must never block tomorrow's
        # conversation — sweep it to 'abandoned' before looking up who's due.
        # What it swept is processed at the end of this run, not here: those
        # sessions owe an entity-extraction pass and a memory card, and
        # doing that work before the due-user lookup would push a slow
        # extraction into the next cron minute.
        abandoned = await session_manager.abandon_stale_sessions(db)

        if settings.SCHEDULED_OPENER_ENABLED:
            await _open_scheduled_sessions(db, current_hour, current_minute)

        # 4. Outputs for the sessions swept at step 0. A conversation that
        # simply stopped is still a conversation the family is owed a card
        # for (PRD §7.3); before this the sweep was a dead end and those
        # sessions produced nothing at all. Wrapped because this runs every
        # minute and must never take the scheduler down.
        if abandoned:
            try:
                processed = await orchestrator.process_abandoned_sessions(abandoned, db)
                logger.info(
                    "Scheduler: produced outputs for %d of %d abandoned session(s)",
                    processed,
                    len(abandoned),
                )
            except Exception:
                logger.exception("Scheduler: failed processing abandoned sessions")


async def _open_scheduled_sessions(
    db: AsyncSession, current_hour: int, current_minute: int
) -> None:
    """
    The daily voice opener and its 30-minute nudge (steps 1-3). Runs only
    with SCHEDULED_OPENER_ENABLED=true, the rollback path; kept intact so a
    rollback restores something that works.
    """
    # 1. Find users scheduled for this minute
    result = await db.execute(
        select(UserProfile).where(
            UserProfile.scheduled_time
            == datetime(2000, 1, 1, current_hour, current_minute).time()
        )
    )
    due_profiles = result.scalars().all()
    logger.info(
        "Scheduler: %d user(s) scheduled at %02d:%02d IST",
        len(due_profiles),
        current_hour,
        current_minute,
    )

    whatsapp = get_whatsapp_adapter()

    for profile in due_profiles:
        # Every paid call for this parent's opener is attributed to her.
        with usage.usage_scope(user_id=profile.user_id):
            try:
                # Check for existing active session today
                active = await session_manager.get_active_session_by_number(
                    profile.whatsapp_number, db
                )
                if active is not None:
                    logger.info(
                        "Scheduler: user %s already has active session %s — skipping",
                        profile.user_id,
                        active.session_id,
                    )
                    continue

                # Consent gate. Nothing is scheduled for a parent who has
                # not agreed — that includes one who has never messaged,
                # one who has been asked and not answered clearly, and one
                # who declined. Katha cannot open the conversation anyway
                # (Meta 63049, see plan S3.0); she waits to be spoken to.
                if not await parent_consent.has_granted(profile.user_id, db):
                    logger.info(
                        "Scheduler: user %s has no recorded parent consent — "
                        "not initiating",
                        profile.user_id,
                    )
                    continue

                # Freemium gate — skip cleanly rather than let start_session
                # raise HTTPException(402) into the generic except below.
                if not await freemium.is_session_allowed(profile.user_id, db):
                    await freemium.send_upgrade_prompt(profile.user_id, db)
                    logger.info(
                        "Scheduler: skipped session initiation for %s — "
                        "session limit reached",
                        profile.user_id,
                    )
                    continue

                # Create new session — session_number and domain are chosen
                # by start_session based on the user's actual progress.
                state = await session_manager.start_session(profile.user_id, db)
                usage.enter_scope(session_id=state.session_id)

                # Build opening message from the session's actual domain
                domain_name = state.domain
                domain = get_domain(domain_name)
                opening_text = (
                    f"Namaste {profile.name} ji! "
                    f"I'm Katha, your daily companion. "
                    f"Today I'd love to hear about your life. "
                    f"{domain.entry_prompt}"
                )

                # Synthesize, then convert. Bulbul returns WAV, and this
                # used to send the raw WAV labelled audio/ogg — so a
                # rollback restored an opener WhatsApp could not play.
                wav_bytes = await tts.synthesize(
                    opening_text, profile, purpose="tts_opener"
                )
                audio_bytes = await convert_wav_to_ogg(wav_bytes)

                # Send voice note
                message_sid, audio_s3_key = await whatsapp.send_voice_note(
                    profile.whatsapp_number, audio_bytes, mime_type="audio/ogg"
                )

                # Record session_open_message_id and the audio key. The key
                # was previously discarded, leaving the object unreachable
                # by DELETE /user/{user_id} (S2.4a).
                await db.execute(
                    update(Session)
                    .where(Session.id == state.session_id)
                    .values(
                        session_open_message_id=message_sid,
                        session_open_audio_s3_key=audio_s3_key,
                        whatsapp_number=profile.whatsapp_number,
                    )
                )
                await db.commit()

                logger.info(
                    "Scheduler: session initiated for user %s, domain %s, sid %s",
                    profile.user_id,
                    domain_name,
                    message_sid,
                )

            except Exception:
                logger.exception(
                    "Scheduler: failed to initiate session for user %s",
                    profile.user_id,
                )

    # 3. 30-minute no-response follow-up
    await _send_followups(db, whatsapp)


async def _send_followups(db: AsyncSession, whatsapp) -> None:
    """
    Send the 30-minute no-reply nudge, at most once per session.

    `followup_sent_at` is what makes "at most once" true. This job runs every
    minute, and the other four predicates stay satisfied for as long as the
    session is open and unanswered — so without a recorded send the same
    session matched on every tick. On 2026-10-02..04 that produced 423
    messages to one number, roughly 210 a day, stopping only when the 4-hour
    stale sweep closed the session.

    The stamp is written whether or not the send succeeded, and that is
    deliberate. A nudge is a courtesy, not something to retry: the common
    failure is Meta rejecting a free-form message outside the 24-hour window
    (error 63016), which will still be true a minute later and for every
    minute after. Retrying it is precisely the loop this fixes.
    """
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

    result = await db.execute(
        select(Session, UserProfile)
        .join(UserProfile, Session.user_id == UserProfile.user_id)
        .where(Session.session_open_message_id.is_not(None))
        .where(Session.last_user_message_at.is_(None))
        .where(Session.started_at < cutoff)
        .where(Session.status == "active")
        .where(Session.followup_sent_at.is_(None))
    )
    rows = result.all()

    for session_row, profile in rows:
        with usage.usage_scope(user_id=profile.user_id, session_id=session_row.id):
            # Claim the session before sending. If the send raises, the stamp is
            # already committed and no retry storm can start.
            session_row.followup_sent_at = datetime.now(timezone.utc)
            db.add(session_row)
            await db.commit()

            try:
                followup_text = (
                    f"Hi {profile.name} ji, just checking in — no pressure at all. "
                    "Whenever you're ready, just send me a voice note and "
                    "we'll continue. "
                    "I'm here. \U0001f64f"
                )
                await whatsapp.send_text(profile.whatsapp_number, followup_text)
                logger.info(
                    "Scheduler: sent 30-min follow-up to user %s", profile.user_id
                )
            except Exception:
                logger.exception(
                    "Scheduler: failed to send follow-up for user %s (not retried — "
                    "the stamp is already set; see _send_followups docstring)",
                    profile.user_id,
                )


async def send_conversation_cards(db_session_factory) -> None:
    """
    Every minute: send approved conversation cards whose time has come
    (docs/specs/CONVERSATION_CARDS.md §8). Does nothing unless
    CONVERSATION_CARDS_ENABLED. Never raises: a failure here must not take
    the scheduler down.
    """
    if not settings.CONVERSATION_CARDS_ENABLED:
        return
    try:
        async with db_session_factory() as db:
            sent = await card_delivery.send_due_cards(db, get_whatsapp_adapter())
        if sent:
            logger.info("Scheduler: sent %d conversation card(s)", sent)
    except Exception:
        logger.exception("Scheduler: sending conversation cards failed")


def create_scheduler(db_session_factory) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance."""
    scheduler = AsyncIOScheduler()
    scheduler.add_job(
        send_conversation_cards,
        trigger="cron",
        minute="*",
        kwargs={"db_session_factory": db_session_factory},
        id="send_conversation_cards",
        replace_existing=True,
        misfire_grace_time=30,
    )
    scheduler.add_job(
        initiate_sessions,
        trigger="cron",
        minute="*",
        kwargs={"db_session_factory": db_session_factory},
        id="initiate_sessions",
        replace_existing=True,
        misfire_grace_time=30,
    )
    return scheduler
