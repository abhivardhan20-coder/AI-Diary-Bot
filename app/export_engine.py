import os
import re
import json
import zipfile
import logging
import secrets
from datetime import datetime
from app.database import get_db
from app.config import EXPORT_DIR

logger = logging.getLogger(__name__)

def parse_export_arguments(args: list[str]) -> dict:
    """
    Parse the arguments provided to the /export command.
    Example usages:
    - /export -> full json export
    - /export format=md -> full markdown export
    - /export diary -> only diary entries
    - /export session_uuid -> specific session
    - /export 2026-05-20 to 2026-05-25 format=txt -> text export in date range
    """
    options = {
        "categories": set(),  # 'chats', 'diary', 'memory', 'summaries', 'emotions', 'profile', 'settings'
        "format": "json",     # 'json', 'md', 'txt'
        "session_id": None,
        "start_date": None,
        "end_date": None,
        "zip": False
    }
    
    arg_str = " ".join(args).strip().lower()
    if not arg_str:
        return options

    # 1. Parse format option (e.g. format=json, format=md, format=txt, format=zip)
    format_match = re.search(r"\bformat=(json|md|markdown|txt|zip)\b", arg_str)
    if format_match:
        fmt = format_match.group(1)
        if fmt == "markdown":
            options["format"] = "md"
        elif fmt == "zip":
            options["zip"] = True
        else:
            options["format"] = fmt
        arg_str = arg_str.replace(format_match.group(0), "").strip()

    # 2. Check for explicit zip keyword
    if "zip" in arg_str.split():
        options["zip"] = True
        arg_str = arg_str.replace("zip", "").strip()

    # 3. Parse date range (YYYY-MM-DD to YYYY-MM-DD)
    date_pattern = r"\b\d{4}-\d{2}-\d{2}\b"
    dates = re.findall(date_pattern, arg_str)
    if len(dates) >= 2:
        options["start_date"] = dates[0]
        options["end_date"] = dates[1]
        for d in dates[:2]:
            arg_str = arg_str.replace(d, "")
        arg_str = arg_str.replace("to", "").strip()
    elif len(dates) == 1:
        options["start_date"] = dates[0]
        options["end_date"] = dates[0]
        arg_str = arg_str.replace(dates[0], "").strip()

    # 4. Tokenize remaining arguments to detect categories, aliases, or session IDs
    tokens = [t.strip() for t in arg_str.split() if t.strip()]
    
    valid_categories = {"chats", "diary", "memory", "summaries", "emotions", "profile", "settings"}
    
    for token in tokens:
        # Treat 'sessions' as 'chats' alias
        if token == "sessions":
            options["categories"].add("chats")
        elif token.startswith("session_") or token.startswith("sess_") or re.match(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$", token):
            options["session_id"] = token
        elif token in valid_categories:
            options["categories"].add(token)

    return options

async def generate_export(user_id: int, options: dict) -> tuple[str, str]:
    """
    Compile user records, format them, and write them to a temporary file.
    Returns a tuple of (file_path, file_name).
    """
    db = get_db()
    data = {
        "export_metadata": {
            "version": "1.0",
            "timestamp": datetime.now().isoformat(),
            "user_id": user_id,
            "scope": "full" if not options["categories"] and not options["session_id"] and not options["start_date"] else "filtered"
        }
    }

    # If no categories are explicitly specified and no session ID is requested, fetch everything
    export_all = not options["categories"] and not options["session_id"]

    # 1. Fetch Profile Data
    if export_all or "profile" in options["categories"]:
        user_info = await db.get_user(user_id)
        if user_info:
            data["profile"] = {
                "name": user_info.get("first_name"),
                "username": user_info.get("username"),
                "timezone": user_info.get("timezone"),
                "relationship_stage": user_info.get("relationship_stage"),
                "onboarding_status": user_info.get("onboarding_status"),
                "age": user_info.get("age"),
                "nationality": user_info.get("nationality"),
                "city": user_info.get("city")
            }

    # 2. Fetch Reminder & Streak Settings
    if export_all or "settings" in options["categories"]:
        user_info = await db.get_user(user_id)
        schedule = await db.get_schedule(user_id) or {}
        data["settings"] = {
            "reminder_time": user_info.get("reminder_time") if user_info else None,
            "reminder_enabled": user_info.get("reminder_enabled") if user_info else None,
            "streak_count": schedule.get("streak_count", 0),
            "longest_streak": schedule.get("longest_streak", 0),
            "last_diary_date": schedule.get("last_diary_date")
        }

    # 3. Fetch Chats & Sessions
    if options["session_id"]:
        # Verify session ownership & fetch
        session = await db.get_session(user_id, options["session_id"])
        if session:
            MAX_EXPORT_EPISODES = 5000
            episodes = await db.get_episodes_for_session(user_id, options["session_id"], limit=MAX_EXPORT_EPISODES)
            messages = []
            for ep in episodes:
                messages.append({
                    "role": "user",
                    "timestamp": ep.get("timestamp"),
                    "content": ep.get("user_message")
                })
                messages.append({
                    "role": "assistant",
                    "timestamp": ep.get("timestamp"),
                    "content": ep.get("bot_response")
                })
            session_copy = dict(session)
            session_copy["messages"] = messages
            session_copy.pop("embedding", None)
            data["sessions"] = [session_copy]
        else:
            data["sessions"] = []
    elif export_all or "chats" in options["categories"]:
        all_sessions = await db.get_all_sessions(user_id)
        if options["start_date"] and options["end_date"]:
            all_sessions = [s for s in all_sessions if options["start_date"] <= s.get("date") <= options["end_date"]]
            
        # N+1 optimization: batch fetch all user episodes in a single query
        all_episodes = await db.get_all_episodes(user_id)
        episodes_by_session = {}
        for ep in all_episodes:
            sid = ep.get("session_id")
            if sid:
                episodes_by_session.setdefault(sid, []).append(ep)
                
        sessions_list = []
        for s in all_sessions:
            episodes = episodes_by_session.get(s["session_id"], [])
            messages = []
            for ep in episodes:
                messages.append({
                    "role": "user",
                    "timestamp": ep.get("timestamp"),
                    "content": ep.get("user_message")
                })
                messages.append({
                    "role": "assistant",
                    "timestamp": ep.get("timestamp"),
                    "content": ep.get("bot_response")
                })
            s_copy = dict(s)
            s_copy["messages"] = messages
            s_copy.pop("embedding", None)
            sessions_list.append(s_copy)
        data["sessions"] = sessions_list

    # 4. Fetch Diary Entries
    if export_all or "diary" in options["categories"]:
        diary_entries = await db.get_all_diary_entries(user_id)
        if options["start_date"] and options["end_date"]:
            diary_entries = [d for d in diary_entries if options["start_date"] <= d.get("created_at")[:10] <= options["end_date"]]
            
        cleaned_diary = []
        for entry in diary_entries:
            d = dict(entry)
            d.pop("embedding", None)
            cleaned_diary.append(d)
        data["diary"] = cleaned_diary

    # 5. Fetch Memory Items
    if export_all or "memory" in options["categories"]:
        memories = await db.get_all_memories(user_id)
        cleaned_memories = []
        for m in memories:
            cleaned_memories.append({
                "id": m.get("id"),
                "category": m.get("category"),
                "content": m.get("content"),
                "first_seen": m.get("first_seen"),
                "last_seen": m.get("last_seen"),
                "mention_count": m.get("mention_count"),
                "is_resolved": m.get("is_resolved"),
                "importance": m.get("importance")
            })
        data["memories"] = cleaned_memories

    # 6. Fetch Summaries
    if export_all or "summaries" in options["categories"]:
        summaries = await db.get_all_summaries(user_id)
        if options["start_date"] and options["end_date"]:
            summaries = [s for s in summaries if options["start_date"] <= s.get("period_start")[:10] <= options["end_date"]]
            
        cleaned_summaries = []
        for s in summaries:
            s_copy = dict(s)
            for f in ("emotional_trends", "key_events"):
                if s_copy.get(f):
                    try:
                        s_copy[f] = json.loads(s_copy[f])
                    except Exception:
                        pass
            cleaned_summaries.append(s_copy)
        data["summaries"] = cleaned_summaries

    # 7. Fetch Emotions
    if "emotions" in options["categories"]:
        from app.memory_engine import get_emotional_trends
        trends = await get_emotional_trends(user_id, days=180)
        
        episodes = await db.get_all_episodes(user_id)
        emotions_history = []
        for ep in episodes:
            if ep.get("detected_emotion"):
                emotions_history.append({
                    "timestamp": ep.get("timestamp"),
                    "emotion": ep.get("detected_emotion"),
                    "confidence": ep.get("emotion_confidence"),
                    "secondary": ep.get("secondary_emotion")
                })
        data["emotional_history"] = {
            "trends": trends,
            "history": emotions_history
        }

    # Ensure output exports directory exists
    os.makedirs(str(EXPORT_DIR), exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    opaque_token = secrets.token_hex(16)
    friendly_name = f"eva_export_{ts}.{options['format']}"
    
    raw_opaque_name = f"{opaque_token}.{options['format']}"
    raw_path = os.path.join(str(EXPORT_DIR), raw_opaque_name)
    
    # Render file content based on selected format
    if options["format"] == "json":
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
    elif options["format"] == "txt":
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(f"==================================================\n")
            f.write(f"         AI DIARY COMPANION ARCHIVE EXPORT        \n")
            f.write(f"==================================================\n")
            f.write(f"User ID: {user_id}\n")
            f.write(f"Export Time: {data['export_metadata']['timestamp']}\n\n")
            
            if "profile" in data:
                f.write(f"--- PROFILE INFO ---\n")
                for k, v in data["profile"].items():
                    f.write(f"{k.capitalize()}: {v}\n")
                f.write("\n")
                
            if "sessions" in data:
                f.write(f"--- CHATS & SESSIONS ---\n")
                for s in data["sessions"]:
                    f.write(f"Session: {s.get('title') or 'Untitled'} (ID: {s.get('session_id')})\n")
                    f.write(f"Date: {s.get('date')} | Summary: {s.get('summary') or 'None'}\n")
                    f.write(f"--------------------------------------------------\n")
                    for m in s.get("messages", []):
                        role = "You" if m["role"] == "user" else "Eva"
                        f.write(f"[{m.get('timestamp')}] {role}: {m.get('content')}\n")
                    f.write("\n")
                    
            if "diary" in data:
                f.write(f"--- DIARY ENTRIES ---\n")
                for d in data["diary"]:
                    f.write(f"[{d.get('created_at')}] Title: {d.get('title') or 'Untitled'}\n")
                    f.write(f"Mood: {d.get('detected_emotions')} (Confidence: {d.get('emotion_confidence')})\n")
                    f.write(f"Content: {d.get('raw_text')}\n")
                    f.write(f"AI Summary: {d.get('ai_summary')}\n")
                    f.write(f"--------------------------------------------------\n\n")
                    
            if "memories" in data:
                f.write(f"--- EXTRACTED MEMORIES ---\n")
                for m in data["memories"]:
                    f.write(f"- [{m.get('category')}] {m.get('content')} (Importance: {m.get('importance')})\n")
                f.write("\n")

    elif options["format"] == "md":
        with open(raw_path, "w", encoding="utf-8") as f:
            f.write(f"# AI Companion History Archive\n\n")
            f.write(f"* **User ID**: `{user_id}`\n")
            f.write(f"* **Exported At**: `{data['export_metadata']['timestamp']}`\n\n")
            
            if "profile" in data:
                f.write(f"## Profile Information\n\n")
                for k, v in data["profile"].items():
                    f.write(f"* **{k.capitalize()}**: {v}\n")
                f.write("\n")
                
            if "sessions" in data:
                f.write(f"## Chat History & Sessions\n\n")
                for s in data["sessions"]:
                    f.write(f"### {s.get('title') or 'Untitled'} (Session `{s.get('session_id')}`)\n")
                    f.write(f"* **Date**: {s.get('date')}\n")
                    f.write(f"* **Summary**: {s.get('summary') or 'None'}\n\n")
                    for m in s.get("messages", []):
                        role = "You" if m["role"] == "user" else "Eva"
                        f.write(f"* **{role}** ({m.get('timestamp')[:19].replace('T', ' ')}): {m.get('content')}\n")
                    f.write("\n")
                    
            if "diary" in data:
                f.write(f"## Diary Entries\n\n")
                for d in data["diary"]:
                    f.write(f"### {d.get('title') or 'Untitled'} ({d.get('created_at')[:10]})\n")
                    f.write(f"* **Mood**: {d.get('detected_emotions')} (Confidence: {d.get('emotion_confidence')})\n")
                    f.write(f"* **Entry**: {d.get('raw_text')}\n")
                    f.write(f"* **Eva's Summary**: *{d.get('ai_summary')}*\n\n")
                    
            if "memories" in data:
                f.write(f"## Extracted Memories\n\n")
                for m in data["memories"]:
                    f.write(f"* **{m.get('category').capitalize()}**: {m.get('content')} *(Importance: {m.get('importance')} | Mentions: {m.get('mention_count')} holds)*\n")
                f.write("\n")

    # If zipped compression is requested OR file size exceeds 1 MB (1048576 bytes)
    file_size = os.path.getsize(raw_path)
    if options["zip"] or file_size > 1048576:
        zip_opaque_name = f"{secrets.token_hex(16)}.zip"
        zip_path = os.path.join(str(EXPORT_DIR), zip_opaque_name)
        zip_friendly_name = f"eva_export_{ts}.zip"
        
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(raw_path, arcname=friendly_name)
        
        # Clean up the raw unzipped temporary file
        try:
            os.remove(raw_path)
        except OSError:
            pass
            
        return zip_path, zip_friendly_name

    return raw_path, friendly_name
