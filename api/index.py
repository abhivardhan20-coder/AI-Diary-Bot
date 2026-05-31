import os
import json
import logging
import hmac
import httpx
import asyncio
from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from telegram import Update

from contextlib import asynccontextmanager
from app.config import WEBHOOK_SECRET, CRON_SECRET, validate_config
from app.bot import get_ptb_app
from app.database import get_db
from app.utils import check_and_generate_summaries
from app.semantic_engine import curate_user_profile

def safe_compare(val1: str, val2: str) -> bool:
    if not val1 or not val2:
        return False
    return hmac.compare_digest(val1.encode("utf-8"), val2.encode("utf-8"))

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")

# Global bot application instance using the singleton
ptb_app = get_ptb_app()

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Unified application lifespan manager."""
    # STARTUP
    try:
        # Validate environment variables first to fail fast
        validate_config()
    except Exception as e:
        logger.critical("Configuration validation failed: %s", e)
        from app.config import IS_VERCEL
        if not IS_VERCEL:
            raise e

    try:
        logger.info("Starting database initialization...")
        await get_db().initialize()
        logger.info("Database initialized successfully.")
    except Exception as e:
        logger.error("DATABASE INIT FAILED: %s", e, exc_info=True)
        # Don't re-raise — let the app start so we can at least see health/root endpoints

    try:
        from app.config import IS_VERCEL, WEBHOOK_URL
        logger.info("Starting PTB application initialization...")
        await ptb_app.initialize()
        logger.info("PTB application initialized successfully.")
        
        # Start PTB application
        await ptb_app.start()
        logger.info("PTB application started successfully.")
        
        # Register bot commands menu on startup
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
            BotCommand("search", "search past conversations by keyword"),
            BotCommand("stats", "view your usage statistics and streaks"),
            BotCommand("mood", "show emotional trend breakdown"),
            BotCommand("help", "list all available commands"),
            BotCommand("commands", "list all available commands")
        ]
        try:
            await ptb_app.bot.set_my_commands(commands_list)
            logger.info("Bot commands menu registered successfully.")
        except Exception as e:
            logger.error("Failed to set bot commands: %s", e)
        
        if not IS_VERCEL:
            # Long-running server (Railway / Local): setup webhook or polling
            if WEBHOOK_URL:
                webhook_path = f"{WEBHOOK_URL.rstrip('/')}/webhook"
                logger.info("Setting webhook to %s", webhook_path)
                await ptb_app.bot.set_webhook(
                    url=webhook_path,
                    secret_token=WEBHOOK_SECRET,
                    drop_pending_updates=True
                )
            else:
                logger.info("WEBHOOK_URL not set. Falling back to POLLING mode...")
                await ptb_app.updater.start_polling()
        else:
            logger.info("Skipping automatic webhook registration on startup (handled on-demand via /setup-webhook to optimize Vercel cold starts).")
        
        # Start background sweeper task for _in_mem_counters
        from app.utils import _sweep_counters
        asyncio.create_task(_sweep_counters(interval=300))
        logger.info("Started background counter sweep task (interval: 300s).")
    except Exception as e:
        logger.error("PTB INIT/START FAILED: %s", e, exc_info=True)

    yield

    # SHUTDOWN
    logger.info("Shutting down application...")
    try:
        from app.config import IS_VERCEL, WEBHOOK_URL
        if not IS_VERCEL and not WEBHOOK_URL:
            await ptb_app.updater.stop()
        await ptb_app.stop()
    except Exception as e:
        logger.error("Error stopping PTB app: %s", e)
    await ptb_app.shutdown()
    await get_db().close()
    logger.info("Application shutdown completed.")

# Expose app for Vercel with lifespan
app = FastAPI(lifespan=lifespan)

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
async def root():
    return {"status": "online", "message": "Telegram Memory Bot is active."}

@app.get("/health")
async def health():
    return {"status": "ok"}

@app.get("/setup-webhook")
async def setup_webhook(request: Request):
    # SEC-2 [CRITICAL]: setup-webhook Endpoint Has No Authentication
    token = request.headers.get("X-Admin-Token") or request.query_params.get("token", "")
    if not CRON_SECRET or not safe_compare(token, CRON_SECRET):
        logger.warning("Unauthorized /setup-webhook attempt")
        return Response(status_code=status.HTTP_403_FORBIDDEN)

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
            "error": "Failed to set webhook. Please check server logs.",
            "webhook_url": webhook_path
        }

@app.post("/webhook")
async def webhook(request: Request):
    client_host = request.client.host if request.client else "unknown"
    # Verify secret token in timing-safe way
    token = request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")
    if not WEBHOOK_SECRET or not safe_compare(token, WEBHOOK_SECRET):
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
        auth_header = request.headers.get("Authorization", "")
        expected_header = f"Bearer {CRON_SECRET}"
        if not safe_compare(auth_header, expected_header):
            logger.warning("Unauthorized cron attempt from IP %s with invalid secret", client_host)
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
            
    logger.info("Executing daily summaries and curation cron")
    db = get_db()
    try:
        # Run out-of-band session compaction globally for all uncompacted ended sessions
        from app.memory_engine import compact_uncompacted_sessions
        await compact_uncompacted_sessions()
        
        users = await db.get_all_users()
        user_ids = [u["user_id"] for u in users]
        
        from app.config import QSTASH_TOKEN, WEBHOOK_URL
        if QSTASH_TOKEN and WEBHOOK_URL:
            logger.info("Enqueuing daily cron jobs to QStash for %d users", len(user_ids))
            failed = []
            async with httpx.AsyncClient() as client:
                for uid in user_ids:
                    try:
                        # Enqueue a call to /cron/user-daily
                        res = await client.post(
                            f"https://qstash.upstash.io/v2/publish/{WEBHOOK_URL.rstrip('/')}/cron/user-daily",
                            headers={
                                "Authorization": f"Bearer {QSTASH_TOKEN}",
                                "Upstash-Forward-Authorization": f"Bearer {CRON_SECRET}",
                                "Content-Type": "application/json"
                            },
                            json={"user_id": uid}
                        )
                        res.raise_for_status()
                    except Exception as eq:
                        logger.error("Failed to enqueue daily cron for user %d: %s", uid, eq)
                        failed.append(uid)
            
            if failed:
                logger.error("QStash enqueue failed for %d/%d users", len(failed), len(user_ids))
                return {"status": "partial", "failed_count": len(failed), "total": len(user_ids)}
            return {"status": "success", "total": len(user_ids)}
        else:
            logger.warning("QStash not configured. Falling back to sequential execution for %d users", len(user_ids))
            for uid in user_ids:
                try:
                    await check_and_generate_summaries(uid)
                    await curate_user_profile(uid)
                except Exception as ue:
                    logger.error("Sequential cron failed for user %d: %s", uid, ue)
                    
    except Exception as e:
        logger.error("Daily summaries cron failed: %s", e)
        return {"status": "failed", "error": "Cron failed. Check logs for details."}
    return {"status": "success"}

@app.post("/cron/user-daily")
async def cron_user_daily(request: Request):
    client_host = request.client.host if request.client else "unknown"
    user_id = None  # Initialize for exception handler
    if CRON_SECRET:
        auth_header = request.headers.get("Authorization", "")
        expected_header = f"Bearer {CRON_SECRET}"
        if not safe_compare(auth_header, expected_header):
            logger.warning("Unauthorized user cron attempt from IP %s", client_host)
            return Response(status_code=status.HTTP_401_UNAUTHORIZED)
            
    try:
        data = await request.json()
    except Exception:
        return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "Invalid JSON"})
    
    try:
        user_id = data.get("user_id")
        if not isinstance(user_id, int):
            return JSONResponse(status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "user_id must be an integer"})
            
        logger.info("Executing daily summaries and curation for user %d", user_id)
        await check_and_generate_summaries(user_id)
        await curate_user_profile(user_id)
        return {"status": "success", "user_id": user_id}
    except Exception as e:
        logger.error("Daily summaries cron failed for user %s: %s", user_id, e)
        return Response(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
