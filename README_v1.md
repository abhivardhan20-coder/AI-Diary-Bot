# Project Snapshot: telegram-memory-bot

> [!NOTE]
> This is an automatically generated, portable code snapshot of the project.
> It contains metadata, the file tree layout, and the complete contents of all non-excluded files.

## Snapshot Metadata
- **Generation Timestamp**: 2026-05-30 21:12:04 (Local Time)
- **README Version**: v1
- **Total Files Included**: 32

## Project Directory Tree
```text
.
├── .dockerignore
├── .gitignore
├── .pytest_cache
│   ├── .gitignore
│   ├── CACHEDIR.TAG
│   ├── README.md
│   └── v
│       └── cache
│           ├── lastfailed
│           └── nodeids
├── .python-version
├── Dockerfile
├── README.md
├── api
│   └── index.py
├── app
│   ├── __init__.py
│   ├── bot.py
│   ├── config.py
│   ├── diary_engine.py
│   ├── export_engine.py
│   ├── logging_config.py
│   ├── memory_engine.py
│   ├── prompts.py
│   ├── retrieval_engine.py
│   ├── scheduler.py
│   ├── semantic_engine.py
│   ├── utils.py
│   └── webhook.py
├── main.py
├── pyproject.toml
├── railway.json
├── requirements.txt
├── tests
│   ├── test_export.py
│   ├── test_session_memory.py
│   └── test_user_isolation.py
└── vercel.json
```

---

## File Contents

### 📄 `.dockerignore`

```dockerignore
# Version control
.git
.gitignore

# Python
__pycache__/
*.py[cod]
*$py.class
.venv/
.env

# Data and Logs
data/
backups/
logs/
exports/
memory/

# IDE
.vscode/
.idea/

```

### 📄 `.gitignore`

```
# Version control
.git

# Python-generated files
__pycache__/
*.py[oc]
build/
dist/
wheels/
*.egg-info
.venv/
.python-version
uv.lock

# Secrets
.env

# Runtime Data
data/
backups/
logs/
exports/
memory/

# IDE
.vscode/
.idea/

```

### 📄 `.pytest_cache/.gitignore`

```
# Created by pytest automatically.
*

```

### 📄 `.pytest_cache/CACHEDIR.TAG`

```
Signature: 8a477f597d28d172789f06886806bc55
# This file is a cache directory tag created by pytest.
# For information about cache directory tags, see:
#	https://bford.info/cachedir/spec.html

```

### 📄 `.pytest_cache/README.md`

```markdown
# pytest cache directory #

This directory contains data from the pytest's cache plugin,
which provides the `--lf` and `--ff` options, as well as the `cache` fixture.

**Do not** commit this to version control.

See [the docs](https://docs.pytest.org/en/stable/how-to/cache.html) for more information.

```

### 📄 `.pytest_cache/v/cache/lastfailed`

```
{}
```

### 📄 `.pytest_cache/v/cache/nodeids`

```
[
  "tests/test_export.py::test_export_formatters_and_zip",
  "tests/test_export.py::test_export_security_isolation",
  "tests/test_export.py::test_generate_export_full_json",
  "tests/test_export.py::test_parse_export_arguments",
  "tests/test_session_memory.py::test_commands_handler",
  "tests/test_session_memory.py::test_hybrid_session_retrieval",
  "tests/test_session_memory.py::test_session_compaction_and_summarization",
  "tests/test_session_memory.py::test_session_segmentation_lifecycle",
  "tests/test_user_isolation.py::test_concurrency_race_conditions",
  "tests/test_user_isolation.py::test_db_queries_isolation",
  "tests/test_user_isolation.py::test_session_manager_cleanup",
  "tests/test_user_isolation.py::test_vector_search_isolation"
]
```

### 📄 `.python-version`

```
3.14

```

### 📄 `api/index.py`

```python
import os
import json
import logging
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from telegram import Update

from app.config import WEBHOOK_SECRET, CRON_SECRET
from app.bot import build_ptb_application
from app.database import get_db
from app.utils import check_and_generate_summaries
from app.semantic_engine import curate_user_profile

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Expose app for Vercel
app = FastAPI()

# Global bot application instance
ptb_app = build_ptb_application()

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    client_host = request.client.host if request.client else "unknown"
    logger.error("Unhandled API error from IP %s during request to %s: %s", 
                 client_host, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal Server Error"}
    )

@app.on_event("startup")
async def startup():
    try:
        logger.info("Starting database initialization...")
        await get_db().initialize()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error("DATABASE INIT FAILED: %s", e, exc_info=True)
        # Don't re-raise — let the app start so we can at least see health/root endpoints
    try:
        logger.info("Starting PTB application initialization...")
        await ptb_app.initialize()
        logger.info("PTB application initialized successfully.")
        
        # Start PTB application
        await ptb_app.start()
        logger.info("PTB application started successfully.")
        
        logger.info("Skipping automatic webhook registration on startup (handled on-demand via /setup-webhook to optimize cold starts).")
    except Exception as e:
        logger.error("PTB INIT/START FAILED: %s", e, exc_info=True)

@app.on_event("shutdown")
async def shutdown():
    try:
        await ptb_app.stop()
    except Exception as e:
        logger.error("Error stopping PTB app: %s", e)
    await ptb_app.shutdown()
    await get_db().close()

@app.get("/")
async def root():
    return {"status": "online", "message": "Telegram Memory Bot is active."}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/setup-webhook")
async def setup_webhook(request: Request):
    from app.config import WEBHOOK_URL, WEBHOOK_SECRET, TELEGRAM_BOT_TOKEN
    
    # Use config WEBHOOK_URL if set, otherwise detect dynamically from request
    base_url = WEBHOOK_URL.rstrip('/') if WEBHOOK_URL else str(request.base_url).rstrip('/')
    # Force HTTPS on Vercel
    if "localhost" not in base_url and not base_url.startswith("https://"):
        base_url = base_url.replace("http://", "https://")
    webhook_path = f"{base_url}/webhook"
    
    logger.info("Setting webhook to %s", webhook_path)
    try:
        success = await ptb_app.bot.set_webhook(
            url=webhook_path,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=False
        )
        
        try:
            await ptb_app.start()
        except Exception as start_err:
            logger.warning("Bot already started or start failed: %s", start_err)
            
        return {
            "status": "success" if success else "failed",
            "webhook_url": webhook_path,
            "secret_token_configured": bool(WEBHOOK_SECRET),
            "bot_token_configured": bool(TELEGRAM_BOT_TOKEN),
            "info": "Webhook set successfully. Try messaging your bot now!"
        }
    except Exception as e:
        logger.error("Failed to set webhook: %s", e, exc_info=True)
        return {
            "status": "error",
            "error": str(e),
            "webhook_url": webhook_path
        }

@app.post("/webhook")
async def webhook(request: Request):
    client_host = request.client.host if request.client else "unknown"
    # Verify secret token
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != WEBHOOK_SECRET:
        logger.warning("Unauthorized webhook attempt from IP %s with invalid secret", client_host)
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        data = await request.json()
        
        # Deduplication using Redis
        update_id = data.get("update_id")
        if update_id is not None:
            from app.utils import is_duplicate_update
            if await is_duplicate_update(update_id):
                logger.info("Dropping duplicate Telegram update: %s", update_id)
                return Response(status_code=status.HTTP_200_OK)

        update = Update.de_json(data, ptb_app.bot)
        
        # Check unusual traffic patterns
        user_id = None
        if update.effective_user:
            user_id = update.effective_user.id
        if user_id is not None:
            from app.utils import log_unusual_traffic
            traffic_count = await log_unusual_traffic(user_id)
            if traffic_count is not None:
                logger.warning("Suspicious traffic pattern detected: user %d sent %d requests in the last 60 seconds from IP %s", user_id, traffic_count, client_host)
        
        # Verify and process
        await ptb_app.process_update(update)
    except Exception as e:
        logger.error("Error processing update from IP %s: %s", client_host, e)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(status_code=status.HTTP_200_OK)

@app.get("/cron/daily")
async def cron_daily(request: Request):
    client_host = request.client.host if request.client else "unknown"
    if CRON_SECRET:
        auth_header = request.headers.get("Authorization")
        if not auth_header or auth_header != f"Bearer {CRON_SECRET}":
            logger.warning("Unauthorized cron attempt from IP %s with invalid secret", client_host)
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
    logger.info("Executing Vercel Cron: Daily Summaries, Session Compaction & Curation")
    db = get_db()
    try:
        # Run out-of-band session compaction globally for all uncompacted ended sessions
        from app.memory_engine import compact_uncompacted_sessions
        await compact_uncompacted_sessions()
        
        users = await db.get_all_users()
        for u in users:
            uid = u["user_id"]
            try:
                await check_and_generate_summaries(uid)
                await curate_user_profile(uid)
            except Exception as e:
                logger.error("Cron failed for user %d: %s", uid, e)
    except Exception as e:
        logger.error("Daily summaries cron failed: %s", e)
        return {"status": "failed", "error": str(e)}
    return {"status": "success"}

@app.post("/qstash-reminder")
async def qstash_reminder(request: Request):
    from qstash import Receiver
    from app.config import QSTASH_CURRENT_SIGNING_KEY, QSTASH_NEXT_SIGNING_KEY
    
    if not QSTASH_CURRENT_SIGNING_KEY:
        logger.warning("No QStash signing keys configured. Proceeding without signature validation.")
    else:
        receiver = Receiver(
            current_signing_key=QSTASH_CURRENT_SIGNING_KEY,
            next_signing_key=QSTASH_NEXT_SIGNING_KEY
        )
        body = await request.body()
        signature = request.headers.get("Upstash-Signature")
        try:
            receiver.verify(body=body.decode("utf-8"), signature=signature)
        except Exception as e:
            logger.warning("QStash signature verification failed: %s", e)
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
            
    try:
        data = await request.json()
        user_id = data.get("user_id")
        if user_id:
            from app.scheduler import send_reminder_now
            await send_reminder_now(ptb_app, user_id)
    except Exception as e:
        logger.error("Error processing QStash reminder: %s", e)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)
        
    return {"status": "success"}

```

### 📄 `app/__init__.py`

```python
__version__ = "2.1.0"

```

### 📄 `app/bot.py`

```python
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

async def determine_relationship_stage(user_id: int, user_info: dict | None = None) -> str:
    db = get_db()
    if not user_info:
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
        user_info = await db.get_user(user_id)
        if not user_info or user_info.get("username") != update.effective_user.username or user_info.get("first_name") != update.effective_user.first_name:
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
            # Initialize a completely new session
            new_session_id = str(uuid.uuid4())
            today_date = now_dt.strftime("%Y-%m-%d")
            
            writes = [
                db.create_session(new_session_id, user_id, now_str, today_date),
                db.update_user(user_id, current_session_id=new_session_id)
            ]
            if current_session_id:
                end_time_str = last_seen_str or now_str
                writes.append(db.update_session(user_id, current_session_id, end_time=end_time_str))
                # Trigger out-of-band compaction in background task
                asyncio.create_task(compact_session(user_id, current_session_id))
                
            await asyncio.gather(*writes)
            current_session_id = new_session_id
    
        await update.message.reply_chat_action(ChatAction.TYPING)
        
        # 1. Determine relationship stage
        stage = await determine_relationship_stage(user_id, user_info=user_info)
        
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
        ctx = await build_context(user_id, text, user_info=user_info, classification=classification)
        
        profile = ctx.get("profile")
        if not profile:
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

```

### 📄 `app/config.py`

```python
"""
Centralized configuration for the AI Diary Companion.
Production-ready with environment validation and Railway compatibility.
"""

import os
import logging
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

IS_VERCEL: bool = os.getenv("VERCEL") is not None

# ── Secrets & Core Env ──────────────────────────────────────────────────────────
TELEGRAM_BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "")
OPENROUTER_API_KEY: str = os.getenv("OPENROUTER_API_KEY", "")
QSTASH_TOKEN: str = os.getenv("QSTASH_TOKEN", "")
QSTASH_CURRENT_SIGNING_KEY: str = os.getenv("QSTASH_CURRENT_SIGNING_KEY", "")
QSTASH_NEXT_SIGNING_KEY: str = os.getenv("QSTASH_NEXT_SIGNING_KEY", "")

# Webhook Settings
WEBHOOK_URL: str = os.getenv("WEBHOOK_URL", "")  # e.g., https://your-app.up.railway.app
WEBHOOK_SECRET: str = os.getenv("WEBHOOK_SECRET", "super-secret-token")
CRON_SECRET: str = os.getenv("CRON_SECRET", "")
PORT: int = int(os.getenv("PORT", "8000"))

# Cloud Storage Settings
DATABASE_URL: str = os.getenv("DATABASE_URL", "")
REDIS_URL: str = os.getenv("REDIS_URL", "")

# ── LLM Settings ────────────────────────────────────────────────────────────────
LLM_BASE_URL: str = "https://openrouter.ai/api/v1"
LLM_MODEL: str = os.getenv("LLM_MODEL", "google/gemini-2.5-flash")
LLM_MAX_TOKENS: int = int(os.getenv("LLM_MAX_TOKENS", "800"))
LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.7"))
LLM_RETRY_ATTEMPTS: int = 3
LLM_RETRY_BASE_DELAY: float = 1.0

# ── Paths ────────────────────────────────────────────────────────────────────────
BASE_DIR: Path = Path(__file__).parent.parent
DATA_DIR: Path = Path(os.getenv("DATA_PATH", str(BASE_DIR / "data") if not IS_VERCEL else "/tmp"))
BACKUP_DIR: Path = Path(os.getenv("BACKUP_PATH", str(BASE_DIR / "backups") if not IS_VERCEL else "/tmp"))
EXPORT_DIR: Path = Path(os.getenv("EXPORT_PATH", str(BASE_DIR / "exports") if not IS_VERCEL else "/tmp"))
LOG_DIR: Path = Path(os.getenv("LOG_PATH", str(BASE_DIR / "logs") if not IS_VERCEL else "/tmp"))

# Create directories only outside of Vercel serverless environment
if not IS_VERCEL:
    for d in (DATA_DIR, BACKUP_DIR, EXPORT_DIR, LOG_DIR):
        d.mkdir(parents=True, exist_ok=True)

DB_PATH: Path = DATA_DIR / "diary.db"

# ── Timezone & Scheduling ───────────────────────────────────────────────────────
DEFAULT_TIMEZONE: str = os.getenv("TIMEZONE", "Asia/Kolkata")
DEFAULT_REMINDER_TIME: str = "22:00"
BACKUP_INTERVAL_HOURS: int = int(os.getenv("BACKUP_INTERVAL_HOURS", "6"))

# ── Memory & Retrieval Settings ─────────────────────────────────────────────────
RECENT_EPISODES_COUNT: int = 5
TOPIC_MATCH_COUNT: int = 3
EMOTION_MATCH_COUNT: int = 3
SUMMARY_COUNT: int = 2
MAX_CONTEXT_CHARS: int = 12000

# ── Logging ──────────────────────────────────────────────────────────────────────
LOG_LEVEL: str = os.getenv("LOG_LEVEL", "INFO")
LOG_FORMAT: str = "%(asctime)s | %(name)-20s | %(levelname)-7s | %(message)s"

def validate_config():
    """Fail fast if required environment variables are missing."""
    missing = []
    if not TELEGRAM_BOT_TOKEN:
        missing.append("TELEGRAM_BOT_TOKEN")
    if not OPENROUTER_API_KEY:
        missing.append("OPENROUTER_API_KEY")
    if not WEBHOOK_URL:
        # We don't fail here because local testing might use polling or tunnel
        print("WARNING: WEBHOOK_URL is not set. Bot will only work in polling mode if manually started.")
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")

```

### 📄 `app/diary_engine.py`

```python
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

```

### 📄 `app/export_engine.py`

```python
import os
import re
import json
import zipfile
import logging
from datetime import datetime
from app.database import get_db

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
            episodes = await db.get_episodes_for_session(user_id, options["session_id"])
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
            
        sessions_list = []
        for s in all_sessions:
            episodes = await db.get_episodes_for_session(user_id, s["session_id"])
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

    # Ensure output exports/ directory exists
    os.makedirs("exports", exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    base_name = f"export_{user_id}_{ts}"
    
    raw_path = None
    file_name = None
    
    # Render file content based on selected format
    if options["format"] == "json":
        raw_path = f"exports/{base_name}.json"
        with open(raw_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        file_name = f"{base_name}.json"
        
    elif options["format"] == "txt":
        raw_path = f"exports/{base_name}.txt"
        file_name = f"{base_name}.txt"
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
        raw_path = f"exports/{base_name}.md"
        file_name = f"{base_name}.md"
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
        zip_path = f"exports/{base_name}.zip"
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            zipf.write(raw_path, arcname=file_name)
        
        # Clean up the raw unzipped temporary file
        try:
            os.remove(raw_path)
        except OSError:
            pass
            
        return zip_path, f"{base_name}.zip"

    return raw_path, file_name

```

### 📄 `app/logging_config.py`

```python
"""
Structured logging configuration for production.
Supports console output and rotating file logs.
"""

import logging
import sys
from logging.handlers import RotatingFileHandler
from app.config import LOG_LEVEL, LOG_FORMAT, LOG_DIR

def setup_logging():
    """Configure logging for the entire application."""
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, LOG_LEVEL, logging.INFO))

    # Formatter
    formatter = logging.Formatter(LOG_FORMAT)

    # Console Handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    root_logger.addHandler(console_handler)

    # Rotating File Handler (Production)
    try:
        file_handler = RotatingFileHandler(
            LOG_DIR / "app.log",
            maxBytes=5 * 1024 * 1024,  # 5MB
            backupCount=5,
            encoding="utf-8"
        )
        file_handler.setFormatter(formatter)
        root_logger.addHandler(file_handler)
    except Exception as e:
        print(f"Failed to setup file logging: {e}")

    # Reduce noise from third-party libs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)
    logging.getLogger("telegram").setLevel(logging.INFO)

    logging.info("Logging initialized at %s level", LOG_LEVEL)

```

### 📄 `app/memory_engine.py`

```python
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


```

### 📄 `app/prompts.py`

```python
"""
All LLM system prompts and diary question templates.
Centralised here so personality and behaviour can be tuned in one place.
"""

import random

# ── Main Companion System Prompt Template ─────────────────────────────────────────

SYSTEM_PROMPT_TEMPLATE = """\
You are Eva — {name}'s warm, casual online friend (NOT a therapist/AI assistant).
Style: lowercase texting format (e.g. "hii", "lmaoo", "wait what?"). Relaxed grammar. No unsolicited advice, no repetitive acknowledgements. Match length/energy.
{stage_directive}

Profile:
{full_profile_context}

Context:
{temporal_context}

Memories:
{retrieved_memories}
(Reference memories naturally, e.g. "when we spoke yesterday". Never say "session", "episode", or database IDs.)

Rules:
- Greet casually. If user vents, respond briefly/inquiringly first (e.g. "what happened?"). Only escalate comfort if they share details.
- Ask max 1 question, at most 30% of the time.
- BANNED (DO NOT USE): "I understand", "I hear you", "That sounds like", "It must be", "Thank you for sharing", "As an AI", "I'm here for you", "That's valid", "Remember that", "Absolutely", "Certainly", "How can I help you", "As your companion", "As an AI companion".

{length_directive}
"""

STAGE_DIRECTIVES = {
    "new": "You're getting to know this person. Be warm but not presumptuous.",
    "warming": "You know the basics about this person. Be more personal, reference what you know.",
    "established": "You know this person well. You can be more direct, call out patterns you notice.",
    "close": "This is a close friendship. You can tease, push back, speak plainly.",
}

# ── Diary Check-in Prompts ───────────────────────────────────────────────────────

DIARY_PROMPTS = [
    "Hey! How was your day today? 🌙",
    "Good evening! What emotions did you feel most strongly today?",
    "Hi there! Did anything stressful or meaningful happen today?",
    "Evening check-in time! What are you thinking about tonight? 💭",
    "What are you grateful for today? Even small things count. ✨",
    "Did anything make you anxious, excited, angry, or proud today?",
    "How are you feeling right now, in this moment?",
    "What was the highlight of your day? And what was the hardest part?",
    "Did you learn anything new about yourself today?",
    "If you could describe today in one word, what would it be?",
    "How did you take care of yourself today?",
    "What's been on your mind the most this week?",
    "Did you make progress on anything that matters to you today?",
    "How was your energy today compared to yesterday?",
    "Is there something you wish you had done differently today?",
    "What's one thing you're looking forward to tomorrow?",
    "Did you have any meaningful conversations today?",
    "How well did you sleep last night, and how did it affect your day?",
    "What challenged you today, and how did you handle it?",
    "Take a moment — how is your body feeling right now? Any tension?",
]

# Context-aware diary prompts — used when we have history
CONTEXTUAL_DIARY_TEMPLATES = [
    "Last time you mentioned {topic}. How has that been going?",
    "You were feeling {emotion} recently. Has that shifted at all?",
    "You set a goal to {goal}. Any progress today?",
    "A while back you talked about {event}. How are things now?",
    "You mentioned struggling with {stressor}. How was that today?",
]


def get_diary_prompt() -> str:
    """Return a random diary check-in prompt."""
    return random.choice(DIARY_PROMPTS)


def get_contextual_diary_prompt(
    topics: list[str] | None = None,
    emotions: list[str] | None = None,
    goals: list[str] | None = None,
    stressors: list[str] | None = None,
    events: list[str] | None = None,
) -> str:
    """
    Return a context-aware diary prompt if we have history,
    otherwise fall back to a generic one.
    """
    candidates: list[str] = []

    if topics:
        candidates.append(
            random.choice(CONTEXTUAL_DIARY_TEMPLATES[:1]).format(
                topic=random.choice(topics)
            )
        )
    if emotions:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[1].format(emotion=random.choice(emotions))
        )
    if goals:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[2].format(goal=random.choice(goals))
        )
    if events:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[3].format(event=random.choice(events))
        )
    if stressors:
        candidates.append(
            CONTEXTUAL_DIARY_TEMPLATES[4].format(stressor=random.choice(stressors))
        )

    if candidates:
        # Mix: 60% chance contextual, 40% chance generic
        if random.random() < 0.6:
            return random.choice(candidates)

    return get_diary_prompt()


# ── Emotion Analysis Prompt ──────────────────────────────────────────────────────

EMOTION_ANALYSIS_PROMPT = """\
Analyze the emotional tone of this conversation exchange.

User message: "{user_message}"
Assistant response: "{bot_response}"

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "emotion": "<primary emotion>",
  "confidence": <0.0-1.0>,
  "secondary_emotion": "<secondary emotion or null>",
  "topics": ["<topic1>", "<topic2>"]
}}

Valid emotions: happy, sad, anxious, angry, excited, stressed, grateful, neutral, \
proud, lonely, hopeful, frustrated, calm, overwhelmed, nostalgic, confused, motivated, tired

Extract 1-3 key topics discussed (e.g., "work", "relationships", "health", "exams").
"""

# ── Profile Extraction Prompt ────────────────────────────────────────────────────

PROFILE_EXTRACTION_PROMPT = """\
Given this conversation exchange, extract any NEW personal information about the user \
that should be remembered long-term. Only extract facts that are clearly stated or \
strongly implied. Do not invent information.

User message: "{user_message}"
Assistant response: "{bot_response}"

Current known profile:
{current_profile}

Respond with ONLY a JSON object containing ONLY the fields that have NEW updates \
(do not repeat existing information). Use null for no update. Example:
{{
  "name": null,
  "goals": ["new goal if mentioned"],
  "stressors": ["new stressor if mentioned"],
  "preferences": [],
  "relationships": [],
  "recurring_emotions": [],
  "important_events": [],
  "habits": [],
  "routines": [],
  "personality_traits": [],
  "fears": [],
  "aspirations": [],
  "strengths": [],
  "interests": ["new interest if mentioned"],
  "favorite_topics": ["new favorite topic if mentioned"]
}}

If nothing new was shared, respond with: {{"no_update": true}}
"""

# ── Summary Generation Prompts ───────────────────────────────────────────────────

DAILY_SUMMARY_PROMPT = """\
Summarize the following diary conversations from {date} into a concise daily summary.

Conversations:
{episodes}

Create a summary covering:
1. Key events or activities mentioned
2. Dominant emotional tone
3. Any concerns or stressors discussed
4. Progress toward goals (if mentioned)
5. Notable insights or reflections

Keep it to 3-5 sentences. Be specific, not generic. Write in third person \
(e.g., "The user felt..." or "They mentioned...").
"""

WEEKLY_SUMMARY_PROMPT = """\
Summarize the following daily summaries from the past week into a weekly overview.

Daily summaries:
{daily_summaries}

Create a weekly summary covering:
1. Overall emotional trajectory for the week
2. Major events or milestones
3. Recurring concerns or patterns
4. Goal progress
5. Notable changes from previous weeks (if apparent)

Keep it to 4-6 sentences. Focus on trends and patterns, not individual days.
"""

MONTHLY_SUMMARY_PROMPT = """\
Summarize the following weekly summaries from the past month into a monthly overview.

Weekly summaries:
{weekly_summaries}

Create a monthly summary covering:
1. Emotional arc across the month
2. Biggest events or life changes
3. Patterns in behaviour, mood, or habits
4. Progress toward long-term goals
5. Areas of growth or concern

Keep it to 5-8 sentences. Focus on the big picture.
"""

# ── Search Result Prompt ─────────────────────────────────────────────────────────

SEARCH_RESULT_PROMPT = """\
The user searched their memory for: "{query}"

Here are the matching memories:
{results}

Summarize these memories naturally and conversationally. Reference dates when helpful. \
Highlight patterns or recurring themes if you notice any. Be specific — quote the user's \
own words where impactful.
"""

# ── Summary Command Prompt ───────────────────────────────────────────────────────

SUMMARY_COMMAND_PROMPT = """\
Based on the user's recent history and profile, generate a personal life summary.

Recent emotional trends:
{emotional_trends}

Semantic profile:
{profile}

Recent summaries:
{recent_summaries}

Recent episodes:
{recent_episodes}

Create a warm, insightful summary covering:
1. Current emotional state and recent mood patterns
2. Active goals and progress
3. Recurring concerns or stressors
4. Important recent events
5. Positive trends or areas of growth

Write it directly to the user in second person ("You've been..."). \
Keep it conversational and supportive. 5-8 sentences.
"""

# ── Diary Entry Analysis Prompt ──────────────────────────────────────────────────

DIARY_ANALYSIS_PROMPT = """\
Analyze this diary entry. Extract information as JSON only.
Entry: "{diary_text}"
Profile: {profile}

Response JSON structure:
{{
  "title": "<short 3-8 word title>",
  "detected_emotions": "<primary, secondary>",
  "emotion_confidence": <0.0-1.0>,
  "extracted_goals": ["<goals/ambitions>"],
  "extracted_stressors": ["<worries/stressors>"],
  "extracted_relationships": ["<names/relationships>"],
  "extracted_topics": ["<topics>"],
  "personality_signals": ["<observed traits>"],
  "behavioral_patterns": ["<observed behaviors>"],
  "importance_score": <0.0-1.0: 0.8+ major breaktrough/crisis; 0.5+ regular; 0.2+ surface>,
  "ai_summary": "<2-3 sentence emotional core summary>"
}}
Emotions: happy, sad, anxious, angry, excited, stressed, grateful, neutral, proud, lonely, hopeful, frustrated, calm, overwhelmed, nostalgic, confused, motivated, tired, burned out, numb, conflicted, content, fearful
"""

DIARY_FOLLOWUP_PROMPT = """\
You are a warm, reflective friend (not therapist). Respond to the user's diary entry.
Entry: "{diary_text}"
Emotions: {emotions} | Topics: {topics} | Stressors: {stressors} | Goals: {goals}
Profile: {profile}

Write a reply (under 100 words, plain text, no markdown) that:
1. Empathizes with their feelings.
2. Identifies patterns or connections to past experiences/goals (if relevant in profile).
3. Asks exactly ONE reflective follow-up question.
"""

DIARY_ENTRY_INTRO = (
    "📝 Diary Mode\n\n"
    "Write your diary entry for today. You can talk about your thoughts, "
    "emotions, stress, goals, experiences, relationships, fears, ambitions, "
    "or anything on your mind.\n\n"
    "Take your time — I'll read everything carefully, analyze your emotions "
    "and patterns, and remember it all permanently. 🌙"
)

# ── Mood Summary Prompt ──────────────────────────────────────────────────────────

MOOD_SUMMARY_PROMPT = """\
Analyze the user's emotional journey based on their diary entries and conversations.

Diary emotion timeline:
{diary_timeline}

Conversation emotion data:
{conversation_emotions}

User profile:
{profile}

Create a warm, insightful mood report covering:
1. Current emotional state (based on most recent entries)
2. Emotional trajectory over the observed period (improving, declining, stable, fluctuating)
3. Dominant recurring emotions
4. Identified emotional triggers or patterns
5. Positive trends worth celebrating
6. Concerns worth being mindful of

Write directly to the user in second person. Be specific and reference their \
actual experiences. Keep it under 200 words. Use plain text, no markdown.
"""

# ── Timeline Prompt ──────────────────────────────────────────────────────────────

TIMELINE_PROMPT = """\
Based on the user's high-importance diary entries, construct a life timeline.

Important events:
{events}

Present this as a chronological life timeline. For each event:
- Show the date
- Give a brief 1-sentence description
- Note the emotional tone

Format each event as:
📌 [Date] — [Brief description] ([emotional tone])

Keep entries concise. Show the most impactful moments. End with a brief \
reflective observation about the user's journey so far (1-2 sentences).
Use plain text, no markdown.
"""

# ── New Prompts for Companion humanisation and curation ────────────────────────────

KEYWORD_EXTRACTION_PROMPT = """\
Given this user message, extract 1-3 search terms or keywords (comma-separated) to search their past conversation history for relevant context.
Focus on nouns, names, specific events, feelings, or topics.

User message: "{user_message}"

Respond with ONLY the comma-separated terms. Do not include markdown, formatting, or quotes. E.g.: job interview, anxiety, boss
"""

RERANKING_PROMPT = """\
You are an episodic memory retrieval assistant. Your job is to select the top 3 most relevant past conversation turns (episodes) that are related to the user's current message, to help a companion bot respond with context.

User's current message: "{user_message}"

Candidate past episodes:
{candidates}

For each candidate, evaluate how relevant it is to the current message.
Respond with ONLY a JSON list of the IDs of the top 3 most relevant episodes, in order of relevance. E.g. [12, 45, 7]
If no candidates are relevant, respond with []. Do not include markdown formatting or extra text.
"""

MESSAGE_TYPE_CLASSIFIER_PROMPT = """\
Classify the user message into one of these types: [casual, emotional, reflective, question, task]

Definitions:
- casual: short texts, greetings, reactions, small talk, single-word or low-substance responses (e.g. "hi", "lol", "yeah", "cool", "okay").
- emotional: sharing feelings, expressing vulnerability, venting, talking about emotional events (e.g. "I'm so sad today", "my dog passed away").
- reflective: introspection, thinking deep thoughts, self-analysis ("I've been thinking about why I get anxious...").
- question: direct questions asking for information or thoughts (e.g. "what do you think I should do?", "why is the sky blue?").
- task: asking for help with something specific, writing/formatting text, calculations, brainstorming list ("can you write a poem about rain?", "help me plan my trip").

User message: "{user_message}"

Respond with ONLY the classification word: casual, emotional, reflective, question, or task. Do not include formatting, quotes, or markdown.
"""

CURATION_PROMPT = """\
You are a memory curation assistant for Eva, an AI companion. Below is the list of active memory items (facts, goals, stressors, relationships, traits) Eva knows about the user, along with recent summaries of their life.

Active memory items:
{memory_items}

Recent summaries of the user's life:
{recent_summaries}

Identify any memory items that have been completed, resolved, abandoned, or are no longer active.
- Active goals that are completed or abandoned
- Stressors that are resolved or no longer bothering the user
- Habits or routines that have been discontinued
- Facts/preferences that are outdated or replaced by newer information

Respond with ONLY a JSON object containing a list of IDs of the memory items that should be marked as resolved. E.g.
{{
  "resolved_ids": [4, 15]
}}
If no items are resolved, respond with {"resolved_ids": []}. Do not include markdown, comments, or extra text.
"""


# ── Session Compaction Prompt ────────────────────────────────────────────────────

SESSION_COMPACTION_PROMPT = """\
Analyze the following conversation turns from a single chat session.
Extract key insights to create a permanent session memory.

Conversation turns:
{episodes}

Respond with ONLY a JSON object (no markdown, no explanation):
{{
  "title": "<short 3-8 word title/topic of the session>",
  "summary": "<2-3 sentence compact summary of the conversation overview>",
  "emotion_metadata": {{
     "primary_emotion": "<dominant emotion>",
     "emotional_progression": "<e.g., anxious to calm, or happy throughout>",
     "intensity": <0.0-1.0>
  }},
  "important_memories": [
     {{
        "category": "<one of: interests, habits, favorite_topics, recurring_emotions, important_events, relationships, preferences, goals, stressors, fears, aspirations, strengths>",
        "content": "<specific fact or memory to extract about the user>",
        "importance": <0.0-1.0>
     }}
  ],
  "importance_score": <0.0-1.0>
}}

Guidelines for importance_score:
- 0.8-1.0: Deep personal sharing, major life updates, high emotional vulnerability, or critical events.
- 0.5-0.7: Conversational sharing, sharing interests, habits, minor updates or goals.
- 0.1-0.4: Surface-level casual chatting, greetings, or short checks.
"""



```

### 📄 `app/retrieval_engine.py`

```python
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
        
        score = vec_sim * 0.7 + (kw_overlap * 0.1)
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

async def build_context(user_id: int, current_message: str, user_info: dict | None = None, classification: str = "question") -> dict:
    db = get_db()
    llm = get_llm()
    
    # 1. Parallelize initial profile, history, and summary fetches
    tasks = [
        get_profile(user_id),
        db.get_recent_episodes(user_id, limit=4),
        db.get_recent_summaries(user_id, "weekly", limit=1),
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
    weekly_summaries = results[2]
    
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
            
        # Get query vector (already generated concurrently in the initial gather)
        
        if query_vector:
            # Parallelize vector searches and keyword searches on both episodes and sessions
            all_search_tasks = [
                get_vector_search_candidates(user_id, current_message, limit=10, query_vector=query_vector),
                db.get_vector_session_candidates(user_id, query_vector, limit=3)
            ]
            for kw in keywords:
                all_search_tasks.append(db.search_episodes(user_id, kw, limit=10))
                all_search_tasks.append(db.search_sessions(user_id, kw, limit=3))
                
            search_results = await asyncio.gather(*all_search_tasks)
            
            # Process episodes
            vector_episodes = search_results[0]
            vector_sessions = search_results[1]
            
            candidates = []
            seen_ids = set()
            recent_ids = {ep["id"] for ep in recent}
            
            for r in vector_episodes:
                if r["id"] not in seen_ids and r["id"] not in recent_ids:
                    seen_ids.add(r["id"])
                    candidates.append(r)
                    
            # Process keyword episodes
            for idx in range(2, len(search_results), 2):
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
                    
            for idx in range(3, len(search_results), 2):
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
                
    # Process weekly summary
    weekly_summary = weekly_summaries[0]["content"] if weekly_summaries else "No weekly summaries generated yet."
    
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
        "weekly_summary": weekly_summary,
        "pending_topics": pending_topics,
        "profile": profile,
    }

```

### 📄 `app/scheduler.py`

```python
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

```

### 📄 `app/semantic_engine.py`

```python
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

```

### 📄 `app/utils.py`

```python
"""
Utility classes and functions for the AI Diary Companion.
Includes LLM client with retry logic and summarization helpers.
"""

import asyncio
import json
import logging
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from openai import AsyncOpenAI

try:
    import redis.asyncio as aioredis
except ImportError:
    aioredis = None

from app.config import (
    OPENROUTER_API_KEY, LLM_BASE_URL, LLM_MODEL, 
    LLM_MAX_TOKENS, LLM_TEMPERATURE, LLM_RETRY_ATTEMPTS, 
    LLM_RETRY_BASE_DELAY, REDIS_URL
)
from app.prompts import DAILY_SUMMARY_PROMPT, WEEKLY_SUMMARY_PROMPT, MONTHLY_SUMMARY_PROMPT

logger = logging.getLogger(__name__)

class LLMClient:
    """Async LLM client with retry logic and structured output helpers."""

    def __init__(self) -> None:
        self._client = AsyncOpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=LLM_BASE_URL,
        )

    async def _call_with_retry(
        self,
        messages: list[dict],
        max_tokens: int | None = None,
        temperature: float | None = None,
    ) -> str:
        max_tokens = max_tokens or LLM_MAX_TOKENS
        temperature = temperature if temperature is not None else LLM_TEMPERATURE

        last_error = None
        for attempt in range(LLM_RETRY_ATTEMPTS):
            try:
                response = await self._client.chat.completions.create(
                    model=LLM_MODEL,
                    messages=messages,
                    max_completion_tokens=max_tokens,
                    temperature=temperature,
                )
                return response.choices[0].message.content or ""
            except Exception as exc:
                last_error = exc
                delay = LLM_RETRY_BASE_DELAY * (2 ** attempt)
                logger.warning("LLM call failed (attempt %d/%d): %s. Retrying...", attempt + 1, LLM_RETRY_ATTEMPTS, exc)
                await asyncio.sleep(delay)

        logger.error("LLM call failed after %d attempts: %s", LLM_RETRY_ATTEMPTS, last_error)
        raise last_error

    async def chat(
        self,
        system_prompt: str,
        user_message: str,
        history: list[dict] | None = None,
        max_tokens: int | None = None,
        temperature: float | None = None
    ) -> str:
        messages = [{"role": "system", "content": system_prompt}]
        if history:
            messages.extend(history)
        messages.append({"role": "user", "content": user_message})
        return await self._call_with_retry(messages, max_tokens=max_tokens, temperature=temperature)

    async def _call_json(self, prompt: str, max_tokens: int = 400, temperature: float = 0.3) -> dict | None:
        messages = [
            {"role": "system", "content": "You are a precise JSON-only assistant. Respond with ONLY valid JSON, no markdown fences."},
            {"role": "user", "content": prompt},
        ]
        try:
            raw = await self._call_with_retry(messages, max_tokens=max_tokens, temperature=temperature)
            cleaned = re.sub(r"^```(?:json)?\s*", "", raw.strip())
            cleaned = re.sub(r"\s*```$", "", cleaned)
            return json.loads(cleaned)
        except Exception as exc:
            logger.warning("Failed to parse JSON: %s", exc)
            return None

    async def analyze_emotion(self, prompt: str) -> dict | None:
        return await self._call_json(prompt, max_tokens=300, temperature=0.2)

    async def extract_profile(self, prompt: str) -> dict | None:
        return await self._call_json(prompt, max_tokens=500, temperature=0.2)

    async def generate_summary(self, prompt: str) -> str:
        messages = [
            {"role": "system", "content": "You are a concise, insightful summarizer."},
            {"role": "user", "content": prompt},
        ]
        return await self._call_with_retry(messages, max_tokens=600, temperature=0.5)

    async def extract_single_fact(self, fact_type: str, text: str) -> str:
        # Try deterministic regex extraction first to avoid LLM latency
        text_clean = text.strip()
        if "name" in fact_type.lower():
            match = re.search(r"\b(?:my name is|i am|i'm|call me)\s+([A-Za-z]+)", text_clean, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            words = text_clean.split()
            if len(words) <= 2 and all(w.isalpha() for w in words):
                return text_clean
        elif "age" in fact_type.lower():
            match = re.search(r"\b\d+\b", text_clean)
            if match:
                return match.group(0)
        elif "nationality" in fact_type.lower():
            match = re.search(r"\b(?:i am|i'm)\s+([A-Za-z]+)", text_clean, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            words = text_clean.split()
            if len(words) <= 2 and all(w.isalpha() for w in words):
                return text_clean
        elif "city" in fact_type.lower():
            match = re.search(r"\b(?:in|at|live in)\s+([A-Za-z\s]+)", text_clean, re.IGNORECASE)
            if match:
                return match.group(1).strip()
            words = text_clean.split()
            if len(words) <= 2 and all(w.isalpha() for w in words):
                return text_clean

        prompt = f"""\
Extract the {fact_type} from the user's message.
User message: "{text}"

Respond with ONLY the extracted value. If you cannot extract it, respond with "Unknown".
Do not include any other text, quotes, or markdown.
"""
        res = await self.chat("You are a precise facts extraction assistant.", prompt, max_tokens=25)
        return res.strip()

    async def embed_text(self, text: str) -> list[float] | None:
        try:
            response = await self._client.embeddings.create(
                model="text-embedding-3-small",
                input=text
            )
            return response.data[0].embedding
        except Exception as e:
            logger.warning("Failed to generate embedding: %s. Using mock embedding.", e)
            import hashlib
            h = hashlib.sha256(text.encode('utf-8')).digest()
            mock_vec = []
            for i in range(1536):
                val = ((h[i % 32] * (i + 1)) % 1000) / 500.0 - 1.0
                mock_vec.append(val)
            return mock_vec

_llm_instance: LLMClient | None = None

def get_llm() -> LLMClient:
    global _llm_instance
    if _llm_instance is None:
        _llm_instance = LLMClient()
    return _llm_instance

# --- Summarization Logic (Moved from summarizer.py) ---

def format_episodes_for_summary(episodes: list[dict]) -> str:
    if not episodes: return "(no conversations)"
    lines = []
    for ep in episodes:
        ts = ep.get("timestamp", "")[:16].replace("T", " ")
        emotion = ep.get("detected_emotion", "")
        lines.append(f"[{ts}] [{emotion}]\n  User: {ep.get('user_message')}\n  Assistant: {ep.get('bot_response')[:200]}")
    return "\n".join(lines)

async def check_and_generate_summaries(user_id: int):
    from app.database import get_db
    db = get_db()
    llm = get_llm()
    now = datetime.now()
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    # Generate embeddings for episodes that lack them
    try:
        rows = await db.fetch("SELECT id, user_message FROM episodes WHERE user_id = ? AND embedding IS NULL", user_id)
        for r in rows:
            eid = r["id"]
            msg = r["user_message"]
            vec = await llm.embed_text(msg)
            if vec:
                await db.update_episode_embedding(user_id, eid, vec)
    except Exception as e:
        logger.error("Failed to generate daily episode embeddings: %s", e)

    # Daily Summary
    existing_daily = await db.get_recent_summaries(user_id, "daily", limit=1)
    if not existing_daily or existing_daily[0].get("period_start", "")[:10] < yesterday:
        start, end = f"{yesterday}T00:00:00", f"{yesterday}T23:59:59"
        episodes = await db.get_episodes_by_date_range(user_id, start, end)
        if episodes:
            text = format_episodes_for_summary(episodes)
            prompt = DAILY_SUMMARY_PROMPT.format(date=yesterday, episodes=text)
            summary = await llm.generate_summary(prompt)
            await db.save_summary(user_id, "daily", start, end, summary)
            logger.info("Daily summary generated for user %d", user_id)

    # Weekly Summary
    existing_weekly = await db.get_recent_summaries(user_id, "weekly", limit=1)
    all_dailies = await db.get_recent_summaries(user_id, "daily", limit=30)
    if all_dailies:
        cutoff = existing_weekly[0]["period_end"] if existing_weekly else "1970-01-01"
        uncovered_dailies = [d for d in all_dailies if d["period_end"] > cutoff]
        if uncovered_dailies:
            first_uncovered_start = datetime.fromisoformat(uncovered_dailies[-1]["period_start"])
            if (datetime.now() - first_uncovered_start).days >= 7:
                window_end = first_uncovered_start + timedelta(days=7)
                window_end_str = window_end.isoformat()
                dailies_in_window = [d for d in uncovered_dailies if d["period_start"] <= window_end_str]
                
                daily_texts = []
                for d in reversed(dailies_in_window):
                    daily_texts.append(f"[{d['period_start'][:10]}]: {d['content']}")
                daily_summaries_str = "\n\n".join(daily_texts)
                
                prompt = WEEKLY_SUMMARY_PROMPT.format(daily_summaries=daily_summaries_str)
                summary = await llm.generate_summary(prompt)
                
                start_str = dailies_in_window[-1]["period_start"]
                end_str = dailies_in_window[0]["period_end"]
                await db.save_summary(user_id, "weekly", start_str, end_str, summary)
                logger.info("Weekly summary generated for user %d from %s to %s", user_id, start_str, end_str)

    # Monthly Summary
    existing_monthly = await db.get_recent_summaries(user_id, "monthly", limit=1)
    all_weeklies = await db.get_recent_summaries(user_id, "weekly", limit=12)
    if all_weeklies:
        cutoff = existing_monthly[0]["period_end"] if existing_monthly else "1970-01-01"
        uncovered_weeklies = [w for w in all_weeklies if w["period_end"] > cutoff]
        if uncovered_weeklies:
            first_uncovered_start = datetime.fromisoformat(uncovered_weeklies[-1]["period_start"])
            if (datetime.now() - first_uncovered_start).days >= 30:
                window_end = first_uncovered_start + timedelta(days=30)
                window_end_str = window_end.isoformat()
                weeklies_in_window = [w for w in uncovered_weeklies if w["period_start"] <= window_end_str]
                
                weekly_texts = []
                for w in reversed(weeklies_in_window):
                    weekly_texts.append(f"[{w['period_start'][:10]} to {w['period_end'][:10]}]: {w['content']}")
                weekly_summaries_str = "\n\n".join(weekly_texts)
                
                prompt = MONTHLY_SUMMARY_PROMPT.format(weekly_summaries=weekly_summaries_str)
                summary = await llm.generate_summary(prompt)
                
                start_str = weeklies_in_window[-1]["period_start"]
                end_str = weeklies_in_window[0]["period_end"]
                await db.save_summary(user_id, "monthly", start_str, end_str, summary)
                logger.info("Monthly summary generated for user %d from %s to %s", user_id, start_str, end_str)


class UserSessionManager:
    _instance = None

    def __new__(cls) -> "UserSessionManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._locks = {}
            cls._instance._lock_count = {}
            cls._instance._global_lock = asyncio.Lock()
            
            if REDIS_URL and aioredis:
                logger.info("Initializing Redis connection pool for distributed locks")
                cls._instance._redis = aioredis.from_url(REDIS_URL, decode_responses=True)
            else:
                logger.info("Using in-memory lock manager (no Redis configured)")
                cls._instance._redis = None
        return cls._instance

    @asynccontextmanager
    async def lock_user(self, user_id: int):
        if self._redis is None:
            async with self._global_lock:
                if user_id not in self._locks:
                    self._locks[user_id] = asyncio.Lock()
                    self._lock_count[user_id] = 0
                self._lock_count[user_id] += 1
                lock = self._locks[user_id]

            async with lock:
                try:
                    yield
                finally:
                    async with self._global_lock:
                        self._lock_count[user_id] -= 1
                        if self._lock_count[user_id] == 0:
                            self._locks.pop(user_id, None)
                            self._lock_count.pop(user_id, None)
            return

        lock_key = f"lock:user:{user_id}"
        token = str(uuid.uuid4())
        acquired = False
        retry_delay = 0.1
        max_attempts = 150  # 15 seconds
        
        for _ in range(max_attempts):
            res = await self._redis.set(lock_key, token, nx=True, px=30000)
            if res:
                acquired = True
                break
            await asyncio.sleep(retry_delay)
            
        if not acquired:
            raise TimeoutError(f"Could not acquire Redis lock for user {user_id}")
            
        try:
            yield
        finally:
            val = await self._redis.get(lock_key)
            if val == token:
                await self._redis.delete(lock_key)


_session_manager_instance: UserSessionManager | None = None


def get_session_manager() -> UserSessionManager:
    global _session_manager_instance
    if _session_manager_instance is None:
        _session_manager_instance = UserSessionManager()
    return _session_manager_instance

_cache_instance = None


class CacheManager:
    def __init__(self):
        self._local_cache = {}
        self._local_lock = asyncio.Lock()

    @property
    def redis(self):
        return get_session_manager()._redis

    async def get(self, key: str):
        r = self.redis
        if r is not None:
            try:
                val = await r.get(key)
                return json.loads(val) if val else None
            except Exception as e:
                logger.error("Redis get failed for key %s: %s", key, e)
        
        async with self._local_lock:
            if key in self._local_cache:
                val, expire_time = self._local_cache[key]
                if datetime.now() < expire_time:
                    return val
                else:
                    del self._local_cache[key]
        return None

    async def set(self, key: str, value, ttl: int = 300):
        r = self.redis
        if r is not None:
            try:
                await r.set(key, json.dumps(value), ex=ttl)
                return
            except Exception as e:
                logger.error("Redis set failed for key %s: %s", key, e)
        
        async with self._local_lock:
            expire_time = datetime.now() + timedelta(seconds=ttl)
            self._local_cache[key] = (value, expire_time)

    async def delete(self, key: str):
        r = self.redis
        if r is not None:
            try:
                await r.delete(key)
                return
            except Exception as e:
                logger.error("Redis delete failed for key %s: %s", key, e)
        
        async with self._local_lock:
            self._local_cache.pop(key, None)

    async def delete_pattern(self, pattern: str):
        r = self.redis
        if r is not None:
            try:
                keys = await r.keys(pattern)
                if keys:
                    await r.delete(*keys)
                return
            except Exception as e:
                logger.error("Redis delete_pattern failed for pattern %s: %s", pattern, e)
        
        async with self._local_lock:
            import fnmatch
            keys_to_del = [k for k in self._local_cache.keys() if fnmatch.fnmatch(k, pattern)]
            for k in keys_to_del:
                self._local_cache.pop(k, None)


def get_cache() -> CacheManager:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = CacheManager()
    return _cache_instance



async def is_duplicate_update(update_id: int) -> bool:
    """Check if the update has already been processed using Redis cache."""
    manager = get_session_manager()
    if manager._redis is None:
        return False
        
    key = f"update:{update_id}"
    res = await manager._redis.set(key, "1", nx=True, ex=3600)
    return not res


async def log_unusual_traffic(user_id: int) -> int | None:
    """Increment rate counter in Redis and return count if exceeding threshold."""
    manager = get_session_manager()
    if manager._redis is None:
        return None
    try:
        key = f"traffic:user:{user_id}"
        pipe = manager._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, 60)
        res = await pipe.execute()
        count = res[0]
        if count > 20:
            return count
    except Exception as e:
        logger.error("Failed to update traffic logs in Redis: %s", e)
    return None


async def check_rate_limit(user_id: int, action: str, limit: int, window: int) -> bool:
    """
    Check if user has exceeded the rate limit for a specific action.
    Returns True if allowed (limit not exceeded), False if rate-limited (limit exceeded).
    """
    manager = get_session_manager()
    if manager._redis is None:
        return True  # Fallback to allow if Redis is not configured (local dev)
        
    key = f"ratelimit:{action}:{user_id}"
    try:
        pipe = manager._redis.pipeline()
        pipe.incr(key)
        pipe.expire(key, window)
        res = await pipe.execute()
        count = res[0]
        if count > limit:
            return False
    except Exception as e:
        logger.error("Rate limit check failed for user %d: %s", user_id, e)
        return True  # Fail-open
    return True




```

### 📄 `app/webhook.py`

```python
"""
FastAPI webhook server for Telegram Bot.
Handles lifecycle events and update routing.
"""

import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from telegram import Update

from app.config import WEBHOOK_URL, WEBHOOK_SECRET, PORT
from app.bot import build_ptb_application
from app.database import get_db

logger = logging.getLogger(__name__)

# Global bot application
ptb_app = build_ptb_application()

@asynccontextmanager
async def lifecycle(app: FastAPI):
    """Manage application startup and shutdown."""
    # STARTUP
    logger.info("Starting up...")
    await get_db().initialize()
    
    # Initialize bot
    await ptb_app.initialize()
    
    # Set webhook
    if WEBHOOK_URL:
        webhook_path = f"{WEBHOOK_URL}/webhook"
        logger.info("Setting webhook to %s", webhook_path)
        await ptb_app.bot.set_webhook(
            url=webhook_path,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True
        )
    else:
        logger.info("WEBHOOK_URL not set. Falling back to POLLING mode...")
        await ptb_app.updater.start_polling()

    # Run bot start logic
    await ptb_app.start()
    
    yield
    
    # SHUTDOWN
    logger.info("Shutting down...")
    if not WEBHOOK_URL:
        await ptb_app.updater.stop()
    await ptb_app.stop()
    await ptb_app.shutdown()
    await get_db().close()

# Create FastAPI app
app = FastAPI(lifespan=lifecycle)

@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    client_host = request.client.host if request.client else "unknown"
    logger.error("Unhandled API error from IP %s during request to %s: %s", 
                 client_host, request.url.path, exc, exc_info=True)
    return JSONResponse(
        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
        content={"detail": "Internal Server Error"}
    )

@app.get("/")
async def index():
    return {"status": "online", "message": "AI Diary Assistant is running."}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.post("/webhook")
async def webhook(request: Request):
    """Handle incoming Telegram updates."""
    client_host = request.client.host if request.client else "unknown"
    # Verify secret token
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token")
    if token != WEBHOOK_SECRET:
        logger.warning("Unauthorized webhook attempt from IP %s with invalid secret", client_host)
        return Response(status_code=status.HTTP_403_FORBIDDEN)

    try:
        data = await request.json()
        update = Update.de_json(data, ptb_app.bot)
        
        # Check unusual traffic patterns
        user_id = None
        if update.effective_user:
            user_id = update.effective_user.id
        if user_id is not None:
            from app.utils import log_unusual_traffic
            traffic_count = await log_unusual_traffic(user_id)
            if traffic_count is not None:
                logger.warning("Suspicious traffic pattern detected: user %d sent %d requests in the last 60 seconds from IP %s", user_id, traffic_count, client_host)
        
        await ptb_app.process_update(update)
    except Exception as e:
        logger.error("Error processing update from IP %s: %s", client_host, e)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(status_code=status.HTTP_200_OK)

```

### 📄 `Dockerfile`

```dockerfile
# Build stage
FROM python:3.11-slim as builder

WORKDIR /app

# Install build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# Final stage
FROM python:3.11-slim

WORKDIR /app

# Copy installed dependencies
COPY --from=builder /install /usr/local

# Copy application code
COPY . .

# Create persistent directories (should be mapped to volumes in Railway)
RUN mkdir -p data backups logs exports && \
    chmod -R 777 data backups logs exports

# Environment variables
ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
ENV PORT=8000

# Expose port
EXPOSE 8000

# Run the application
CMD ["python", "main.py"]

```

### 📄 `main.py`

```python
"""
Production entry point for the AI Diary Companion.
Starts the FastAPI server via uvicorn.
"""

import uvicorn
import logging
from app.config import PORT, validate_config
from app.logging_config import setup_logging

# Initialize logging first
setup_logging()
logger = logging.getLogger(__name__)

def main():
    try:
        # Validate environment
        validate_config()
        
        logger.info("Starting server on port %d", PORT)
        
        # Start uvicorn
        # In production, uvicorn app/webhook:app is usually run via Docker CMD
        # but having a python entrypoint is good for local dev and Railway.
        uvicorn.run(
            "app.webhook:app",
            host="0.0.0.0",
            port=PORT,
            log_level="info",
            proxy_headers=True,
            forwarded_allow_ips="*"
        )
    except Exception as e:
        logger.critical("Failed to start application: %s", e)
        exit(1)

if __name__ == "__main__":
    main()

```

### 📄 `pyproject.toml`

```toml
[project]
name = "telegram-memory-bot"
version = "2.0.0"
description = "AI Diary Companion — persistent memory, daily check-ins, emotional intelligence"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "openai>=2.36.0",
    "python-dotenv>=1.2.2",
    "python-telegram-bot>=22.7",
    "aiosqlite>=0.21.0",
    "apscheduler>=3.11.0",
    "aiofiles>=24.1.0",
    "asyncpg>=0.31.0",
    "qstash>=3.4.0",
    "fastapi>=0.136.3",
    "uvicorn>=0.48.0",
    "httpx>=0.28.1",
    "python-multipart>=0.0.29",
    "redis>=8.0.0",
]

```

### 📄 `railway.json`

```json
{
  "$schema": "https://railway.app/railway.schema.json",
  "build": {
    "builder": "DOCKERFILE",
    "dockerfilePath": "Dockerfile"
  },
  "deploy": {
    "numReplicas": 1,
    "restartPolicyType": "ON_FAILURE",
    "restartPolicyMaxRetries": 10,
    "healthcheckPath": "/health",
    "healthcheckTimeout": 5,
    "healthcheckInterval": 30
  }
}

```

### 📄 `README.md`

```markdown
# AI Diary Companion 🌙📓

A deeply personalized, persistent AI diary and long-term advisor on Telegram. Unlike standard chatbots, this companion uses a multi-layered memory system to remember your emotions, goals, stressors, and life events, providing context-aware advice and reflective journaling support.

## ✨ Features

### 🧠 Advanced Multi-Layered Memory
- **Episodic Memory**: Every conversation is stored permanently.
- **Semantic Profile**: Automatically builds and updates a structured profile of who you are (goals, habits, stressors, relationships).
- **Diary Entry System**: Dedicated long-form reflective journaling with deep AI analysis.
- **Rolling Summaries**: Generates daily, weekly, and monthly summaries of your life.
- **Emotional Intelligence**: Detects 18+ emotional states and tracks patterns over time.

### 📝 Reflective Journaling
- **Structured Entries**: Use `/diary` to write deep reflections that are analyzed for meaning and importance.
- **Importance Scoring**: AI identifies major life milestones and breakthroughs.
- **AI Follow-ups**: Receive warm, empathetic, and reflective questions after every entry.

### 📊 Insights & Reflection
- **Context-Aware Advisor**: The bot uses all historical context to give deeply personalized advice.
- **Daily Check-ins**: Scheduled reminders to encourage consistent journaling.

### 🔒 Privacy & Resilience
- **SQLite WAL Storage**: Transactional database ensures zero data loss even during crashes.
- **Automated Backups**: Periodic snapshots of your entire memory database.
- **Data Export**: Export your entire history in readable JSON or Markdown formats with `/export`.
- **Self-Destruct**: Total data wipe available via `/clear`.

## 🛠️ Tech Stack

| Layer | Tool |
|---|---|
| **Bot Framework** | `python-telegram-bot` v21+ |
| **LLM Gateway** | [OpenRouter](https://openrouter.ai/) (Gemini 2.5 Flash / Pro) |
| **Database** | SQLite (WAL Mode) with `aiosqlite` |
| **Scheduling** | `APScheduler` |
| **Package Manager** | `uv` |
| **Language** | Python 3.12+ |

## 🚀 Quick Start

### 1. Prerequisites
- [uv](https://github.com/astral-sh/uv) installed.
- A Telegram bot token from [@BotFather](https://t.me/botfather).
- An API key from [OpenRouter](https://openrouter.ai/).

### 2. Setup
Clone the repository and install dependencies:
```bash
uv sync
```

### 3. Configuration
Create a `.env` file in the root directory:
```env
TELEGRAM_BOT_TOKEN=your_telegram_token
OPENROUTER_API_KEY=your_openrouter_key
# Optional: Set preferred model
# LLM_MODEL=google/gemini-2.0-flash-001
```

### 4. Run the Bot
```bash
uv run python bot.py
```

## 🎮 Commands

| Command | Description |
|---|---|
| `/start` | Start onboarding or greet Eva 🌙 |
| `/diary` | Write a new diary entry 📓 |
| `/entries` | View your past diary entries |
| `/chats` | View your chat history and sessions 💬 |
| `/memory` | View or delete things Eva remembers about you 🧠 |
| `/settime` | Configure daily reminder check-in time ⏰ |
| `/clear` | Clear all your data permanently ⚠️ |
| `/reboot` | Wipe everything and restart onboarding 🔄 |
| `/export` | Export your companion history and memories 📦 |
| `/commands` | List all available commands 📋 |

## 📂 Project Structure

- `app/bot.py`: Main Telegram handlers and message routing.
- `app/database.py`: Core SQLite logic and schema management.
- `app/diary_engine.py`: Deep analysis pipeline for diary entries.
- `app/export_engine.py`: Data export functionality.
- `app/memory_engine.py`: Emotion analysis and session memory operations.
- `app/retrieval_engine.py`: Hybrid memory retrieval.
- `app/scheduler.py`: Daily reminders and periodic backups.
- `app/semantic_engine.py`: User profile extraction and storage.
- `app/webhook.py`: FastAPI webhook server for Telegram Bot.
- `app/utils.py`: Resilient async LLM interface and utilities.

## ⚙️ Customization
Edit `config.py` to adjust:
- Memory context window size.
- Emotional analysis sensitivity.
- Default check-in times and timezones.
- Backup intervals.

## 📜 License
MIT

```

### 📄 `requirements.txt`

```
python-telegram-bot>=22.7
fastapi>=0.110.0
uvicorn>=0.28.0
aiosqlite>=0.21.0
openai>=1.14.0
python-dotenv>=1.0.1
httpx>=0.27.0
python-multipart>=0.0.12
asyncpg>=0.29.0
redis>=5.0.3
qstash>=3.4.0

```

### 📄 `tests/test_export.py`

```python
import os
import json
import zipfile
from pathlib import Path
from datetime import datetime
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock

# Override DB_PATH to use a separate test database
import app.config
TEST_DB_PATH = Path(app.config.DATA_DIR) / "test_export_diary.db"
app.config.DB_PATH = TEST_DB_PATH
app.config.DATABASE_URL = ""

from app.database import get_db
from app.export_engine import parse_export_arguments, generate_export
from app.bot import export_handler

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:
            pass
            
    db = get_db()
    db._initialized = False
    await db.initialize()
    
    yield
    
    await db.close()
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:
            pass

def test_parse_export_arguments():
    # 1. Default (no args)
    opt = parse_export_arguments([])
    assert opt["format"] == "json"
    assert opt["zip"] is False
    assert len(opt["categories"]) == 0
    assert opt["session_id"] is None
    
    # 2. Format options
    opt = parse_export_arguments(["format=md"])
    assert opt["format"] == "md"
    
    opt = parse_export_arguments(["format=markdown"])
    assert opt["format"] == "md"
    
    opt = parse_export_arguments(["format=txt"])
    assert opt["format"] == "txt"
    
    # 3. Zip options
    opt = parse_export_arguments(["zip"])
    assert opt["zip"] is True
    
    opt = parse_export_arguments(["format=zip"])
    assert opt["zip"] is True
    
    # 4. Dates
    opt = parse_export_arguments(["2026-05-20"])
    assert opt["start_date"] == "2026-05-20"
    assert opt["end_date"] == "2026-05-20"
    
    opt = parse_export_arguments(["2026-05-20", "to", "2026-05-25"])
    assert opt["start_date"] == "2026-05-20"
    assert opt["end_date"] == "2026-05-25"
    
    # 5. Session ID
    opt = parse_export_arguments(["session_12345"])
    assert opt["session_id"] == "session_12345"
    
    # 6. Categories
    opt = parse_export_arguments(["diary", "memory", "format=txt"])
    assert "diary" in opt["categories"]
    assert "memory" in opt["categories"]
    assert opt["format"] == "txt"

@pytest.mark.asyncio
async def test_generate_export_full_json():
    db = get_db()
    user_id = 9999
    
    # Setup mock user data
    await db.ensure_user(user_id, "export_guy", "Guy")
    await db.update_onboarding_data(user_id, onboarding_status="completed")
    
    # Add a session, episodes, and diary entries
    session_id = "sess_export_test"
    await db.create_session(session_id, user_id, "2026-05-29T10:00:00", "2026-05-29")
    await db.save_episode(user_id, "Hello Eva", "Hii there!", session_id=session_id)
    await db.save_diary_entry(user_id, "Today was great.", title="Export Entry")
    await db.upsert_memory_item(user_id, "interests", "exporting tests")
    
    # Perform full export
    options = parse_export_arguments([])
    file_path, file_name = await generate_export(user_id, options)
    
    assert os.path.exists(file_path)
    assert file_name.endswith(".json")
    
    with open(file_path, "r", encoding="utf-8") as f:
        export_data = json.load(f)
        
    # Verify JSON structure
    assert export_data["export_metadata"]["user_id"] == user_id
    assert export_data["profile"]["name"] == "Guy"
    assert len(export_data["sessions"]) == 1
    assert export_data["sessions"][0]["session_id"] == session_id
    assert len(export_data["sessions"][0]["messages"]) == 2
    assert export_data["sessions"][0]["messages"][0]["content"] == "Hello Eva"
    assert len(export_data["diary"]) == 1
    assert export_data["diary"][0]["title"] == "Export Entry"
    assert len(export_data["memories"]) == 1
    assert export_data["memories"][0]["content"] == "exporting tests"
    
    # Cleanup
    os.remove(file_path)

@pytest.mark.asyncio
async def test_export_security_isolation():
    db = get_db()
    user_a = 777
    user_b = 888
    
    # Setup User A and User B
    await db.ensure_user(user_a, "user_a", "Alice")
    await db.ensure_user(user_b, "user_b", "Bob")
    
    session_a = "sess_alice"
    session_b = "sess_bob"
    await db.create_session(session_a, user_a, "2026-05-29T10:00:00", "2026-05-29")
    await db.create_session(session_b, user_b, "2026-05-29T10:00:00", "2026-05-29")
    
    await db.save_episode(user_a, "Alice message", "Reply A", session_id=session_a)
    await db.save_episode(user_b, "Bob message", "Reply B", session_id=session_b)
    
    # Scenario 1: User A requests their own sessions
    opt_a = parse_export_arguments([])
    file_path_a, _ = await generate_export(user_a, opt_a)
    with open(file_path_a, "r", encoding="utf-8") as f:
        data_a = json.load(f)
    assert len(data_a["sessions"]) == 1
    assert data_a["sessions"][0]["session_id"] == session_a
    os.remove(file_path_a)
    
    # Scenario 2: User A attempts to export Bob's session_id
    opt_steal = parse_export_arguments([session_b])
    file_path_steal, _ = await generate_export(user_a, opt_steal)
    with open(file_path_steal, "r", encoding="utf-8") as f:
        data_steal = json.load(f)
    # Bob's session should NOT be retrieved since User A does not own it (Ownership validation check)
    assert len(data_steal["sessions"]) == 0
    os.remove(file_path_steal)

@pytest.mark.asyncio
async def test_export_formatters_and_zip():
    db = get_db()
    user_id = 555
    
    await db.ensure_user(user_id, "format_tester", "Frank")
    await db.save_diary_entry(user_id, "Testing format export.", title="Formatter Test")
    
    # 1. TXT Export
    opt_txt = parse_export_arguments(["format=txt"])
    file_path_txt, name_txt = await generate_export(user_id, opt_txt)
    assert os.path.exists(file_path_txt)
    assert name_txt.endswith(".txt")
    with open(file_path_txt, "r", encoding="utf-8") as f:
        content = f.read()
        assert "Formatter Test" in content
        assert "Testing format export." in content
    os.remove(file_path_txt)
    
    # 2. Markdown Export
    opt_md = parse_export_arguments(["format=md"])
    file_path_md, name_md = await generate_export(user_id, opt_md)
    assert os.path.exists(file_path_md)
    assert name_md.endswith(".md")
    with open(file_path_md, "r", encoding="utf-8") as f:
        content = f.read()
        assert "## Diary Entries" in content
        assert "Formatter Test" in content
    os.remove(file_path_md)
    
    # 3. Zipped Export
    opt_zip = parse_export_arguments(["zip"])
    file_path_zip, name_zip = await generate_export(user_id, opt_zip)
    assert os.path.exists(file_path_zip)
    assert name_zip.endswith(".zip")
    assert zipfile.is_zipfile(file_path_zip)
    
    # Verify contents of zip file
    with zipfile.ZipFile(file_path_zip, "r") as zf:
        namelist = zf.namelist()
        assert len(namelist) == 1
        assert namelist[0].endswith(".json")
        
    os.remove(file_path_zip)

```

### 📄 `tests/test_session_memory.py`

```python
import asyncio
import json
import os
from pathlib import Path
from datetime import datetime, timedelta
import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock, patch

# Override DB_PATH to use a separate test database
import app.config
TEST_DB_PATH = Path(app.config.DATA_DIR) / "test_session_diary.db"
app.config.DB_PATH = TEST_DB_PATH
app.config.DATABASE_URL = ""

from app.database import get_db
from app.utils import get_llm
from app.memory_engine import compact_session, compact_uncompacted_sessions
from app.retrieval_engine import build_context
from app.bot import message_handler

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:
            pass
            
    db = get_db()
    db._initialized = False
    await db.initialize()
    
    yield
    
    await db.close()
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:
            pass

@pytest.fixture
def mock_llm():
    mock_client = MagicMock()
    mock_client.chat = AsyncMock(return_value="Mocked response from Eva")
    mock_client.analyze_emotion = AsyncMock(return_value={
        "emotion": "happy",
        "confidence": 0.9,
        "secondary_emotion": None,
        "topics": ["coding"]
    })
    mock_client.extract_profile = AsyncMock(return_value={"no_update": True})
    
    # Mocking _call_json for session compaction format
    compaction_data = {
        "title": "Discussing Python and Tests",
        "summary": "The user talked about writing python tests for a session memory system.",
        "emotion_metadata": {
            "primary_emotion": "happy",
            "emotional_progression": "curious to happy",
            "intensity": 0.8
        },
        "important_memories": [
            {
                "category": "interests",
                "content": "programming in python",
                "importance": 0.9
            },
            {
                "category": "goals",
                "content": "implementing session memory system",
                "importance": 0.85
            }
        ],
        "importance_score": 0.8
    }
    mock_client._call_json = AsyncMock(return_value=compaction_data)
    mock_client.embed_text = AsyncMock(return_value=[0.1] * 1536)
    
    with patch("app.utils._llm_instance", mock_client):
        yield mock_client

@pytest.mark.asyncio
async def test_session_segmentation_lifecycle(mock_llm):
    db = get_db()
    user_id = 12345
    
    # Ensure user exists
    await db.ensure_user(user_id, "test_user", "Charlie")
    await db.update_onboarding_data(user_id, onboarding_status="completed")
    
    # Mock update message and context
    update = MagicMock()
    update.effective_user.id = user_id
    update.effective_user.username = "test_user"
    update.effective_user.first_name = "Charlie"
    update.message.text = "Hello Eva, I am working on python code today."
    update.message.reply_text = AsyncMock()
    update.message.reply_chat_action = AsyncMock()
    
    context = MagicMock()
    
    # Send first message -> Should create first session
    await message_handler(update, context)
    await asyncio.sleep(0.1)
    
    user_info = await db.get_user(user_id)
    first_session_id = user_info["current_session_id"]
    assert first_session_id is not None
    
    # Verify episode was saved under first session
    episodes = await db.get_episodes_for_session(user_id, first_session_id)
    assert len(episodes) == 1
    assert episodes[0]["user_message"] == "Hello Eva, I am working on python code today."
    
    # Send second message immediately -> Should reuse the same session
    update.message.text = "I also like testing."
    await message_handler(update, context)
    await asyncio.sleep(0.1)
    
    user_info = await db.get_user(user_id)
    assert user_info["current_session_id"] == first_session_id
    
    episodes = await db.get_episodes_for_session(user_id, first_session_id)
    assert len(episodes) == 2
    
    # Update last_seen to be 31 minutes ago to trigger segmentation
    last_seen_dt = datetime.now() - timedelta(minutes=31)
    await db.update_user(user_id, last_seen=last_seen_dt.isoformat())
    
    # Send third message -> Should spawn a new session and end the old one
    update.message.text = "Now I am doing something else."
    await message_handler(update, context)
    await asyncio.sleep(0.1)
    
    user_info = await db.get_user(user_id)
    second_session_id = user_info["current_session_id"]
    assert second_session_id is not None
    assert second_session_id != first_session_id
    
    # Check that the old session has an end_time set
    old_session = await db.get_session(user_id, first_session_id)
    assert old_session["end_time"] is not None
    
    # Check that new episode is in the second session
    new_episodes = await db.get_episodes_for_session(user_id, second_session_id)
    assert len(new_episodes) == 1
    assert new_episodes[0]["user_message"] == "Now I am doing something else."

@pytest.mark.asyncio
async def test_session_compaction_and_summarization(mock_llm):
    db = get_db()
    user_id = 67890
    session_id = "test-session-uuid"
    
    await db.ensure_user(user_id, "compactor_user", "Alice")
    await db.create_session(session_id, user_id, datetime.now().isoformat(), datetime.now().strftime("%Y-%m-%d"))
    
    # Save a couple of episodes under this session
    await db.save_episode(user_id, "I love coding in Python.", "That's great!", session_id=session_id)
    await db.save_episode(user_id, "It is very fun.", "Indeed.", session_id=session_id)
    
    # Run compaction
    await compact_session(user_id, session_id)
    
    # Verify session fields in database
    session = await db.get_session(user_id, session_id)
    assert session["title"] == "Discussing Python and Tests"
    assert session["summary"] == "The user talked about writing python tests for a session memory system."
    assert round(session["importance_score"], 2) == 0.8
    assert session["emotion_metadata"]["primary_emotion"] == "happy"
    assert len(session["memories"]) == 2
    assert session["embedding"] is not None
    
    # Verify memories were upserted to memory_items
    memories = await db.get_active_memories(user_id)
    assert len(memories) >= 2
    categories = {m["category"] for m in memories}
    contents = {m["content"] for m in memories}
    assert "interests" in categories
    assert "goals" in categories
    assert "programming in python" in contents

@pytest.mark.asyncio
async def test_hybrid_session_retrieval(mock_llm):
    db = get_db()
    user_id = 54321
    session_id_1 = "session-1"
    session_id_2 = "session-2"
    
    await db.ensure_user(user_id, "retriever_user", "Bob")
    await db.update_onboarding_data(user_id, onboarding_status="completed")
    
    # Save compacted sessions manually to test retrieval
    await db.create_session(session_id_1, user_id, datetime.now().isoformat(), "2026-05-29")
    await db.update_session(
        user_id=user_id,
        session_id=session_id_1,
        title="Python testing",
        summary="Bob works on building test suites for databases.",
        emotion_metadata={"primary_emotion": "neutral"},
        memories=[{"category": "interests", "content": "writing test cases"}],
        importance_score=0.7,
        embedding=[0.1] * 1536
    )
    
    await db.create_session(session_id_2, user_id, datetime.now().isoformat(), "2026-05-28")
    await db.update_session(
        user_id=user_id,
        session_id=session_id_2,
        title="Gardening hobbies",
        summary="Bob talks about growing organic tomatoes.",
        emotion_metadata={"primary_emotion": "happy"},
        memories=[{"category": "interests", "content": "growing organic tomatoes"}],
        importance_score=0.6,
        embedding=[0.8] * 1536
    )
    
    # Query build_context with 'tomatoes' keyword
    ctx = await build_context(user_id, "Did we talk about tomatoes?")
    
    # Let's verify retrieved memories has session-2
    assert "tomatoes" in ctx["retrieved_memories"]
    assert "Gardening hobbies" in ctx["retrieved_memories"]


@pytest.mark.asyncio
async def test_commands_handler():
    from app.bot import commands_handler
    
    update = MagicMock()
    update.message.reply_text = AsyncMock()
    context = MagicMock()
    
    await commands_handler(update, context)
    
    update.message.reply_text.assert_called_once()
    args, kwargs = update.message.reply_text.call_args
    content = args[0]
    assert "/start" in content
    assert "/diary" in content
    assert "/commands" in content
    assert "Available Commands" in content


```

### 📄 `tests/test_user_isolation.py`

```python
import asyncio
import json
import os
import shutil
import pytest
from pathlib import Path

# Override DB_PATH to use a separate test database
import app.config
TEST_DB_PATH = Path(app.config.DATA_DIR) / "test_diary.db"
app.config.DB_PATH = TEST_DB_PATH
app.config.DATABASE_URL = ""

from app.database import get_db
from app.utils import get_session_manager
from app.retrieval_engine import build_context, get_vector_search_candidates
from app.bot import message_handler


import pytest_asyncio

@pytest_asyncio.fixture(autouse=True)
async def setup_test_db():
    # Ensure any old test DB is cleared
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:
            pass
            
    db = get_db()
    # Force re-initialization with test DB
    db._initialized = False
    await db.initialize()
    
    yield
    
    # Close and clean up test DB
    await db.close()
    if TEST_DB_PATH.exists():
        try:
            TEST_DB_PATH.unlink()
        except OSError:
            pass


@pytest.mark.asyncio
async def test_db_queries_isolation():
    db = get_db()
    user_a = 11111
    user_b = 22222

    # Ensure users exist
    await db.ensure_user(user_a, "user_a", "Alice")
    await db.ensure_user(user_b, "user_b", "Bob")

    # Save distinct episodes
    await db.save_episode(user_a, "Hello from Alice", "Response to Alice", topics=["topicA"])
    await db.save_episode(user_b, "Hello from Bob", "Response to Bob", topics=["topicB"])

    # Save distinct diary entries
    entry_a_id = await db.save_diary_entry(user_a, "Alice's secret diary", title="Secret Alice")
    entry_b_id = await db.save_diary_entry(user_b, "Bob's secret diary", title="Secret Bob")

    # Verify Alice cannot retrieve Bob's episodes
    recent_a = await db.get_recent_episodes(user_a)
    assert len(recent_a) == 1
    assert recent_a[0]["user_message"] == "Hello from Alice"

    # Verify Bob cannot retrieve Alice's episodes
    recent_b = await db.get_recent_episodes(user_b)
    assert len(recent_b) == 1
    assert recent_b[0]["user_message"] == "Hello from Bob"

    # Verify search scoping
    search_a = await db.search_episodes(user_a, "Hello")
    assert len(search_a) == 1
    assert search_a[0]["user_message"] == "Hello from Alice"

    # Verify active memory items scoping
    await db.upsert_memory_item(user_a, "interests", "Naruto")
    await db.upsert_memory_item(user_b, "interests", "One Piece")

    memories_a = await db.get_active_memories(user_a)
    assert len(memories_a) == 1
    assert memories_a[0]["content"] == "Naruto"

    memories_b = await db.get_active_memories(user_b)
    assert len(memories_b) == 1
    assert memories_b[0]["content"] == "One Piece"

    # Verify updates are scoped by user_id: Bob should not be able to update Alice's entry
    await db.update_diary_entry(user_id=user_b, entry_id=entry_a_id, title="Hacked by Bob")
    entry_a = await db.get_latest_diary_entry(user_a)
    assert entry_a["title"] == "Secret Alice"  # Title remains unchanged because update was scoped to user_b


@pytest.mark.asyncio
async def test_vector_search_isolation():
    db = get_db()
    user_a = 11111
    user_b = 22222

    await db.ensure_user(user_a, "user_a", "Alice")
    await db.ensure_user(user_b, "user_b", "Bob")

    # Same message but different users
    mock_emb = json.dumps([0.1] * 1536).encode('utf-8')

    # Directly write embeddings in DB for simulation
    cursor = await db.db.execute("""
        INSERT INTO episodes (user_id, user_message, bot_response, embedding, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (user_a, "I love programming in python", "Indeed", mock_emb, "2026-05-29T10:00:00"))
    
    await db.db.execute("""
        INSERT INTO episodes (user_id, user_message, bot_response, embedding, timestamp)
        VALUES (?, ?, ?, ?, ?)
    """, (user_b, "I love programming in python", "Indeed", mock_emb, "2026-05-29T10:00:00"))
    await db.db.commit()

    # Search for User A should ONLY yield User A's candidate, never User B's
    results_a = await get_vector_search_candidates(user_a, "python", limit=5)
    assert len(results_a) == 1
    assert all(r["user_id"] == user_a for r in results_a)

    results_b = await get_vector_search_candidates(user_b, "python", limit=5)
    assert len(results_b) == 1
    assert all(r["user_id"] == user_b for r in results_b)


@pytest.mark.asyncio
async def test_session_manager_cleanup():
    manager = get_session_manager()
    user_id = 99999

    # Acquire lock context
    async with manager.lock_user(user_id):
        # Lock should be in manager structures
        assert user_id in manager._locks
        assert manager._lock_count[user_id] == 1

    # After exit, lock structures should be fully cleaned up to prevent memory leaks
    assert user_id not in manager._locks
    assert user_id not in manager._lock_count


@pytest.mark.asyncio
async def test_concurrency_race_conditions():
    manager = get_session_manager()
    user_id = 77777
    execution_order = []

    async def task_worker(worker_id: int, delay: float):
        async with manager.lock_user(user_id):
            execution_order.append(f"start_{worker_id}")
            await asyncio.sleep(delay)
            execution_order.append(f"end_{worker_id}")

    # Launch two concurrent tasks for the SAME user
    # Task 1 starts and sleeps for 0.1s. Task 2 must wait for Task 1 to complete before starting.
    await asyncio.gather(
        task_worker(1, 0.1),
        task_worker(2, 0.01)
    )

    # Verify strict serial execution order for the same user
    assert execution_order == ["start_1", "end_1", "start_2", "end_2"]

```

### 📄 `vercel.json`

```json
{
  "version": 2,
  "builds": [
    {
      "src": "api/index.py",
      "use": "@vercel/python"
    }
  ],
  "routes": [
    {
      "src": "/webhook",
      "dest": "api/index.py"
    },
    {
      "src": "/cron/(.*)",
      "dest": "api/index.py"
    },
    {
      "src": "/(.*)",
      "dest": "api/index.py"
    }
  ],
  "crons": [
    {
      "path": "/cron/daily",
      "schedule": "0 2 * * *"
    }
  ]
}

```
