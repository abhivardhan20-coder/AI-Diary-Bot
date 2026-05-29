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
