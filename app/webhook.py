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
