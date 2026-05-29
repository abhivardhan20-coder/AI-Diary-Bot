"""
Diary engine for the AI Diary Companion.
"""

import logging
from app.database import get_db
from app.utils import get_llm
from app.semantic_engine import get_profile, update_profile_from_conversation, profile_to_context_string
from app.prompts import DIARY_ANALYSIS_PROMPT, DIARY_FOLLOWUP_PROMPT

logger = logging.getLogger(__name__)

async def process_diary_entry(user_id: int, raw_text: str) -> dict:
    db = get_db()
    entry_id = await db.save_diary_entry(user_id=user_id, raw_text=raw_text)
    
    # Analysis
    profile = await get_profile(user_id)
    p_str = await profile_to_context_string(profile, user_id)
    prompt = DIARY_ANALYSIS_PROMPT.format(diary_text=raw_text, profile=p_str or "(new user)")
    analysis = await get_llm().analyze_emotion(prompt)
    
    if analysis:
        await db.update_diary_entry(user_id, entry_id, **analysis)
    else:
        analysis = {}

    # Followup
    follow_prompt = DIARY_FOLLOWUP_PROMPT.format(
        diary_text=raw_text, emotions=analysis.get("detected_emotions", "neutral"),
        topics=", ".join(analysis.get("extracted_topics", [])),
        stressors=", ".join(analysis.get("extracted_stressors", [])),
        goals=", ".join(analysis.get("extracted_goals", [])),
        profile=p_str or "(new user)"
    )
    followup = await get_llm().chat("You are a warm AI diary companion.", follow_prompt, max_tokens=300)
    
    if followup:
        await db.update_diary_entry(user_id, entry_id, ai_followup=followup)
        await update_profile_from_conversation(user_id, raw_text, followup)

    return {"entry_id": entry_id, "analysis": analysis, "followup": followup}

async def get_diary_summary(user_id: int) -> str:
    db = get_db()
    count = await db.get_diary_entry_count(user_id)
    if count == 0: return "No diary entries yet."
    latest = await db.get_latest_diary_entry(user_id)
    return f"📓 Total entries: {count}\n📅 Latest: {latest['created_at'][:10]}"

async def format_diary_entry_display(entry: dict) -> str:
    return f"📓 {entry.get('title', 'Untitled')}\n📅 {entry['created_at'][:10]}\n\n{entry['raw_text'][:500]}"
