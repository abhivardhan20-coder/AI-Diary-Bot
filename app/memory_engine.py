"""
Layered memory engine for the AI Diary Companion.
Bridges database operations with higher-level analysis.
"""

import logging
from collections import Counter
from app.database import get_db
from app.utils import get_llm
from app.prompts import EMOTION_ANALYSIS_PROMPT

logger = logging.getLogger(__name__)

# Valid emotion labels
VALID_EMOTIONS = {
    "happy", "sad", "anxious", "angry", "excited", "stressed",
    "grateful", "neutral", "proud", "lonely", "hopeful", "frustrated",
    "calm", "overwhelmed", "nostalgic", "confused", "motivated", "tired",
}

async def save_episode(user_id: int, user_message: str, bot_response: str, **kwargs) -> int:
    db = get_db()
    return await db.save_episode(user_id, user_message, bot_response, **kwargs)

async def get_recent_episodes(user_id: int, limit: int = 5) -> list[dict]:
    return await get_db().get_recent_episodes(user_id, limit)

async def search_episodes(user_id: int, query: str, limit: int = 10) -> list[dict]:
    return await get_db().search_episodes(user_id, query, limit)

async def analyze_emotion(user_message: str, bot_response: str) -> dict:
    prompt = EMOTION_ANALYSIS_PROMPT.format(user_message=user_message, bot_response=bot_response)
    llm = get_llm()
    result = await llm.analyze_emotion(prompt)
    
    if not result:
        return {"emotion": "neutral", "confidence": 0.5, "topics": []}

    emotion = result.get("emotion", "neutral").lower().strip()
    if emotion not in VALID_EMOTIONS: emotion = "neutral"
    
    return {
        "emotion": emotion,
        "confidence": result.get("confidence", 0.5),
        "secondary_emotion": result.get("secondary_emotion"),
        "topics": result.get("topics", [])
    }

async def get_emotional_trends(user_id: int, days: int = 30) -> dict:
    db = get_db()
    counts = await db.get_emotion_counts(user_id, days)
    if not counts:
        return {"total_entries": 0, "trend_summary": "No emotional data yet."}
    
    total = sum(e["count"] for e in counts)
    summary = f"Recent trends: " + ", ".join([f"{e['detected_emotion']} ({round(e['count']/total*100)}%)" for e in counts[:3]])
    return {"total_entries": total, "trend_summary": summary}

async def get_memory_summary(user_id: int) -> str:
    db = get_db()
    count = await db.get_episode_count(user_id)
    if count == 0: return "No memories yet."
    
    oldest = await db.get_oldest_episode(user_id)
    oldest_date = oldest["timestamp"][:10] if oldest else "unknown"
    
    schedule = await db.get_schedule(user_id)
    streak = schedule.get("streak_count", 0) if schedule else 0
    
    lines = [
        f"🧠 Total memories: {count}",
        f"📅 Started on: {oldest_date}",
        f"🔥 Streak: {streak} days"
    ]
    return "\n".join(lines)


async def compact_session(user_id: int, session_id: str):
    import json
    db = get_db()
    llm = get_llm()
    
    # 1. Fetch episodes
    episodes = await db.get_episodes_for_session(user_id, session_id)
    if not episodes:
        logger.info("No episodes to compact for session %s", session_id)
        return
        
    # 2. Format episodes
    formatted_turns = []
    for ep in episodes:
        formatted_turns.append(f"User: {ep['user_message']}")
        formatted_turns.append(f"Eva: {ep['bot_response']}")
    episodes_text = "\n".join(formatted_turns)
    
    # 3. Call LLM
    from app.prompts import SESSION_COMPACTION_PROMPT
    prompt = SESSION_COMPACTION_PROMPT.format(episodes=episodes_text)
    result = await llm._call_json(prompt, max_tokens=1000, temperature=0.3)
    if not result:
        logger.warning("Failed to generate session compaction for session %s", session_id)
        return
        
    # 4. Save results to database
    title = result.get("title")
    summary = result.get("summary")
    emotion_metadata = result.get("emotion_metadata", {})
    important_memories = result.get("important_memories", [])
    importance_score = result.get("importance_score", 0.3)
    
    # Get vector embedding of summary
    embedding = None
    if summary:
        embedding = await llm.embed_text(summary)
        
    await db.update_session(
        user_id=user_id,
        session_id=session_id,
        title=title,
        summary=summary,
        emotion_metadata=emotion_metadata,
        memories=important_memories,
        importance_score=importance_score,
        embedding=embedding
    )
    
    # 5. Extract and save new facts into long-term memory_items table
    for mem in important_memories:
        category = mem.get("category")
        content = mem.get("content")
        importance = mem.get("importance", 0.5)
        if category and content:
            await db.upsert_memory_item(user_id, category, content, importance)
            
    # Rebuild semantic profile cache since memories have changed
    await db.rebuild_semantic_profile_cache(user_id)
    logger.info("Compacted session %s for user %d", session_id, user_id)


async def compact_uncompacted_sessions():
    db = get_db()
    uncompacted = await db.get_uncompacted_sessions()
    logger.info("Found %d uncompacted sessions", len(uncompacted))
    for sess in uncompacted:
        try:
            user_id = sess["user_id"]
            session_id = sess["session_id"]
            await compact_session(user_id, session_id)
        except Exception as e:
            logger.error("Failed to compact session %s: %s", sess.get("session_id"), e)

