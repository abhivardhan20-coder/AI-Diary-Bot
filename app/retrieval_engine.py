"""
Hybrid context retrieval engine for the AI Diary Companion.
"""

import logging
import json
import asyncio
from datetime import datetime, timedelta
from app.config import DEFAULT_TIMEZONE
from app.database import get_db
from app.semantic_engine import get_profile, profile_to_context_string
from app.utils import get_llm
from app.prompts import KEYWORD_EXTRACTION_PROMPT, RERANKING_PROMPT
logger = logging.getLogger(__name__)

import re

STOPWORDS = {
    "i", "me", "my", "myself", "we", "our", "ours", "ourselves", "you", "your", "yours", 
    "yourself", "yourselves", "he", "him", "his", "himself", "she", "her", "hers", "herself", 
    "it", "its", "itself", "they", "them", "their", "theirs", "themselves", "what", "which", 
    "who", "whom", "this", "that", "these", "those", "am", "is", "are", "was", "were", "be", 
    "been", "being", "have", "has", "had", "having", "do", "does", "did", "doing", "a", "an", 
    "the", "and", "but", "if", "or", "because", "as", "until", "while", "of", "at", "by", "for", 
    "with", "about", "against", "between", "into", "through", "during", "before", "after", 
    "above", "below", "to", "from", "up", "down", "in", "out", "on", "off", "over", "under", 
    "again", "further", "then", "once", "here", "there", "when", "where", "why", "how", "all", 
    "any", "both", "each", "few", "more", "most", "other", "some", "such", "no", "nor", "not", 
    "only", "own", "same", "so", "than", "too", "very", "s", "t", "can", "will", "just", "don", 
    "should", "now", "eva", "hey", "hello", "hi", "yes", "no", "yeah", "okay", "ok"
}

def deterministic_extract_keywords(text: str) -> list[str]:
    words = re.findall(r"\b[a-zA-Z]{3,}\b", text.lower())
    keywords = [w for w in words if w not in STOPWORDS]
    seen = set()
    unique_keywords = []
    for kw in keywords:
        if kw not in seen:
            seen.add(kw)
            unique_keywords.append(kw)
    return unique_keywords[:3]

from datetime import datetime, timezone

def deterministic_rerank(candidates: list[dict], current_message: str, query_vector: list[float] | None, keywords: list[str]) -> list[dict]:
    scored = []
    for c in candidates:
        vec_sim = 0.0
        if "embedding" in c and c["embedding"] and query_vector:
            try:
                emb = c["embedding"]
                if isinstance(emb, bytes):
                    emb = json.loads(emb.decode('utf-8'))
                elif isinstance(emb, str):
                    emb = json.loads(emb)
                dot_product = sum(a * b for a, b in zip(query_vector, emb))
                magnitude_q = sum(a * a for a in query_vector) ** 0.5
                magnitude_e = sum(a * a for a in emb) ** 0.5
                if magnitude_q * magnitude_e > 0:
                    vec_sim = dot_product / (magnitude_q * magnitude_e)
            except Exception:
                pass
        
        msg_lower = (c.get("user_message", "") + " " + c.get("bot_response", "")).lower()
        kw_overlap = sum(1 for kw in keywords if kw in msg_lower)
        
        # Temporal decay: half-life of 30 days
        try:
            ts = datetime.fromisoformat(c['timestamp'])
            if ts.tzinfo is not None:
                ts = ts.astimezone(timezone.utc).replace(tzinfo=None)
            now_naive = datetime.now(timezone.utc).replace(tzinfo=None)
            days_ago = max(0, (now_naive - ts).days)
        except Exception:
            days_ago = 30
        recency = 0.5 ** (days_ago / 30)
        
        score = vec_sim * 0.6 + (kw_overlap * 0.1) + (recency * 0.3)
        scored.append((score, c))
        
    scored.sort(key=lambda x: x[0], reverse=True)
    return [item[1] for item in scored[:3]]

async def get_vector_search_candidates(user_id: int, query_text: str, limit: int = 10, query_vector: list[float] | None = None) -> list[dict]:
    db = get_db()
    if query_vector is None:
        llm = get_llm()
        query_vector = await llm.embed_text(query_text)
    if not query_vector:
        return []
        
    if db._is_postgres:
        vec_str = f"[{','.join(map(str, query_vector))}]"
        results = await db.fetch("""
            SELECT id, user_id, user_message, bot_response, timestamp 
            FROM episodes 
            WHERE user_id = ? AND embedding IS NOT NULL
            ORDER BY embedding <=> ?::vector
            LIMIT ?
        """, user_id, vec_str, limit)
        return results

    episodes = await db.get_episodes_with_embeddings(user_id)
    if not episodes:
        return []
        
    scored_episodes = []
    for ep in episodes:
        try:
            emb_blob = ep.get("embedding")
            if not emb_blob:
                continue
            emb = json.loads(emb_blob.decode('utf-8'))
            
            dot_product = sum(a * b for a, b in zip(query_vector, emb))
            magnitude_q = sum(a * a for a in query_vector) ** 0.5
            magnitude_e = sum(a * a for a in emb) ** 0.5
            if magnitude_q * magnitude_e > 0:
                sim = dot_product / (magnitude_q * magnitude_e)
            else:
                sim = 0.0
                
            scored_episodes.append((sim, ep))
        except Exception as e:
            logger.error("Failed to compute cosine similarity for episode %s: %s", ep.get("id"), e)
            continue
            
    scored_episodes.sort(key=lambda x: x[0], reverse=True)
    return [ep for sim, ep in scored_episodes[:limit]]

def deduplicate_context(profile_str: str, memories_str: str) -> str:
    """Strip lines from retrieved_memories that overlap too much with terms in the profile context."""
    profile_terms = set(re.findall(r'\b[a-z]{4,}\b', profile_str.lower()))
    lines = []
    for line in memories_str.split('\n'):
        words = set(re.findall(r'\b[a-z]{4,}\b', line.lower()))
        overlap = words & profile_terms
        # Keep if the line has a low amount of shared terms (less than 5)
        if len(overlap) < 5:
            lines.append(line)
    return '\n'.join(lines)

async def build_context(user_id: int, current_message: str, user_info: dict | None = None, classification: str = "question") -> dict:
    db = get_db()
    llm = get_llm()
    
    # 1. Parallelize initial profile and history fetches
    tasks = [
        get_profile(user_id),
        db.get_recent_episodes(user_id, limit=4),
    ]
    
    user_info_idx = -1
    embed_idx = -1
    
    if user_info is None:
        user_info_idx = len(tasks)
        tasks.append(db.get_user(user_id))
        
    if classification != "casual":
        embed_idx = len(tasks)
        tasks.append(llm.embed_text(current_message))
        
    results = await asyncio.gather(*tasks)
    profile = results[0]
    recent = results[1]
    
    if user_info_idx != -1:
        user_info = results[user_info_idx]
        
    query_vector = None
    if embed_idx != -1:
        query_vector = results[embed_idx]
        
    # Process profile context
    full_profile_context = await profile_to_context_string(profile, user_id)
    
    # Process history (last 4 turns)
    history = []
    for ep in recent:
        history.append({"role": "user", "content": ep["user_message"]})
        history.append({"role": "assistant", "content": ep["bot_response"]})
        
    # Temporal Context
    tz_str = user_info.get("timezone", DEFAULT_TIMEZONE) if user_info else DEFAULT_TIMEZONE
    try:
        from zoneinfo import ZoneInfo
        tz = ZoneInfo(tz_str)
    except Exception:
        tz = None
        
    now = datetime.now(tz) if tz else datetime.now()
    now_str = now.strftime('%A, %B %d, %Y at %I:%M %p')
    temporal_lines = [f"Current time: {now_str}"]
    
    # Gap check since last conversation
    if recent:
        last_ep = recent[0]
        try:
            last_time = datetime.fromisoformat(last_ep["timestamp"])
            tzinfo = last_time.tzinfo
            now_for_sub = datetime.now(tzinfo) if tzinfo is not None else datetime.now()
            gap = now_for_sub - last_time
            days = gap.days
            hours = gap.seconds // 3600
            if days > 7:
                temporal_lines.append(f"Note: It has been {days} days since your last conversation. Acknowledge the gap naturally.")
            elif days > 1:
                temporal_lines.append(f"Note: You last spoke {days} days ago.")
            elif days == 1:
                temporal_lines.append("Note: You last spoke yesterday.")
            elif hours >= 4:
                temporal_lines.append(f"Note: You last spoke {hours} hours ago.")
        except Exception as e:
            logger.error("Failed to parse last timestamp: %s", e)
            
    temporal_context = "\n".join(temporal_lines)
    
    retrieved_memories = "No relevant past memories found."
    
    # Adaptive Retrieval: Skip vector search & keyword queries for casual small-talk
    if classification != "casual":
        # Extract keywords deterministically
        keywords = deterministic_extract_keywords(current_message)
        if not keywords:
            keywords = [current_message]
            
        all_search_tasks = []
        vector_task_count = 0
        if query_vector:
            all_search_tasks.append(get_vector_search_candidates(user_id, current_message, limit=10, query_vector=query_vector))
            all_search_tasks.append(db.get_vector_session_candidates(user_id, query_vector, limit=3))
            vector_task_count = 2
            
        for kw in keywords:
            all_search_tasks.append(db.search_episodes(user_id, kw, limit=10))
            all_search_tasks.append(db.search_sessions(user_id, kw, limit=3))
            
        if all_search_tasks:
            search_results = await asyncio.gather(*all_search_tasks)
            
            vector_episodes = search_results[0] if query_vector else []
            vector_sessions = search_results[1] if query_vector else []
            
            candidates = []
            seen_ids = set()
            recent_ids = {ep["id"] for ep in recent}
            
            for r in vector_episodes:
                if r["id"] not in seen_ids and r["id"] not in recent_ids:
                    seen_ids.add(r["id"])
                    candidates.append(r)
                    
            # Process keyword episodes
            for idx in range(vector_task_count, len(search_results), 2):
                ep_list = search_results[idx]
                for r in ep_list:
                    if r["id"] not in seen_ids and r["id"] not in recent_ids:
                        seen_ids.add(r["id"])
                        candidates.append(r)
                        if len(candidates) >= 15:
                            break
                if len(candidates) >= 15:
                    break
                    
            # Process sessions
            sessions_candidates = []
            seen_session_ids = set()
            for s in vector_sessions:
                if s["session_id"] not in seen_session_ids:
                    seen_session_ids.add(s["session_id"])
                    sessions_candidates.append(s)
                    
            for idx in range(vector_task_count + 1, len(search_results), 2):
                s_list = search_results[idx]
                for s in s_list:
                    if s["session_id"] not in seen_session_ids:
                        seen_session_ids.add(s["session_id"])
                        sessions_candidates.append(s)
                        
            retrieved_episodes_str = ""
            if candidates:
                # Deterministic Rerank instead of LLM Reranking
                top_episodes = deterministic_rerank(candidates, current_message, query_vector, keywords)
                if top_episodes:
                    memories_lines = []
                    for ep in top_episodes:
                        memories_lines.append(f"- Date: {ep['timestamp'][:10]}\n  User: {ep['user_message']}\n  You: {ep['bot_response']}")
                    retrieved_episodes_str = "\n".join(memories_lines)
                    
            retrieved_sessions_str = ""
            if sessions_candidates:
                session_lines = []
                for s in sessions_candidates[:3]:
                    memories_list = []
                    if s.get("memories"):
                        m_data = s["memories"]
                        if isinstance(m_data, list):
                            for m in m_data:
                                if isinstance(m, dict):
                                    memories_list.append(f"- {m.get('content')}")
                                else:
                                    memories_list.append(f"- {m}")
                    memories_str = "\n".join(memories_list) if memories_list else "None extracted"
                    session_lines.append(
                        f"📅 Session on {s.get('date')} (Title: {s.get('title') or 'Untitled'})\n"
                        f"  Summary: {s.get('summary') or 'None'}\n"
                        f"  Memories: {memories_str}"
                    )
                retrieved_sessions_str = "\n\n".join(session_lines)
                
            blocks = []
            if retrieved_episodes_str:
                blocks.append(f"--- Relevant Past Conversation Moments ---\n{retrieved_episodes_str}")
            if retrieved_sessions_str:
                blocks.append(f"--- Relevant Past Chat Sessions ---\n{retrieved_sessions_str}")
                
            if blocks:
                retrieved_memories = "\n\n".join(blocks)
                # Deduplicate retrieved memories against the user profile context
                retrieved_memories = deduplicate_context(full_profile_context, retrieved_memories)
                
    # Process pending topics
    pending_topics = []
    if user_info and user_info.get("pending_topics"):
        try:
            pending_topics = json.loads(user_info["pending_topics"])
        except Exception:
            pass
            
    return {
        "full_profile_context": full_profile_context,
        "temporal_context": temporal_context,
        "retrieved_memories": retrieved_memories,
        "history": history,
        "pending_topics": pending_topics,
        "profile": profile,
    }
