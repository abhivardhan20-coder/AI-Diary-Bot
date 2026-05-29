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



