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
    except Exception as e:
        logger.error("PTB INIT FAILED: %s", e, exc_info=True)

@app.on_event("shutdown")
async def shutdown():
    await ptb_app.shutdown()
    await get_db().close()

@app.get("/")
async def root():
    return {"status": "online", "message": "Telegram Memory Bot is active."}

@app.get("/health")
async def health():
    return {"status": "ok"}

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
