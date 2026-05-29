"""
Stateless user reminder schedule checks.
Designed for serverless Vercel Cron execution.
"""

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from app.config import DEFAULT_TIMEZONE, DEFAULT_REMINDER_TIME
from app.database import get_db
from app.prompts import get_contextual_diary_prompt
from app.semantic_engine import get_profile
from app.utils import get_session_manager

logger = logging.getLogger(__name__)

async def schedule_user_reminder(app, user_id: int, time_str: str, tz: str = DEFAULT_TIMEZONE):
    # In serverless, we simply update the database settings.
    # The external cron trigger will fetch and process schedules.
    await get_db().update_user_settings(user_id, reminder_time=time_str, timezone=tz, reminder_enabled=1)

async def check_and_send_reminders(app):
    """
    Triggered periodically by Vercel Cron.
    Determines if it is time to check in with any user based on their timezone.
    """
    logger.info("Checking user reminder schedules...")
    db = get_db()
    users = await db.get_all_users_with_reminders()
    
    for u in users:
        user_id = u["user_id"]
        tz_str = u.get("timezone", DEFAULT_TIMEZONE)
        rem_time_str = u.get("reminder_time", DEFAULT_REMINDER_TIME)
        
        try:
            tz = ZoneInfo(tz_str)
        except Exception:
            tz = ZoneInfo(DEFAULT_TIMEZONE)
            
        now_local = datetime.now(tz)
        
        # Parse target reminder hour & minute
        try:
            h_rem, m_rem = map(int, rem_time_str.split(":"))
        except Exception:
            continue
            
        # Trigger reminder if current hour matches and we are within 30 minutes of the target time
        # This window ensures delivery even if the cron runs every 15-30 minutes.
        if now_local.hour == h_rem and abs(now_local.minute - m_rem) < 30:
            async with get_session_manager().lock_user(user_id):
                # Double-check last sent date in local timezone to prevent multiple triggers in the same window
                schedule = await db.get_schedule(user_id)
                last_sent_str = schedule.get("last_reminder_sent") if schedule else None
                if last_sent_str:
                    try:
                        last_sent = datetime.fromisoformat(last_sent_str)
                        last_sent_local = last_sent.astimezone(tz)
                        if last_sent_local.date() == now_local.date():
                            continue  # Already sent today
                    except Exception:
                        pass
                        
                # Send prompt
                logger.info("Sending scheduled check-in prompt to user %d", user_id)
                try:
                    p = await get_profile(user_id)
                    prompt = get_contextual_diary_prompt(goals=p.get("goals"), stressors=p.get("stressors"))
                    await app.bot.send_message(chat_id=user_id, text=prompt)
                    await db.update_schedule(user_id, last_reminder_sent=datetime.now().isoformat())
                except Exception as e:
                    logger.error("Failed to send scheduled prompt to %d: %s", user_id, e)
