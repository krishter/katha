from __future__ import annotations

import logging
from datetime import datetime, timezone

import pytz
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from adapters import sarvam_tts
from adapters.whatsapp_stub import get_whatsapp_adapter
from core import session_manager
from models.session import Session
from models.user_profile import UserProfileModel as UserProfile
from prompts.domains import get_domain_sequence

logger = logging.getLogger(__name__)

_IST = pytz.timezone("Asia/Kolkata")


async def initiate_sessions(db_session_factory) -> None:
    """
    Runs every minute (cron: * * * * *).

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
        domain_sequence = get_domain_sequence()

        for profile in due_profiles:
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

                # Create new session
                state = await session_manager.start_session(profile.user_id, db)

                # Build opening message
                domain_name = domain_sequence[0]  # Default to childhood for session 1
                from prompts.domains import get_domain

                domain = get_domain(domain_name)
                opening_text = (
                    f"Namaste {profile.name} ji! "
                    f"I'm Katha, your daily companion. "
                    f"Today I'd love to hear about your life. "
                    f"{domain.entry_prompt}"
                )

                # Synthesize
                audio_bytes = await sarvam_tts.synthesize(
                    opening_text, language_code=profile.preferred_language
                )

                # Send voice note
                message_sid = await whatsapp.send_voice_note(
                    profile.whatsapp_number, audio_bytes, mime_type="audio/ogg"
                )

                # Record session_open_message_id
                await db.execute(
                    update(Session)
                    .where(Session.id == state.session_id)
                    .values(
                        session_open_message_id=message_sid,
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
                    "Scheduler: failed to initiate session for user %s", profile.user_id
                )

        # 3. 30-minute no-response follow-up
        await _send_followups(db, whatsapp)


async def _send_followups(db: AsyncSession, whatsapp) -> None:
    """Send follow-up text to users who haven't responded in 30 minutes."""
    from datetime import timedelta

    cutoff = datetime.now(timezone.utc) - timedelta(minutes=30)

    result = await db.execute(
        select(Session, UserProfile)
        .join(UserProfile, Session.user_id == UserProfile.user_id)
        .where(Session.session_open_message_id.is_not(None))
        .where(Session.last_user_message_at.is_(None))
        .where(Session.started_at < cutoff)
        .where(Session.session_end_suggested.is_(False))
        .where(Session.goal_met.is_(False))
    )
    rows = result.all()

    for session_row, profile in rows:
        try:
            followup_text = (
                f"Hi {profile.name} ji, just checking in — no pressure at all. "
                "Whenever you're ready, just send me a voice note and we'll continue. "
                "I'm here. \U0001f64f"
            )
            await whatsapp.send_text(profile.whatsapp_number, followup_text)
            logger.info(
                "Scheduler: sent 30-min follow-up to user %s", profile.user_id
            )
        except Exception:
            logger.exception(
                "Scheduler: failed to send follow-up for user %s", profile.user_id
            )


def create_scheduler(db_session_factory) -> AsyncIOScheduler:
    """Create and configure the APScheduler instance."""
    scheduler = AsyncIOScheduler()
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
