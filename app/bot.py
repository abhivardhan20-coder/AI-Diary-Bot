"""
Telegram Bot core logic and handler registration.
Modified for production webhook usage.
"""

import logging
import asyncio
import json
import random
import re
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, ApplicationBuilder, CommandHandler, 
    MessageHandler, filters, ContextTypes, CallbackQueryHandler
)
from telegram.constants import ChatAction

from app.config import TELEGRAM_BOT_TOKEN, DEFAULT_TIMEZONE, DEFAULT_REMINDER_TIME
from app.database import get_db
from app.memory_engine import get_memory_summary, save_episode, analyze_emotion
from app.retrieval_engine import build_context
from app.semantic_engine import get_profile, update_profile_from_conversation
from app.diary_engine import process_diary_entry, get_diary_summary
from app.scheduler import schedule_user_reminder
from app.prompts import SYSTEM_PROMPT_TEMPLATE, DIARY_ENTRY_INTRO
from app.utils import get_llm, get_session_manager

logger = logging.getLogger(__name__)

async def determine_relationship_stage(user_id: int) -> str:
    db = get_db()
    user_info = await db.get_user(user_id)
    if not user_info:
        return "new"
    
    created_at_str = user_info.get("created_at")
    if not created_at_str:
        return "new"
        
    try:
        created_at = datetime.fromisoformat(created_at_str)
        days_active = (datetime.now() - created_at).days
    except Exception:
        days_active = 0
        
    episode_count = await db.get_episode_count(user_id)
    
    if episode_count >= 150 and days_active >= 30:
        stage = "close"
    elif episode_count >= 50 and days_active >= 10:
        stage = "established"
    elif episode_count >= 15 and days_active >= 3:
        stage = "warming"
    else:
        stage = "new"
        
    current_stage = user_info.get("relationship_stage", "new")
    if current_stage != stage:
        await db.update_user(user_id, relationship_stage=stage)
        
    return stage

def parse_time_and_tz(text: str, default_tz: str = "Asia/Kolkata") -> tuple[str, str] | None:
    text = text.strip().upper()
    parts = text.split()
    if not parts:
        return None
        
    tz_part = default_tz
    last_part = parts[-1]
    
    if last_part not in ["AM", "PM"] and (re.match(r"^[A-Z_]+/[A-Z_]+$", last_part) or last_part in ["UTC", "GMT", "EST", "PST", "CST", "MST", "CET", "IST"]):
        tz_part = last_part
        if "/" in tz_part:
            tz_part = "/".join(p.capitalize() for p in tz_part.split("/"))
        parts = parts[:-1]
        
    time_part = " ".join(parts)
    
    match_24 = re.match(r"^(\d{1,2}):(\d{2})$", time_part)
    if match_24:
        h, m = int(match_24.group(1)), int(match_24.group(2))
        if 0 <= h < 24 and 0 <= m < 60:
            time_str = f"{h:02d}:{m:02d}"
            try:
                ZoneInfo(tz_part)
            except (ZoneInfoNotFoundError, ValueError):
                tz_part = default_tz
            return time_str, tz_part
            
    match_12 = re.match(r"^(\d{1,2})(?::(\d{2}))?\s*(AM|PM)$", time_part)
    if match_12:
        h = int(match_12.group(1))
        m = int(match_12.group(2)) if match_12.group(2) else 0
        ampm = match_12.group(3)
        if 1 <= h <= 12 and 0 <= m < 60:
            if ampm == "PM" and h < 12:
                h += 12
            elif ampm == "AM" and h == 12:
                h = 0
            time_str = f"{h:02d}:{m:02d}"
            try:
                ZoneInfo(tz_part)
            except (ZoneInfoNotFoundError, ValueError):
                tz_part = default_tz
            return time_str, tz_part

    return None

async def start_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with get_session_manager().lock_user(user_id):
        db = get_db()
        await db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)
        user_info = await db.get_user(user_id)
        
        onboarding_status = user_info.get("onboarding_status", "not_started")
        if onboarding_status != "completed":
            await db.update_onboarding_data(user_id, onboarding_status="not_started")
            await handle_onboarding_message(update, context, "not_started", "")
            return
    
        await schedule_user_reminder(context.application, user_id, DEFAULT_REMINDER_TIME, DEFAULT_TIMEZONE)
        profile = await get_profile(user_id)
        name = profile.get("name") or user_info.get("first_name") or update.effective_user.first_name or "there"
        
        await update.message.reply_text(
            f"heyy {name}! good to see you again. what's on your mind? 🌙\n\n"
            "here's a quick reminder of things you can do:\n"
            "- write in your diary: /diary\n"
            "- check your diary entries: /entries\n"
            "- look at what i remember: /memory\n"
            "- see our chat history: /chats\n"
            "- set daily check-in time: /settime"
        )

async def handle_onboarding_message(update: Update, context: ContextTypes.DEFAULT_TYPE, status: str, text: str):
    user_id = update.effective_user.id
    db = get_db()
    llm = get_llm()
    
    if status == "not_started":
        await db.update_onboarding_data(user_id, onboarding_status="waiting_name")
        await update.message.reply_text("heyy, i don't think we've met before. what's your name? :)")
        return
        
    await update.message.reply_chat_action(ChatAction.TYPING)
    
    if status == "waiting_name":
        name = await llm.extract_single_fact("name", text)
        if name == "Unknown" or len(name) > 30:
            name = text.strip()
            
        await db.update_onboarding_data(user_id, first_name=name, onboarding_status="waiting_age")
        await db.upsert_memory_item(user_id, "name", name, importance=1.0)
        await db.rebuild_semantic_profile_cache(user_id)
        
        await update.message.reply_text(f"nice to meet you, {name}! how old are you?")
        
    elif status == "waiting_age":
        age_str = await llm.extract_single_fact("age as an integer", text)
        match = re.search(r"\d+", age_str)
        if match:
            age = int(match.group(0))
        else:
            age = 0
            
        await db.update_onboarding_data(user_id, age=age, onboarding_status="waiting_nationality")
        if age > 0:
            await db.upsert_memory_item(user_id, "age", str(age), importance=0.9)
            await db.rebuild_semantic_profile_cache(user_id)
            
        await update.message.reply_text("gotcha. and where are you from originally? like your nationality?")
        
    elif status == "waiting_nationality":
        nationality = await llm.extract_single_fact("nationality", text)
        if nationality == "Unknown" or len(nationality) > 50:
            nationality = text.strip()
            
        await db.update_onboarding_data(user_id, nationality=nationality, onboarding_status="waiting_city")
        await db.upsert_memory_item(user_id, "nationality", nationality, importance=0.8)
        await db.rebuild_semantic_profile_cache(user_id)
        
        await update.message.reply_text("cool! what city do you live in right now?")
        
    elif status == "waiting_city":
        city = await llm.extract_single_fact("city name", text)
        if city == "Unknown" or len(city) > 50:
            city = text.strip()
            
        await db.update_onboarding_data(user_id, city=city, onboarding_status="completed")
        await db.upsert_memory_item(user_id, "city", city, importance=0.8)
        await db.rebuild_semantic_profile_cache(user_id)
        
        user_info = await db.get_user(user_id)
        name = user_info.get("first_name") or "friend"
        age = user_info.get("age") or "some"
        
        await update.message.reply_text(
            f"ah, {city}! that's awesome. well, it's really nice to meet you, {name}. i'm excited to get to know you better. :)\n\n"
            "by the way, if you ever want to write down your thoughts, just use /diary. you can also view what i remember about you with /memory, check your entries with /entries, and look at previous chats with /chats."
        )

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text
    db = get_db()
    
    async with get_session_manager().lock_user(user_id):
        await db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)
        user_info = await db.get_user(user_id)
        
        onboarding_status = user_info.get("onboarding_status", "not_started")
        if onboarding_status != "completed":
            await handle_onboarding_message(update, context, onboarding_status, text)
            return
            
        diary_mode = user_info.get("diary_mode", 0) if user_info else 0
        if diary_mode:
            await db.update_user(user_id, diary_mode=0)
            await update.message.reply_chat_action(ChatAction.TYPING)
            res = await process_diary_entry(user_id, text)
            
            # Append natural follow-up check-in query
            extra = random.choice([
                "Want to add more to today's entry?",
                "How was your day overall?",
                "Feel free to write more if you want, or let me know how the rest of your day goes."
            ])
            await update.message.reply_text(f"{res['followup']}\n\n{extra}")
            return

        # --- Session Segmentation & Lifecycle ---
        import uuid
        from app.memory_engine import compact_session

        now_dt = datetime.now()
        now_str = now_dt.isoformat()
        current_session_id = user_info.get("current_session_id")
        last_seen_str = user_info.get("last_seen")

        start_new_session = False
        if not current_session_id:
            start_new_session = True
        elif last_seen_str:
            try:
                last_seen_dt = datetime.fromisoformat(last_seen_str)
                # 30-minute gap threshold
                if (now_dt - last_seen_dt).total_seconds() >= 1800:
                    start_new_session = True
            except Exception:
                start_new_session = True
        else:
            start_new_session = True

        if start_new_session:
            # Archive/Compact old session if exists
            if current_session_id:
                end_time_str = last_seen_str or now_str
                await db.update_session(user_id, current_session_id, end_time=end_time_str)
                # Trigger out-of-band compaction in background task
                asyncio.create_task(compact_session(user_id, current_session_id))

            # Initialize a completely new session
            new_session_id = str(uuid.uuid4())
            today_date = now_dt.strftime("%Y-%m-%d")
            await db.create_session(new_session_id, user_id, now_str, today_date)
            await db.update_user(user_id, current_session_id=new_session_id)
            current_session_id = new_session_id
    
        await update.message.reply_chat_action(ChatAction.TYPING)
        
        # 1. Determine relationship stage
        stage = await determine_relationship_stage(user_id)
        
        # 2. Classify message type and compute token limit/instruction
        from app.prompts import STAGE_DIRECTIVES
        
        # Fast Python-based heuristic classifier to avoid a sequential LLM call (saving ~1.5s - 3s latency)
        text_clean = text.strip().lower()
        word_count = len(text_clean.split())
        
        # 1. Task classification
        task_keywords = {
            "write", "create", "generate", "code", "program", "calculate", "math", "sum", 
            "list", "plan", "trip", "explain", "how to", "tutorial", "recipe", "analyze", 
            "summarize", "help me", "translate", "format"
        }
        # 2. Emotional classification
        emotional_keywords = {
            "sad", "depressed", "hurt", "cry", "pain", "angry", "hate", "mad", "happy", "excited", 
            "glad", "joy", "scared", "fear", "anxious", "worry", "worried", "lonely", "alone", 
            "love", "miss", "feel", "feeling", "broken", "worst", "awesome", "great", "bad", "good"
        }
        # 3. Reflective classification
        reflective_keywords = {
            "think", "thought", "ponder", "wonder", "reflect", "maybe", "perhaps", "realize", 
            "realized", "understand", "believe", "mind", "life", "future", "past"
        }
        
        if any(kw in text_clean for kw in task_keywords):
            classification = "task"
        elif "?" in text_clean or text_clean.startswith(("what", "why", "how", "who", "where", "when", "can you", "could you", "do you", "are you", "is there")):
            classification = "question"
        elif any(kw in text_clean for kw in emotional_keywords):
            classification = "reflective" if word_count > 12 else "emotional"
        elif any(kw in text_clean for kw in reflective_keywords) or word_count > 8:
            classification = "reflective"
        else:
            classification = "casual"
        
        if classification == "casual":
            max_tokens = 60
            directive = "Directive: Respond in a short, casual texting format (1 sentence or a brief phrase, lowercase friendly). E.g. 'hii what are you doing?' or 'lmaoo true'."
        elif classification == "emotional":
            if word_count < 6:
                max_tokens = 50
                directive = "Directive: Keep it extremely short (1 sentence or a brief question). Just inquire naturally and supportively about what happened, without comfort paragraphs yet. E.g. 'what happened?' or 'oh no, what's wrong?'."
            else:
                max_tokens = 150
                directive = "Directive: Keep it to 2-3 sentences. Show warm, casual, friend-like comfort without sounding like a therapist."
        elif classification == "reflective":
            if word_count < 8:
                max_tokens = 100
                directive = "Directive: Respond in 1-2 sentences. Keep it conversational and brief."
            else:
                max_tokens = 220
                directive = "Directive: Respond in 2-4 sentences. Provide a thoughtful, peer-like reflection. Do not overexplain."
        elif classification == "question":
            max_tokens = 150
            directive = "Directive: Answer directly in a friendly, conversational manner (1-3 sentences)."
        else:  # task
            max_tokens = 500
            directive = "Directive: Respond as long as needed to fulfill the task helper request."
    
        # 3. Fetch contexts
        ctx = await build_context(user_id, text)
        
        profile = await get_profile(user_id)
        name = profile.get("name") or user_info.get("first_name") or update.effective_user.first_name or "friend"
        
        # Build dynamic system prompt
        stage_directive = STAGE_DIRECTIVES.get(stage, STAGE_DIRECTIVES["new"])
        full_prompt = SYSTEM_PROMPT_TEMPLATE.format(
            name=name,
            stage_directive=stage_directive,
            full_profile_context=ctx["full_profile_context"],
            temporal_context=ctx["temporal_context"],
            retrieved_memories=ctx["retrieved_memories"],
            length_directive=directive
        )
        
        try:
            response = await get_llm().chat(
                system_prompt=full_prompt,
                user_message=text,
                history=ctx["history"],
                max_tokens=max_tokens
            )
            
            episode_id = await save_episode(
                user_id, 
                text, 
                response, 
                detected_emotion="neutral", 
                emotion_confidence=1.0,
                session_id=current_session_id
            )
            
            await update.message.reply_text(response)
            
            async def enrich():
                async with get_session_manager().lock_user(user_id):
                    try:
                        emo = await analyze_emotion(text, response)
                        topics_json = json.dumps(emo.get("topics", []))
                        await db.execute("""
                            UPDATE episodes 
                            SET detected_emotion = ?, emotion_confidence = ?, secondary_emotion = ?, topics = ?
                            WHERE id = ? AND user_id = ?
                        """, emo.get("emotion"), emo.get("confidence"), emo.get("secondary_emotion"), topics_json, episode_id, user_id)
                        
                        await update_profile_from_conversation(user_id, text, response)
                        await db.update_user(user_id, last_seen=datetime.now().isoformat())
                    except Exception as e:
                        logger.error("Background enrichment failed for user %d: %s", user_id, e)
                        
            asyncio.create_task(enrich())
        except Exception as e:
            logger.error("Chat failed: %s", e)
            await update.message.reply_text("I'm a bit overwhelmed right now. Try again soon? 🤔")

async def diary_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with get_session_manager().lock_user(user_id):
        db = get_db()
        user_info = await db.get_user(user_id)
        if user_info.get("onboarding_status", "not_started") != "completed":
            await update.message.reply_text("hey, let's finish introducing ourselves first! what's your name?")
            return
            
        await db.update_user(user_id, diary_mode=1)
        await update.message.reply_text(DIARY_ENTRY_INTRO)

async def entries_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    user_info = await db.get_user(user_id)
    if user_info.get("onboarding_status", "not_started") != "completed":
        await update.message.reply_text("hey, let's finish introducing ourselves first! what's your name?")
        return

    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            pass

    entries = await db.get_all_diary_entries(user_id)
    if not entries:
        await update.message.reply_text("you haven't written any diary entries yet! use /diary to write one. 📓")
        return

    from collections import defaultdict
    grouped = defaultdict(list)
    for entry in entries:
        date_str = entry["created_at"][:10]
        grouped[date_str].append(entry)

    sorted_dates = sorted(grouped.keys(), reverse=True)
    limit = 3
    total_pages = (len(sorted_dates) + limit - 1) // limit

    if page < 1 or page > total_pages:
        page = 1

    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    dates_to_show = sorted_dates[start_idx:end_idx]

    lines = [f"📓 **Your Diary Entries** (Page {page} of {total_pages})\n"]
    for d in dates_to_show:
        lines.append(f"📅 **{d}**")
        for entry in grouped[d]:
            time_str = entry["created_at"][11:16]
            title = entry.get("title") or "Untitled Entry"
            emotions = entry.get("detected_emotions") or "neutral"
            snippet = entry["raw_text"][:120] + "..." if len(entry["raw_text"]) > 120 else entry["raw_text"]
            lines.append(f"- *{time_str}* - **{title}** (Mood: {emotions})\n  \"{snippet}\"")
        lines.append("")

    if page < total_pages:
        lines.append(f"Use `/entries {page+1}` to see older entries.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def chats_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    db = get_db()
    user_info = await db.get_user(user_id)
    if user_info.get("onboarding_status", "not_started") != "completed":
        await update.message.reply_text("hey, let's finish introducing ourselves first! what's your name?")
        return

    if context.args:
        arg = context.args[0]
        if re.match(r"^\d{4}-\d{2}-\d{2}$", arg):
            date_str = arg
            start = f"{date_str}T00:00:00"
            end = f"{date_str}T23:59:59"
            episodes = await db.get_episodes_by_date_range(user_id, start, end)
            if not episodes:
                await update.message.reply_text(f"no conversations found on {date_str}.")
                return

            lines = [f"💬 **Conversation on {date_str}**\n"]
            for ep in episodes:
                time_str = ep["timestamp"][11:16]
                lines.append(f"[{time_str}] **You**: {ep['user_message']}")
                lines.append(f"[{time_str}] **Eva**: {ep['bot_response']}\n")

            full_text = "\n".join(lines)
            if len(full_text) > 4000:
                for i in range(0, len(full_text), 4000):
                    await update.message.reply_text(full_text[i:i+4000], parse_mode="Markdown")
            else:
                await update.message.reply_text(full_text, parse_mode="Markdown")
            return

    rows = await db.fetch("""
        SELECT DISTINCT substr(timestamp, 1, 10) as date 
        FROM episodes 
        WHERE user_id = ? 
        ORDER BY date DESC
    """, user_id)
    dates = [r["date"] for r in rows]

    if not dates:
        await update.message.reply_text("we haven't chatted yet! write me a message to start. 😊")
        return

    page = 1
    if context.args:
        try:
            page = int(context.args[0])
        except ValueError:
            pass

    limit = 4
    total_pages = (len(dates) + limit - 1) // limit
    if page < 1 or page > total_pages:
        page = 1

    start_idx = (page - 1) * limit
    end_idx = start_idx + limit
    dates_to_show = dates[start_idx:end_idx]

    lines = [f"💬 **Your Chat History** (Page {page} of {total_pages})\n"]
    for d in dates_to_show:
        row = await db.fetchrow("""
            SELECT content FROM summaries 
            WHERE user_id = ? AND summary_type = 'daily' AND period_start LIKE ?
        """, user_id, f"{d}%")
        summary_text = row["content"] if row else "Ongoing conversation..."

        lines.append(f"📅 **{d}**")
        lines.append(f"*Summary*: {summary_text}")
        lines.append(f"👉 View conversation: `/chats {d}`\n")

    if page < total_pages:
        lines.append(f"Use `/chats {page+1}` to see older chat logs.")

    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def memory_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with get_session_manager().lock_user(user_id):
        db = get_db()
        user_info = await db.get_user(user_id)
        if user_info.get("onboarding_status", "not_started") != "completed":
            await update.message.reply_text("hey, let's finish introducing ourselves first! what's your name?")
            return
    
        if context.args and context.args[0].lower() == "delete":
            if len(context.args) < 2:
                await update.message.reply_text("Usage: `/memory delete <id>`")
                return
            try:
                mem_id = int(context.args[1])
            except ValueError:
                await update.message.reply_text("please provide a valid memory ID.")
                return
    
            row = await db.fetchrow("SELECT id FROM memory_items WHERE id = ? AND user_id = ?", mem_id, user_id)
            if not row:
                await update.message.reply_text("couldn't find a memory with that ID.")
                return
    
            await db.execute("DELETE FROM memory_items WHERE id = ? AND user_id = ?", mem_id, user_id)
            await db.rebuild_semantic_profile_cache(user_id)
            await update.message.reply_text("✅ memory deleted. i won't refer to it again!")
            return
    
        memories = await db.get_active_memories(user_id)
        if not memories:
            await update.message.reply_text("i don't have any saved memories about you yet! as we chat, i'll remember things naturally. 😊")
            return

    from collections import defaultdict
    grouped = defaultdict(list)
    for m in memories:
        grouped[m["category"]].append(m)

    CAT_LABELS = {
        "interests": "🎨 Interests & Hobbies",
        "habits": "☕ Habits & Routines",
        "favorite_topics": "💬 Favorite Topics",
        "recurring_emotions": "🎭 Emotional Patterns",
        "important_events": "📌 Important Life Events",
        "relationships": "👥 Recurring People / Relationships",
        "preferences": "❤️ Preferences",
        "goals": "🎯 Active Goals",
        "stressors": "⚠️ Stressors & Concerns",
        "fears": "👻 Fears",
        "aspirations": "🚀 Aspirations",
        "strengths": "💪 Strengths",
        "name": "👤 Name",
        "age": "📅 Age",
        "nationality": "🌍 Nationality",
        "city": "🏙️ City",
    }

    lines = ["🧠 **What I Remember About You**\n"]
    sorted_categories = sorted(grouped.keys(), key=lambda c: list(CAT_LABELS.keys()).index(c) if c in CAT_LABELS else 99)
    for cat in sorted_categories:
        label = CAT_LABELS.get(cat, f"Other ({cat.capitalize()})")
        lines.append(f"**{label}**")
        for m in grouped[cat]:
            lines.append(f"- `[{m['id']}]` {m['content']}")
        lines.append("")

    lines.append("To delete any memory, use `/memory delete <id>` (e.g. `/memory delete 3`).")
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")

async def settime_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with get_session_manager().lock_user(user_id):
        db = get_db()
        user_info = await db.get_user(user_id)
        if user_info.get("onboarding_status", "not_started") != "completed":
            await update.message.reply_text("hey, let's finish introducing ourselves first! what's your name?")
            return
    
        if not context.args:
            await update.message.reply_text(
                "Usage:\n"
                "- `/settime HH:MM` (e.g. `/settime 21:00`)\n"
                "- `/settime HH:MM TZ` (e.g. `/settime 9 PM UTC` or `/settime 21:00 Asia/Kolkata`)"
            )
            return
    
        text_arg = " ".join(context.args)
        default_tz = user_info.get("timezone", DEFAULT_TIMEZONE) if user_info else DEFAULT_TIMEZONE
        res = parse_time_and_tz(text_arg, default_tz)
        if not res:
            await update.message.reply_text(
                "❌ invalid format. examples of what works:\n"
                "- `/settime 21:00`\n"
                "- `/settime 9 PM`\n"
                "- `/settime 9:30 PM UTC`\n"
                "- `/settime 22:00 Asia/Kolkata`"
            )
            return
    
        time_str, tz_str = res
        try:
            await schedule_user_reminder(context.application, user_id, time_str, tz_str)
            await update.message.reply_text(f"✅ reminder set for daily at **{time_str}** ({tz_str})!")
        except Exception as e:
            logger.error("Failed to set reminder: %s", e)
            await update.message.reply_text("❌ failed to configure scheduler. check inputs.")

async def clear_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [
            InlineKeyboardButton("❌ Yes, Delete Everything", callback_data="clear_confirm"),
            InlineKeyboardButton("↩️ No, Keep My Data", callback_data="clear_cancel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "⚠️ **Are you absolutely sure you want to clear your memory?**\n\n"
        "This will permanently erase all your chats, diary entries, summaries, memories, and onboarding data. "
        "This action cannot be undone.",
        reply_markup=reply_markup,
        parse_mode="Markdown"
    )

async def clear_callback_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    async with get_session_manager().lock_user(user_id):
        db = get_db()
        
        if query.data == "clear_confirm":
            await db.delete_all_user_data(user_id)
            await query.edit_message_text(
                "🗑️ **All data cleared successfully.**\n\n"
                "Everything has been wiped. Send /start to begin onboarding again."
            )
        elif query.data == "clear_cancel":
            await query.edit_message_text("❌ **Clear cancelled.** Your data is safe!")

async def reboot_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    async with get_session_manager().lock_user(user_id):
        db = get_db()
        
        # 1. Delete all user data
        await db.delete_all_user_data(user_id)
        
        # 2. Reset onboarding state to 'waiting_name'
        await db.ensure_user(user_id, update.effective_user.username, update.effective_user.first_name)
        await db.update_onboarding_data(user_id, onboarding_status="waiting_name")
        
        # 3. Send prompt
        await update.message.reply_text(
            "🗑️ **system rebooted. all data has been wiped clean.**\n\n"
            "heyy, i don't think we've met before. what's your name? :)"
        )

async def commands_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    from telegram import BotCommand
    commands_list = [
        BotCommand("start", "start onboarding or greet Eva"),
        BotCommand("diary", "write a new diary entry"),
        BotCommand("entries", "view your past diary entries"),
        BotCommand("chats", "view your chat history and sessions"),
        BotCommand("memory", "view or delete things Eva remembers about you"),
        BotCommand("settime", "configure daily reminder check-in time"),
        BotCommand("clear", "clear all your data permanently"),
        BotCommand("reboot", "wipe everything and restart onboarding"),
        BotCommand("export", "export your companion history and memories"),
        BotCommand("commands", "list all available commands")
    ]
    try:
        await context.bot.set_my_commands(commands_list)
    except Exception as e:
        logger.error("Failed to set commands menu: %s", e)

    await update.message.reply_text(
        "🤖 **Available Commands**\n\n"
        "/start - start onboarding or greet Eva 🌙\n"
        "/diary - write a new diary entry 📓\n"
        "/entries - view your past diary entries\n"
        "/chats - view your chat history and sessions 💬\n"
        "/memory - view or delete things Eva remembers about you 🧠\n"
        "/settime - configure daily reminder check-in time ⏰\n"
        "/clear - clear all your data permanently ⚠️\n"
        "/reboot - wipe everything and restart onboarding 🔄\n"
        "/export - export your companion history and memories 📦\n"
        "/commands - list all available commands 📋",
        parse_mode="Markdown"
    )

async def export_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import os
    from app.export_engine import parse_export_arguments, generate_export
    
    if not update.message or not update.effective_user:
        return
        
    user_id = update.effective_user.id
    db = get_db()
    
    async with get_session_manager().lock_user(user_id):
        user_info = await db.get_user(user_id)
        if not user_info or user_info.get("onboarding_status", "not_started") != "completed":
            await update.message.reply_text("hey, let's finish introducing ourselves first! what's your name?")
            return
            
        options = parse_export_arguments(context.args)
        episode_count = await db.get_episode_count(user_id)
        
    # If the history is large (> 100 episodes), run the export in the background asynchronously
    is_large = episode_count > 100
    
    if is_large:
        await update.message.reply_text("⏳ I'm compiling your companion history archive in the background. I'll send it to you as soon as it's ready!")
        
        async def run_background_export():
            try:
                file_path, file_name = await generate_export(user_id, options)
                if not file_path or not os.path.exists(file_path):
                    await context.bot.send_message(chat_id=user_id, text="❌ Export failed. No matching data was found or generation failed.")
                    return
                    
                with open(file_path, "rb") as doc:
                    await context.bot.send_document(
                        chat_id=user_id,
                        document=doc,
                        filename=file_name,
                        caption="Here is your requested companion history archive! 📦✨"
                    )
                try:
                    os.remove(file_path)
                except OSError:
                    pass
            except Exception as e:
                logger.error("Async export failed for user %d: %s", user_id, e)
                await context.bot.send_message(chat_id=user_id, text="❌ An error occurred during background export. Please try again.")
                
        asyncio.create_task(run_background_export())
    else:
        try:
            file_path, file_name = await generate_export(user_id, options)
            if not file_path or not os.path.exists(file_path):
                await update.message.reply_text("❌ Export failed. No matching data was found or generation failed.")
                return
                
            with open(file_path, "rb") as doc:
                await update.message.reply_document(
                    document=doc,
                    filename=file_name,
                    caption="Here is your requested companion history archive! 📦✨"
                )
            try:
                os.remove(file_path)
            except OSError:
                pass
        except Exception as e:
            logger.error("Sync export failed for user %d: %s", user_id, e)
            await update.message.reply_text("❌ An error occurred while generating your export. Please try again.")

def rate_limited(handler, is_ai: bool = False):
    """Decorator to limit user requests and protect AI completion credits."""
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
        if not update.effective_user:
            return await handler(update, context)
            
        user_id = update.effective_user.id
        from app.utils import check_rate_limit
        
        # General Command / Message rate limit: 12 requests per 30 seconds
        allowed = await check_rate_limit(user_id, "api_request", limit=12, window=30)
        if not allowed:
            logger.warning("Rate limit exceeded for user %d on api_request", user_id)
            if update.message:
                await update.message.reply_text("⚠️ you are sending messages too fast. please wait a moment.")
            return
            
        if is_ai:
            # AI completions rate limit: 5 completions per 60 seconds
            allowed_ai = await check_rate_limit(user_id, "ai_generation", limit=5, window=60)
            if not allowed_ai:
                logger.warning("Rate limit exceeded for user %d on AI generation", user_id)
                if update.message:
                    await update.message.reply_text("⚠️ you are generating AI responses too fast. please wait a moment.")
                return
                
        return await handler(update, context)
    return wrapper

def build_ptb_application() -> Application:
    """Factory to build the PTB application."""
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    
    app.add_handler(CommandHandler("start", rate_limited(start_handler)))
    app.add_handler(CommandHandler("diary", rate_limited(diary_handler)))
    app.add_handler(CommandHandler("entries", rate_limited(entries_handler)))
    app.add_handler(CommandHandler("chats", rate_limited(chats_handler)))
    app.add_handler(CommandHandler("memory", rate_limited(memory_handler)))
    app.add_handler(CommandHandler("settime", rate_limited(settime_handler)))
    app.add_handler(CommandHandler("clear", rate_limited(clear_handler)))
    app.add_handler(CommandHandler("reboot", rate_limited(reboot_handler)))
    app.add_handler(CommandHandler("commands", rate_limited(commands_handler)))
    app.add_handler(CommandHandler("export", rate_limited(export_handler)))
    app.add_handler(CallbackQueryHandler(rate_limited(clear_callback_handler)))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, rate_limited(message_handler, is_ai=True)))
    
    return app
