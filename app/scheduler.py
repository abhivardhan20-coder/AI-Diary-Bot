"""
User reminder schedule management via Upstash QStash.
"""

import logging
import json
import httpx
import os
from datetime import datetime

from app.config import DEFAULT_TIMEZONE, DEFAULT_REMINDER_TIME, WEBHOOK_URL
from app.database import get_db
from app.prompts import get_contextual_diary_prompt
from app.semantic_engine import get_profile
from app.utils import get_session_manager

QSTASH_TOKEN = os.getenv("QSTASH_TOKEN", "")

logger = logging.getLogger(__name__)

async def schedule_user_reminder(app, user_id: int, time_str: str, tz_str: str = DEFAULT_TIMEZONE):
    """
    Schedules a delayed job via Upstash QStash using the exact time.
    """
    db = get_db()
    await db.update_user_settings(user_id, reminder_time=time_str, timezone=tz_str, reminder_enabled=1)
    
    if not QSTASH_TOKEN or not WEBHOOK_URL:
        logger.warning("QSTASH_TOKEN or WEBHOOK_URL not set. Skipping QStash scheduling.")
        return

    # Delete existing schedule if any
    schedule_data = await db.get_schedule(user_id)
    if schedule_data and schedule_data.get("next_reminder"):
        old_schedule_id = schedule_data["next_reminder"]
        async with httpx.AsyncClient() as client:
            try:
                await client.delete(
                    f"https://qstash.upstash.io/v2/schedules/{old_schedule_id}",
                    headers={"Authorization": f"Bearer {QSTASH_TOKEN}"}
                )
            except Exception as e:
                logger.warning("Could not delete old QStash schedule %s: %s", old_schedule_id, e)

    # Parse target reminder hour & minute
    try:
        h_rem, m_rem = map(int, time_str.split(":"))
    except Exception:
        return
        
    cron_expr = f"{m_rem} {h_rem} * * *"
    
    # Create new QStash schedule
    async with httpx.AsyncClient() as client:
        try:
            res = await client.post(
                f"https://qstash.upstash.io/v2/schedules/{WEBHOOK_URL}/qstash-reminder",
                headers={
                    "Authorization": f"Bearer {QSTASH_TOKEN}",
                    "Upstash-Cron": cron_expr,
                    "Upstash-Cron-Timezone": tz_str,
                    "Content-Type": "application/json"
                },
                json={"user_id": user_id}
            )
            res.raise_for_status()
            data = res.json()
            new_schedule_id = data.get("scheduleId")
            if new_schedule_id:
                await db.update_schedule(user_id, next_reminder=new_schedule_id)
                logger.info("Successfully scheduled QStash reminder %s for user %d at %s %s", new_schedule_id, user_id, time_str, tz_str)
        except Exception as e:
            logger.error("Failed to schedule QStash reminder for user %d: %s", user_id, e)

async def send_reminder_now(app, user_id: int):
    """
    Triggered by the QStash webhook at the exact delayed time.
    """
    logger.info("Executing precise delayed reminder for user %d", user_id)
    db = get_db()
    
    async with get_session_manager().lock_user(user_id):
        try:
            p = await get_profile(user_id)
            prompt = get_contextual_diary_prompt(goals=p.get("goals"), stressors=p.get("stressors"))
            await app.bot.send_message(chat_id=user_id, text=prompt)
            await db.update_schedule(user_id, last_reminder_sent=datetime.now().isoformat())
        except Exception as e:
            logger.error("Failed to send scheduled prompt to %d: %s", user_id, e)
