"""
Semantic profile manager for the AI Diary Companion.
Maintains a structured long-term profile of each user.
"""

import json
import logging
from copy import deepcopy

from app.database import get_db
from app.utils import get_llm
from app.prompts import PROFILE_EXTRACTION_PROMPT

logger = logging.getLogger(__name__)

DEFAULT_PROFILE: dict = {
    "name": None, "goals": [], "stressors": [], "preferences": [],
    "relationships": [], "recurring_emotions": [], "important_events": [],
    "habits": [], "routines": [], "personality_traits": [], "fears": [],
    "aspirations": [], "strengths": [], "interests": [], "favorite_topics": [],
}

async def get_profile(user_id: int) -> dict:
    db = get_db()
    profile = await db.get_semantic_profile(user_id)
    if profile is None: return deepcopy(DEFAULT_PROFILE)
    merged = deepcopy(DEFAULT_PROFILE)
    merged.update(profile)
    return merged

from datetime import datetime

async def update_profile_from_conversation(user_id: int, user_message: str, bot_response: str):
    db = get_db()
    current = await get_profile(user_id)
    
    # Retrieve pending topics
    user_info = await db.get_user(user_id)
    pending_topics = []
    if user_info and user_info.get("pending_topics"):
        try:
            pending_topics = json.loads(user_info["pending_topics"])
        except Exception:
            pass
            
    profile_for_prompt = deepcopy(current)
    profile_for_prompt["pending_topics"] = pending_topics
    
    prompt = PROFILE_EXTRACTION_PROMPT.format(
        user_message=user_message, bot_response=bot_response,
        current_profile=json.dumps(profile_for_prompt, indent=2)
    )
    updates = await get_llm().extract_profile(prompt)
    if not updates or updates.get("no_update"): return
    
    # Save pending topics if updated
    if "pending_topics" in updates and isinstance(updates["pending_topics"], list):
        await db.update_user(user_id, pending_topics=json.dumps(updates["pending_topics"]))
        
    # Write updates to memory_items
    for key, val in updates.items():
        if key in ("no_update", "pending_topics"): continue
        if val is None: continue
        
        if key == "name":
            await db.upsert_memory_item(user_id, "name", val)
        elif key in DEFAULT_PROFILE and isinstance(DEFAULT_PROFILE[key], list) and isinstance(val, list):
            for item in val:
                await db.upsert_memory_item(user_id, key, item)
                
    # Rebuild cached semantic profile from memory_items
    await db.rebuild_semantic_profile_cache(user_id)

def _merge_profile(current: dict, updates: dict) -> dict:
    merged = deepcopy(current)
    for k, v in updates.items():
        if k == "no_update" or k not in merged or v is None: continue
        if isinstance(merged[k], list) and isinstance(v, list):
            existing = {str(i).lower() for i in merged[k]}
            for item in v:
                if str(item).lower() not in existing: merged[k].append(item)
        else:
            merged[k] = v
    return merged

async def decay_memories(user_id: int):
    db = get_db()
    cursor = await db.db.execute("""
        SELECT id, category, importance 
        FROM memory_items 
        WHERE user_id = ? AND is_resolved = 0
    """, (user_id,))
    rows = await cursor.fetchall()
    
    for r in rows:
        mid = r["id"]
        cat = r["category"]
        imp = r["importance"]
        
        # High importance categories decay very slowly, minor ones decay faster
        if cat in ["goals", "relationships", "important_events", "personality_traits", "fears", "aspirations", "strengths"]:
            decay_rate = 0.98
        else:
            decay_rate = 0.90
            
        new_imp = imp * decay_rate
        if new_imp < 0.15:
            await db.db.execute("UPDATE memory_items SET is_resolved = 1, importance = ? WHERE id = ? AND user_id = ?", (new_imp, mid, user_id))
        else:
            await db.db.execute("UPDATE memory_items SET importance = ? WHERE id = ? AND user_id = ?", (new_imp, mid, user_id))
            
    await db.db.commit()
    await db.rebuild_semantic_profile_cache(user_id)
    logger.info("Decayed memory items for user %d", user_id)

async def curate_user_profile(user_id: int):
    db = get_db()
    llm = get_llm()
    
    # Always decay memories daily (called by daily summary job)
    await decay_memories(user_id)
    
    # Check last curation date for LLM-based resolved check (weekly)
    schedule = await db.get_schedule(user_id)
    last_curation = schedule.get("last_curation") if schedule else None
    now = datetime.now()
    if last_curation:
        last_dt = datetime.fromisoformat(last_curation)
        if (now - last_dt).days < 7:
            return  # Run weekly LLM curation only once a week
            
    cursor = await db.db.execute("SELECT id, category, content FROM memory_items WHERE user_id = ? AND is_resolved = 0", (user_id,))
    rows = await cursor.fetchall()
    if not rows: return
    
    memory_items_str = "\n".join([f"ID: {r['id']} | Category: {r['category']} | Content: {r['content']}" for r in rows])
    recent_weeklies = await db.get_recent_summaries(user_id, "weekly", limit=2)
    summaries_str = "\n\n".join([w["content"] for w in recent_weeklies]) if recent_weeklies else "No recent summaries."
    
    from app.prompts import CURATION_PROMPT
    prompt = CURATION_PROMPT.format(memory_items=memory_items_str, recent_summaries=summaries_str)
    res = await llm._call_json(prompt)
    if res and res.get("resolved_ids"):
        resolved_ids = res["resolved_ids"]
        for item_id in resolved_ids:
            await db.db.execute("UPDATE memory_items SET is_resolved = 1 WHERE id = ? AND user_id = ?", (item_id, user_id))
        await db.db.commit()
        await db.rebuild_semantic_profile_cache(user_id)
        logger.info("Weekly profile curation completed for user %d. Marked resolved IDs: %s", user_id, resolved_ids)
        
    await db.update_schedule(user_id, last_curation=now.isoformat())

async def profile_to_context_string(profile: dict, user_id: int | None = None) -> str:
    FIELD_LABELS = {
        "goals": "Active goals",
        "stressors": "Known stressors",
        "preferences": "Preferences",
        "relationships": "Key people",
        "recurring_emotions": "Emotional patterns",
        "important_events": "Life events",
        "habits": "Habits",
        "routines": "Routines",
        "personality_traits": "Personality",
        "fears": "Fears",
        "aspirations": "Long-term aspirations",
        "strengths": "Strengths",
        "interests": "Interests",
        "favorite_topics": "Favorite topics",
    }
    lines = ["=== WHO THIS PERSON IS ==="]
    if profile.get("name"):
        lines.append(f"Name: {profile['name']}")
        
    if user_id:
        try:
            db = get_db()
            user_info = await db.get_user(user_id)
            if user_info:
                if user_info.get("age"):
                    lines.append(f"Age: {user_info['age']}")
                if user_info.get("nationality"):
                    lines.append(f"Nationality: {user_info['nationality']}")
                if user_info.get("city"):
                    lines.append(f"City: {user_info['city']}")
        except Exception as e:
            logger.error("Failed to fetch onboarding context: %s", e)

    for key, label in FIELD_LABELS.items():
        val = profile.get(key)
        if val:
            if isinstance(val, list):
                items = [str(v) for v in val if v]
                if items:
                    lines.append(f"{label}: {', '.join(items[:5])}")
            elif val:
                lines.append(f"{label}: {val}")
    return "\n".join(lines) if len(lines) > 1 else ""
