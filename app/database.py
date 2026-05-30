"""
Async Database manager for the AI Diary Companion.
Supports both local SQLite and cloud PostgreSQL with pgvector.
"""

import asyncio
import json
import logging
import shutil
from datetime import datetime
from pathlib import Path

import aiosqlite
try:
    import asyncpg
except ImportError:
    asyncpg = None

from app.config import DB_PATH, BACKUP_DIR, DATABASE_URL

logger = logging.getLogger(__name__)

# Helper to format queries depending on dialect
def _sql(query: str, is_postgres: bool) -> str:
    if not is_postgres:
        return query
    # Replace ? with $1, $2... for PostgreSQL
    count = 1
    new_query = []
    for char in query:
        if char == '?':
            new_query.append(f"${count}")
            count += 1
        else:
            new_query.append(char)
    return "".join(new_query)

# ── SQLite SQL Schema ───────────────────────────────────────────────────────────

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS users (
    user_id     INTEGER PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    timezone    TEXT    NOT NULL DEFAULT 'Asia/Kolkata',
    reminder_time TEXT  NOT NULL DEFAULT '22:00',
    reminder_enabled INTEGER NOT NULL DEFAULT 1,
    diary_mode  INTEGER NOT NULL DEFAULT 0,
    pending_topics TEXT,
    last_seen   TEXT,
    current_session_id TEXT,
    relationship_stage TEXT NOT NULL DEFAULT 'new',
    onboarding_status TEXT NOT NULL DEFAULT 'completed',
    age         INTEGER,
    nationality TEXT,
    city        TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    user_id             INTEGER NOT NULL,
    start_time          TEXT NOT NULL,
    end_time            TEXT,
    date                TEXT NOT NULL,
    title               TEXT,
    summary             TEXT,
    emotion_metadata    TEXT,
    memories            TEXT,
    importance_score    REAL DEFAULT 0.3,
    embedding           BLOB,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS episodes (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id             INTEGER NOT NULL,
    session_id          TEXT,
    user_message        TEXT    NOT NULL,
    bot_response        TEXT    NOT NULL,
    detected_emotion    TEXT,
    emotion_confidence  REAL,
    secondary_emotion   TEXT,
    topics              TEXT,
    is_diary_entry      INTEGER NOT NULL DEFAULT 0,
    importance_score    REAL DEFAULT 0.3,
    is_referenced       INTEGER DEFAULT 0,
    embedding           BLOB,
    timestamp           TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id),
    FOREIGN KEY (session_id) REFERENCES sessions(session_id)
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_episodes_user_ts ON episodes(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_episodes_user_emotion ON episodes(user_id, detected_emotion);
CREATE INDEX IF NOT EXISTS idx_episodes_topics ON episodes(user_id, topics);

CREATE TABLE IF NOT EXISTS semantic_profiles (
    user_id      INTEGER PRIMARY KEY,
    profile_data TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS summaries (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    summary_type    TEXT    NOT NULL,
    period_start    TEXT    NOT NULL,
    period_end      TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    emotional_trends TEXT,
    key_events      TEXT,
    created_at      TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_summaries_user_type ON summaries(user_id, summary_type, period_start);

CREATE TABLE IF NOT EXISTS schedules (
    user_id             INTEGER PRIMARY KEY,
    next_reminder       TEXT,
    last_reminder_sent  TEXT,
    streak_count        INTEGER NOT NULL DEFAULT 0,
    longest_streak      INTEGER NOT NULL DEFAULT 0,
    last_diary_date     TEXT,
    last_curation       TEXT,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id                 INTEGER NOT NULL,
    title                   TEXT,
    raw_text                TEXT    NOT NULL,
    detected_emotions       TEXT,
    emotion_confidence      REAL,
    extracted_goals         TEXT,
    extracted_stressors     TEXT,
    extracted_relationships TEXT,
    extracted_topics        TEXT,
    personality_signals     TEXT,
    behavioral_patterns     TEXT,
    ai_summary              TEXT,
    ai_followup             TEXT,
    importance_score        REAL    DEFAULT 0.5,
    embedding               BLOB,
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_diary_user_ts ON diary_entries(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_diary_user_importance ON diary_entries(user_id, importance_score DESC);

CREATE TABLE IF NOT EXISTS memory_items (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id         INTEGER NOT NULL,
    category        TEXT NOT NULL,
    content         TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    mention_count   INTEGER DEFAULT 1,
    is_resolved     INTEGER DEFAULT 0,
    importance      REAL DEFAULT 0.5,
    FOREIGN KEY (user_id) REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_memory_items_user_cat ON memory_items(user_id, category, is_resolved);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedup ON memory_items(user_id, category, LOWER(content));
"""

# ── PostgreSQL SQL Schema ───────────────────────────────────────────────────────

SCHEMA_SQL_PG = """
CREATE TABLE IF NOT EXISTS users (
    user_id     BIGINT PRIMARY KEY,
    username    TEXT,
    first_name  TEXT,
    timezone    TEXT    NOT NULL DEFAULT 'Asia/Kolkata',
    reminder_time TEXT  NOT NULL DEFAULT '22:00',
    reminder_enabled INTEGER NOT NULL DEFAULT 1,
    diary_mode  INTEGER NOT NULL DEFAULT 0,
    pending_topics TEXT,
    last_seen   TEXT,
    current_session_id TEXT,
    relationship_stage TEXT NOT NULL DEFAULT 'new',
    onboarding_status TEXT NOT NULL DEFAULT 'completed',
    age         INTEGER,
    nationality TEXT,
    city        TEXT,
    created_at  TEXT    NOT NULL,
    updated_at  TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    session_id          TEXT PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    start_time          TEXT NOT NULL,
    end_time            TEXT,
    date                TEXT NOT NULL,
    title               TEXT,
    summary             TEXT,
    emotion_metadata    TEXT,
    memories            TEXT,
    importance_score    REAL DEFAULT 0.3,
    embedding           vector(1536),
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS episodes (
    id                  SERIAL PRIMARY KEY,
    user_id             BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    session_id          TEXT REFERENCES sessions(session_id) ON DELETE SET NULL,
    user_message        TEXT    NOT NULL,
    bot_response        TEXT    NOT NULL,
    detected_emotion    TEXT,
    emotion_confidence  REAL,
    secondary_emotion   TEXT,
    topics              TEXT,
    is_diary_entry      INTEGER NOT NULL DEFAULT 0,
    importance_score    REAL DEFAULT 0.3,
    is_referenced       INTEGER DEFAULT 0,
    embedding           vector(1536),
    timestamp           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS semantic_profiles (
    user_id      BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    profile_data TEXT    NOT NULL,
    updated_at   TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS summaries (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    summary_type    TEXT    NOT NULL,
    period_start    TEXT    NOT NULL,
    period_end      TEXT    NOT NULL,
    content         TEXT    NOT NULL,
    emotional_trends TEXT,
    key_events      TEXT,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS schedules (
    user_id             BIGINT PRIMARY KEY REFERENCES users(user_id) ON DELETE CASCADE,
    next_reminder       TEXT,
    last_reminder_sent  TEXT,
    streak_count        INTEGER NOT NULL DEFAULT 0,
    longest_streak      INTEGER NOT NULL DEFAULT 0,
    last_diary_date     TEXT,
    last_curation       TEXT
);

CREATE TABLE IF NOT EXISTS diary_entries (
    id                      SERIAL PRIMARY KEY,
    user_id                 BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    title                   TEXT,
    raw_text                TEXT    NOT NULL,
    detected_emotions       TEXT,
    emotion_confidence      REAL,
    extracted_goals         TEXT,
    extracted_stressors     TEXT,
    extracted_relationships TEXT,
    extracted_topics        TEXT,
    personality_signals     TEXT,
    behavioral_patterns     TEXT,
    ai_summary              TEXT,
    ai_followup             TEXT,
    importance_score        REAL    DEFAULT 0.5,
    embedding               vector(1536),
    created_at              TEXT    NOT NULL,
    updated_at              TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS memory_items (
    id              SERIAL PRIMARY KEY,
    user_id         BIGINT NOT NULL REFERENCES users(user_id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    content         TEXT NOT NULL,
    first_seen      TEXT NOT NULL,
    last_seen       TEXT NOT NULL,
    mention_count   INTEGER DEFAULT 1,
    is_resolved     INTEGER DEFAULT 0,
    importance      REAL DEFAULT 0.5
);
"""

INDICES_SQL_PG = """
CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);
CREATE INDEX IF NOT EXISTS idx_episodes_user_ts ON episodes(user_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_episodes_user_emotion ON episodes(user_id, detected_emotion);
CREATE INDEX IF NOT EXISTS idx_episodes_topics ON episodes(user_id, topics);
CREATE INDEX IF NOT EXISTS idx_summaries_user_type ON summaries(user_id, summary_type, period_start);
CREATE INDEX IF NOT EXISTS idx_diary_user_ts ON diary_entries(user_id, created_at);
CREATE INDEX IF NOT EXISTS idx_diary_user_importance ON diary_entries(user_id, importance_score DESC);
CREATE INDEX IF NOT EXISTS idx_memory_items_user_cat ON memory_items(user_id, category, is_resolved);
CREATE UNIQUE INDEX IF NOT EXISTS idx_memory_dedup ON memory_items(user_id, category, LOWER(content));
"""

class DatabaseManager:
    _instance: "DatabaseManager | None" = None
    _lock: asyncio.Lock = asyncio.Lock()

    def __new__(cls) -> "DatabaseManager":
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._db = None
            cls._instance._pool = None
            cls._instance._is_postgres = False
            cls._instance._initialized = False
        return cls._instance

    async def initialize(self) -> None:
        if self._initialized: return
        async with self._lock:
            if self._initialized: return
            
            if DATABASE_URL:
                logger.info("Initializing cloud PostgreSQL database")
                if asyncpg is None:
                    raise ImportError("asyncpg is required to connect to PostgreSQL. Add it to requirements.txt.")
                
                url = DATABASE_URL
                if url.startswith("postgres://"):
                    url = url.replace("postgres://", "postgresql://", 1)
                self._pool = await asyncpg.create_pool(
                    url,
                    min_size=1,
                    max_size=2
                )
                self._is_postgres = True
                
                async with self._pool.acquire() as conn:
                    try:
                        await conn.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                    except Exception as e:
                        logger.warning("Could not enable pgvector extension: %s", e)
                    await conn.execute(SCHEMA_SQL_PG)
                    
                    try:
                        await conn.execute("""
                            DELETE FROM memory_items 
                            WHERE id NOT IN (
                                SELECT MIN(id) 
                                FROM memory_items 
                                GROUP BY user_id, category, LOWER(content)
                            );
                        """)
                    except Exception as e:
                        logger.warning("Failed to deduplicate memory_items in PostgreSQL: %s", e)
                        
                    await conn.execute(INDICES_SQL_PG)
                    try:
                        await conn.execute("ALTER TABLE users ADD COLUMN IF NOT EXISTS current_session_id TEXT;")
                        await conn.execute("ALTER TABLE episodes ADD COLUMN IF NOT EXISTS session_id TEXT;")
                    except Exception as e:
                        logger.warning("Postgres column migration failed: %s", e)
            else:
                logger.info("Initializing local SQLite database at %s", DB_PATH)
                self._db = await aiosqlite.connect(str(DB_PATH))
                self._db.row_factory = aiosqlite.Row
                await self._db.execute("PRAGMA journal_mode=WAL")
                await self._db.execute("PRAGMA synchronous=NORMAL")
                await self._db.execute("PRAGMA foreign_keys=ON")
                
                try:
                    await self._db.execute("""
                        DELETE FROM memory_items 
                        WHERE id NOT IN (
                            SELECT MIN(id) 
                            FROM memory_items 
                            GROUP BY user_id, category, LOWER(content)
                        );
                    """)
                    await self._db.commit()
                except Exception as e:
                    # Ignore if the table does not exist yet (will be created in executescript)
                    pass
                    
                await self._db.executescript(SCHEMA_SQL)
                try:
                    await self._db.execute("ALTER TABLE users ADD COLUMN current_session_id TEXT;")
                except Exception:
                    pass
                try:
                    await self._db.execute("ALTER TABLE episodes ADD COLUMN session_id TEXT;")
                except Exception:
                    pass
                try:
                    await self._db.execute("CREATE INDEX IF NOT EXISTS idx_episodes_session ON episodes(session_id);")
                except Exception:
                    pass
                await self._db.commit()
                self._is_postgres = False
                
            self._initialized = True

    async def close(self) -> None:
        if self._pool:
            await self._pool.close()
            self._pool = None
        if self._db:
            await self._db.close()
            self._db = None
        self._initialized = False

    @property
    def db(self):
        if not self._initialized:
            raise RuntimeError("Database not initialized")
        return self._db

    async def _ensure_initialized(self) -> None:
        if not self._initialized:
            logger.info("Database was not initialized. Initializing on-demand...")
            await self.initialize()

    # --- Query wrappers supporting both dialects ---

    async def execute(self, query: str, *args) -> None:
        await self._ensure_initialized()
        query_fmt = _sql(query, self._is_postgres)
        if self._is_postgres:
            async with self._pool.acquire() as conn:
                await conn.execute(query_fmt, *args)
        else:
            await self._db.execute(query_fmt, args)
            await self._db.commit()

    async def fetch(self, query: str, *args) -> list[dict]:
        await self._ensure_initialized()
        query_fmt = _sql(query, self._is_postgres)
        if self._is_postgres:
            async with self._pool.acquire() as conn:
                rows = await conn.fetch(query_fmt, *args)
                return [dict(r) for r in rows]
        else:
            cursor = await self._db.execute(query_fmt, args)
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]

    async def fetchrow(self, query: str, *args) -> dict | None:
        await self._ensure_initialized()
        query_fmt = _sql(query, self._is_postgres)
        if self._is_postgres:
            async with self._pool.acquire() as conn:
                row = await conn.fetchrow(query_fmt, *args)
                return dict(row) if row else None
        else:
            cursor = await self._db.execute(query_fmt, args)
            row = await cursor.fetchone()
            return dict(row) if row else None

    # --- Schema/Tables data methods ---

    async def ensure_user(self, user_id: int, username: str | None = None, first_name: str | None = None) -> None:
        now = datetime.now().isoformat()
        if self._is_postgres:
            await self.execute("""
                INSERT INTO users (user_id, username, first_name, created_at, updated_at, onboarding_status)
                VALUES ($1, $2, $3, NOW(), NOW(), 'not_started')
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    updated_at = NOW()
            """, user_id, username, first_name)
        else:
            await self.execute("""
                INSERT INTO users (user_id, username, first_name, created_at, updated_at, onboarding_status)
                VALUES (?, ?, ?, ?, ?, 'not_started')
                ON CONFLICT(user_id) DO UPDATE SET
                    username = COALESCE(excluded.username, users.username),
                    first_name = COALESCE(excluded.first_name, users.first_name),
                    updated_at = excluded.updated_at
            """, user_id, username, first_name, now, now)
        from app.utils import get_cache
        await get_cache().delete(f"user:info:{user_id}")

    async def get_user(self, user_id: int) -> dict | None:
        from app.utils import get_cache
        cache_key = f"user:info:{user_id}"
        cached = await get_cache().get(cache_key)
        if cached is not None:
            return cached
        res = await self.fetchrow("SELECT * FROM users WHERE user_id = ?", user_id)
        if res:
            await get_cache().set(cache_key, res, ttl=86400)
        return res

    async def update_user_settings(self, user_id: int, **kwargs) -> None:
        allowed = {"timezone", "reminder_time", "reminder_enabled"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields: return
        
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        if self._is_postgres:
            fields["updated_at"] = datetime.now().isoformat()
            set_clause = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, 1))
            await self.execute(f"UPDATE users SET {set_clause} WHERE user_id = ${len(fields)+1}", *(list(fields.values()) + [user_id]))
        else:
            fields["updated_at"] = datetime.now().isoformat()
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            await self.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", *(list(fields.values()) + [user_id]))
        from app.utils import get_cache
        await get_cache().delete(f"user:info:{user_id}")

    async def get_all_users_with_reminders(self) -> list[dict]:
        return await self.fetch("SELECT * FROM users WHERE reminder_enabled = 1")

    async def save_episode(self, user_id: int, user_message: str, bot_response: str, **kwargs) -> int:
        now = datetime.now().isoformat()
        topics_json = json.dumps(kwargs.get("topics")) if kwargs.get("topics") else None
        is_diary = 1 if kwargs.get("is_diary_entry") else 0
        session_id = kwargs.get("session_id")
        
        if self._is_postgres:
            row = await self.fetchrow("""
                INSERT INTO episodes (user_id, session_id, user_message, bot_response, detected_emotion, 
                                     emotion_confidence, secondary_emotion, topics, is_diary_entry, timestamp)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW())
                RETURNING id
            """, user_id, session_id, user_message, bot_response, kwargs.get("detected_emotion"),
               kwargs.get("emotion_confidence"), kwargs.get("secondary_emotion"), 
               topics_json, is_diary)
            episode_id = row["id"]
        else:
            cursor = await self._db.execute("""
                INSERT INTO episodes (user_id, session_id, user_message, bot_response, detected_emotion, 
                                     emotion_confidence, secondary_emotion, topics, is_diary_entry, timestamp)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, session_id, user_message, bot_response, kwargs.get("detected_emotion"),
                  kwargs.get("emotion_confidence"), kwargs.get("secondary_emotion"), 
                  topics_json, is_diary, now))
            await self._db.commit()
            episode_id = cursor.lastrowid
        from app.utils import get_cache
        await get_cache().delete_pattern(f"user:recent_episodes:{user_id}:*")
        return episode_id

    async def get_recent_episodes(self, user_id: int, limit: int = 5) -> list[dict]:
        from app.utils import get_cache
        cache_key = f"user:recent_episodes:{user_id}:{limit}"
        cached = await get_cache().get(cache_key)
        if cached is not None:
            return cached
        episodes = await self.fetch("SELECT * FROM episodes WHERE user_id = ? ORDER BY timestamp DESC LIMIT ?", user_id, limit)
        for ep in episodes:
            if ep.get("topics"):
                try: ep["topics"] = json.loads(ep["topics"])
                except: ep["topics"] = []
        res = list(reversed(episodes))
        await get_cache().set(cache_key, res, ttl=300)
        return res

    async def get_episodes_by_emotion(self, user_id: int, emotion: str, limit: int = 5) -> list[dict]:
        return await self.fetch("SELECT * FROM episodes WHERE user_id = ? AND detected_emotion = ? ORDER BY timestamp DESC LIMIT ?", user_id, emotion, limit)

    async def search_episodes(self, user_id: int, query: str, limit: int = 10) -> list[dict]:
        p = f"%{query}%"
        return await self.fetch("SELECT * FROM episodes WHERE user_id = ? AND (user_message LIKE ? OR topics LIKE ?) ORDER BY timestamp DESC LIMIT ?", user_id, p, p, limit)

    async def get_episodes_by_date_range(self, user_id: int, start: str, end: str) -> list[dict]:
        return await self.fetch("SELECT * FROM episodes WHERE user_id = ? AND timestamp >= ? AND timestamp <= ? ORDER BY timestamp ASC", user_id, start, end)

    async def get_episode_count(self, user_id: int) -> int:
        row = await self.fetchrow("SELECT COUNT(*) as count FROM episodes WHERE user_id = ?", user_id)
        return row["count"] if row else 0

    async def get_oldest_episode(self, user_id: int) -> dict | None:
        return await self.fetchrow("SELECT * FROM episodes WHERE user_id = ? ORDER BY timestamp ASC LIMIT 1", user_id)

    async def get_emotion_counts(self, user_id: int, days: int = 30) -> list[dict]:
        from datetime import timedelta
        cutoff = (datetime.now() - timedelta(days=days)).isoformat()
        return await self.fetch("SELECT detected_emotion, COUNT(*) as count FROM episodes WHERE user_id = ? AND timestamp >= ? AND detected_emotion IS NOT NULL GROUP BY detected_emotion ORDER BY count DESC", user_id, cutoff)

    async def delete_all_user_data(self, user_id: int) -> None:
        await self.execute("DELETE FROM episodes WHERE user_id = ?", user_id)
        await self.execute("DELETE FROM diary_entries WHERE user_id = ?", user_id)
        await self.execute("DELETE FROM semantic_profiles WHERE user_id = ?", user_id)
        await self.execute("DELETE FROM summaries WHERE user_id = ?", user_id)
        await self.execute("DELETE FROM schedules WHERE user_id = ?", user_id)
        await self.execute("DELETE FROM memory_items WHERE user_id = ?", user_id)
        await self.execute("DELETE FROM users WHERE user_id = ?", user_id)

    async def get_all_episodes(self, user_id: int) -> list[dict]:
        return await self.fetch("SELECT * FROM episodes WHERE user_id = ? ORDER BY timestamp ASC", user_id)

    async def get_semantic_profile(self, user_id: int) -> dict | None:
        from app.utils import get_cache
        cache_key = f"user:profile:{user_id}"
        cached = await get_cache().get(cache_key)
        if cached is not None:
            return cached
        row = await self.fetchrow("SELECT profile_data FROM semantic_profiles WHERE user_id = ?", user_id)
        res = json.loads(row["profile_data"]) if row else None
        if res:
            await get_cache().set(cache_key, res, ttl=86400)
        return res

    async def save_semantic_profile(self, user_id: int, profile: dict) -> None:
        now = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO semantic_profiles (user_id, profile_data, updated_at) VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET profile_data = excluded.profile_data, updated_at = excluded.updated_at
        """, user_id, json.dumps(profile), now)
        from app.utils import get_cache
        await get_cache().delete(f"user:profile:{user_id}")
        await get_cache().delete(f"user:memories:{user_id}")

    async def save_summary(self, user_id: int, summary_type: str, start: str, end: str, content: str, **kwargs) -> None:
        now = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO summaries (user_id, summary_type, period_start, period_end, content, emotional_trends, key_events, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, user_id, summary_type, start, end, content, json.dumps(kwargs.get("trends")), json.dumps(kwargs.get("events")), now)
        from app.utils import get_cache
        await get_cache().delete_pattern(f"user:summaries:{user_id}:*")

    async def get_recent_summaries(self, user_id: int, summary_type: str = "daily", limit: int = 3) -> list[dict]:
        from app.utils import get_cache
        cache_key = f"user:summaries:{user_id}:{summary_type}:{limit}"
        cached = await get_cache().get(cache_key)
        if cached is not None:
            return cached
        res = await self.fetch("SELECT * FROM summaries WHERE user_id = ? AND summary_type = ? ORDER BY period_end DESC LIMIT ?", user_id, summary_type, limit)
        await get_cache().set(cache_key, res, ttl=900)
        return res

    async def get_all_summaries(self, user_id: int) -> list[dict]:
        return await self.fetch("SELECT * FROM summaries WHERE user_id = ? ORDER BY period_start ASC", user_id)

    async def get_schedule(self, user_id: int) -> dict | None:
        return await self.fetchrow("SELECT * FROM schedules WHERE user_id = ?", user_id)

    async def update_schedule(self, user_id: int, **kwargs) -> None:
        await self.execute("INSERT INTO schedules (user_id) VALUES (?) ON CONFLICT(user_id) DO NOTHING", user_id)
        allowed = {"next_reminder", "last_reminder_sent", "streak_count", "longest_streak", "last_diary_date", "last_curation"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if fields:
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            await self.execute(f"UPDATE schedules SET {set_clause} WHERE user_id = ?", *(list(fields.values()) + [user_id]))

    async def save_diary_entry(self, user_id: int, raw_text: str, **kwargs) -> int:
        now = datetime.now().isoformat()
        goals = json.dumps(kwargs.get("extracted_goals")) if kwargs.get("extracted_goals") else None
        stressors = json.dumps(kwargs.get("extracted_stressors")) if kwargs.get("extracted_stressors") else None
        relationships = json.dumps(kwargs.get("extracted_relationships")) if kwargs.get("extracted_relationships") else None
        topics = json.dumps(kwargs.get("extracted_topics")) if kwargs.get("extracted_topics") else None
        importance = kwargs.get("importance_score", 0.5)
        
        if self._is_postgres:
            row = await self.fetchrow("""
                INSERT INTO diary_entries (user_id, title, raw_text, detected_emotions, emotion_confidence, 
                                         extracted_goals, extracted_stressors, extracted_relationships, 
                                         extracted_topics, ai_summary, ai_followup, importance_score, created_at, updated_at)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, NOW(), NOW())
                RETURNING id
            """, user_id, kwargs.get("title"), raw_text, kwargs.get("detected_emotions"), kwargs.get("emotion_confidence"),
               goals, stressors, relationships, topics, kwargs.get("ai_summary"), kwargs.get("ai_followup"), importance)
            return row["id"]
        else:
            cursor = await self._db.execute("""
                INSERT INTO diary_entries (user_id, title, raw_text, detected_emotions, emotion_confidence, 
                                         extracted_goals, extracted_stressors, extracted_relationships, 
                                         extracted_topics, ai_summary, ai_followup, importance_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (user_id, kwargs.get("title"), raw_text, kwargs.get("detected_emotions"), kwargs.get("emotion_confidence"),
                  goals, stressors, relationships, topics, kwargs.get("ai_summary"), kwargs.get("ai_followup"), importance, now, now))
            await self._db.commit()
            return cursor.lastrowid

    async def update_diary_entry(self, user_id: int, entry_id: int, **kwargs) -> None:
        json_fields = {"extracted_goals", "extracted_stressors", "extracted_relationships", "extracted_topics"}
        fields = {k: (json.dumps(v) if k in json_fields else v) for k, v in kwargs.items()}
        fields["updated_at"] = datetime.now().isoformat()
        
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        await self.execute(f"UPDATE diary_entries SET {set_clause} WHERE id = ? AND user_id = ?", *(list(fields.values()) + [entry_id, user_id]))

    async def get_latest_diary_entry(self, user_id: int) -> dict | None:
        row = await self.fetchrow("SELECT * FROM diary_entries WHERE user_id = ? ORDER BY created_at DESC LIMIT 1", user_id)
        return self._parse_diary_row(row) if row else None

    async def get_recent_diary_entries(self, user_id: int, limit: int = 5) -> list[dict]:
        rows = await self.fetch("SELECT * FROM diary_entries WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", user_id, limit)
        return [self._parse_diary_row(r) for r in reversed(rows)]

    async def get_important_diary_entries(self, user_id: int, limit: int = 5) -> list[dict]:
        rows = await self.fetch("SELECT * FROM diary_entries WHERE user_id = ? ORDER BY importance_score DESC, created_at DESC LIMIT ?", user_id, limit)
        return [self._parse_diary_row(r) for r in rows]

    async def search_diary_entries(self, user_id: int, query: str, limit: int = 10) -> list[dict]:
        p = f"%{query}%"
        rows = await self.fetch("SELECT * FROM diary_entries WHERE user_id = ? AND (raw_text LIKE ? OR ai_summary LIKE ? OR title LIKE ?) ORDER BY created_at DESC LIMIT ?", user_id, p, p, p, limit)
        return [self._parse_diary_row(r) for r in rows]

    async def get_diary_entry_count(self, user_id: int) -> int:
        row = await self.fetchrow("SELECT COUNT(*) as count FROM diary_entries WHERE user_id = ?", user_id)
        return row["count"] if row else 0

    async def get_all_diary_entries(self, user_id: int) -> list[dict]:
        rows = await self.fetch("SELECT * FROM diary_entries WHERE user_id = ? ORDER BY created_at ASC", user_id)
        return [self._parse_diary_row(r) for r in rows]

    async def get_diary_emotion_timeline(self, user_id: int, limit: int = 30) -> list[dict]:
        rows = await self.fetch("SELECT id, created_at, detected_emotions, importance_score, ai_summary FROM diary_entries WHERE user_id = ? ORDER BY created_at DESC LIMIT ?", user_id, limit)
        return [dict(r) for r in reversed(rows)]

    async def get_diary_timeline_events(self, user_id: int, min_importance: float = 0.6, limit: int = 20) -> list[dict]:
        rows = await self.fetch("SELECT id, created_at, title, ai_summary, detected_emotions, importance_score FROM diary_entries WHERE user_id = ? AND importance_score >= ? ORDER BY created_at DESC LIMIT ?", user_id, min_importance, limit)
        return [dict(r) for r in reversed(rows)]

    async def update_user(self, user_id: int, **kwargs) -> None:
        allowed = {"timezone", "reminder_time", "reminder_enabled", "diary_mode", "pending_topics", "last_seen", "relationship_stage", "current_session_id"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields: return
        fields["updated_at"] = datetime.now().isoformat()
        
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        await self.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", *(list(fields.values()) + [user_id]))
        from app.utils import get_cache
        await get_cache().delete(f"user:info:{user_id}")

    async def get_all_users(self) -> list[dict]:
        return await self.fetch("SELECT * FROM users")

    async def upsert_memory_item(self, user_id: int, category: str, content: str, importance: float = 0.5) -> None:
        now = datetime.now().isoformat()
        rows = await self.fetch("""
            SELECT * FROM memory_items 
            WHERE user_id = ? AND category = ? AND is_resolved = 0
        """, user_id, category)
        
        match_row = None
        for r in rows:
            if r["content"].lower().strip() == content.lower().strip():
                match_row = r
                break
                
        if match_row:
            await self.execute("""
                UPDATE memory_items 
                SET last_seen = ?, 
                    mention_count = mention_count + 1,
                    importance = CASE WHEN importance + 0.1 > 1.0 THEN 1.0 ELSE importance + 0.1 END
                WHERE id = ? AND user_id = ?
            """, now, match_row["id"], user_id)
        else:
            await self.execute("""
                INSERT INTO memory_items (user_id, category, content, first_seen, last_seen, mention_count, is_resolved, importance)
                VALUES (?, ?, ?, ?, ?, 1, 0, ?)
            """, user_id, category, content, now, now, importance)
        from app.utils import get_cache
        await get_cache().delete(f"user:memories:{user_id}")
        await get_cache().delete(f"user:profile:{user_id}")

    async def decay_memory_items(self, user_id: int) -> None:
        """Decay importance of memory items and resolve those below threshold (0.15)."""
        await self.execute("""
            UPDATE memory_items
            SET importance = importance * CASE
                WHEN category IN ('goals','relationships','important_events',
                    'personality_traits','fears','aspirations','strengths')
                THEN 0.98 ELSE 0.90 END,
            is_resolved = CASE 
                WHEN importance * CASE
                    WHEN category IN ('goals','relationships','important_events',
                        'personality_traits','fears','aspirations','strengths')
                    THEN 0.98 ELSE 0.90 END < 0.15 
                THEN 1 ELSE 0 END
            WHERE user_id = ? AND is_resolved = 0
        """, user_id)
        from app.utils import get_cache
        await get_cache().delete(f"user:memories:{user_id}")
        await get_cache().delete(f"user:profile:{user_id}")

    async def rebuild_semantic_profile_cache(self, user_id: int) -> dict:
        from app.semantic_engine import DEFAULT_PROFILE
        from copy import deepcopy
        
        rows = await self.fetch("""
            SELECT category, content FROM memory_items 
            WHERE user_id = ? AND is_resolved = 0
        """, user_id)
        
        profile = deepcopy(DEFAULT_PROFILE)
        user_info = await self.get_user(user_id)
        if user_info:
            profile["name"] = user_info.get("first_name")
            
        for r in rows:
            cat = r["category"]
            val = r["content"]
            if cat in profile:
                if isinstance(profile[cat], list):
                    profile[cat].append(val)
                else:
                    profile[cat] = val
            elif cat == "name":
                profile["name"] = val
                
        await self.save_semantic_profile(user_id, profile)
        return profile

    def _parse_diary_row(self, row) -> dict:
        d = dict(row)
        json_fields = ["extracted_goals", "extracted_stressors", "extracted_relationships", "extracted_topics"]
        for f in json_fields:
            if d.get(f):
                try: d[f] = json.loads(d[f])
                except: d[f] = []
        return d

    async def get_active_memories(self, user_id: int) -> list[dict]:
        from app.utils import get_cache
        cache_key = f"user:memories:{user_id}"
        cached = await get_cache().get(cache_key)
        if cached is not None:
            return cached
        res = await self.fetch("""
            SELECT id, category, content FROM memory_items 
            WHERE user_id = ? AND is_resolved = 0
        """, user_id)
        await get_cache().set(cache_key, res, ttl=1800)
        return res

    async def get_all_memories(self, user_id: int) -> list[dict]:
        return await self.fetch("SELECT * FROM memory_items WHERE user_id = ?", user_id)

    async def update_onboarding_data(self, user_id: int, **kwargs) -> None:
        allowed = {"onboarding_status", "age", "nationality", "city", "first_name"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields: return
        fields["updated_at"] = datetime.now().isoformat()
        
        set_clause = ", ".join(f"{k} = ?" for k in fields)
        await self.execute(f"UPDATE users SET {set_clause} WHERE user_id = ?", *(list(fields.values()) + [user_id]))
        from app.utils import get_cache
        await get_cache().delete(f"user:info:{user_id}")

    async def get_episodes_with_embeddings(self, user_id: int) -> list[dict]:
        return await self.fetch("""
            SELECT id, user_id, user_message, bot_response, embedding, timestamp 
            FROM episodes 
            WHERE user_id = ? AND embedding IS NOT NULL
            ORDER BY timestamp DESC LIMIT 50
        """, user_id)

    async def update_episode_embedding(self, user_id: int, episode_id: int, embedding: list[float]) -> None:
        if self._is_postgres:
            await self.execute("UPDATE episodes SET embedding = $1 WHERE id = $2 AND user_id = $3", embedding, episode_id, user_id)
        else:
            vec_blob = json.dumps(embedding).encode('utf-8')
            await self.execute("UPDATE episodes SET embedding = ? WHERE id = ? AND user_id = ?", vec_blob, episode_id, user_id)

    # --- Session CRUD and helper methods ---

    async def create_session(self, session_id: str, user_id: int, start_time: str, date: str) -> None:
        now = datetime.now().isoformat()
        await self.execute("""
            INSERT INTO sessions (session_id, user_id, start_time, date, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, session_id, user_id, start_time, date, now, now)

    async def update_session(self, user_id: int, session_id: str, **kwargs) -> None:
        allowed = {"end_time", "title", "summary", "emotion_metadata", "memories", "importance_score", "embedding"}
        fields = {k: v for k, v in kwargs.items() if k in allowed}
        if not fields: return
        
        if "emotion_metadata" in fields and isinstance(fields["emotion_metadata"], (dict, list)):
            fields["emotion_metadata"] = json.dumps(fields["emotion_metadata"])
        if "memories" in fields and isinstance(fields["memories"], (dict, list)):
            fields["memories"] = json.dumps(fields["memories"])
            
        fields["updated_at"] = datetime.now().isoformat()
        
        if self._is_postgres:
            if "embedding" in fields and isinstance(fields["embedding"], list):
                fields["embedding"] = f"[{','.join(map(str, fields['embedding']))}]"
            
            set_clause = ", ".join(f"{k} = ${i}" for i, k in enumerate(fields, 1))
            await self.execute(f"UPDATE sessions SET {set_clause} WHERE session_id = ${len(fields)+1} AND user_id = ${len(fields)+2}", *(list(fields.values()) + [session_id, user_id]))
        else:
            if "embedding" in fields and isinstance(fields["embedding"], list):
                fields["embedding"] = json.dumps(fields["embedding"]).encode('utf-8')
                
            set_clause = ", ".join(f"{k} = ?" for k in fields)
            await self.execute(f"UPDATE sessions SET {set_clause} WHERE session_id = ? AND user_id = ?", *(list(fields.values()) + [session_id, user_id]))

    async def get_session(self, user_id: int, session_id: str) -> dict | None:
        row = await self.fetchrow("SELECT * FROM sessions WHERE session_id = ? AND user_id = ?", session_id, user_id)
        return self._parse_session_row(row) if row else None

    async def get_all_sessions(self, user_id: int) -> list[dict]:
        rows = await self.fetch("SELECT * FROM sessions WHERE user_id = ? ORDER BY start_time ASC", user_id)
        return [self._parse_session_row(r) for r in rows]

    def _parse_session_row(self, row) -> dict:
        d = dict(row)
        for f in ("emotion_metadata", "memories"):
            if d.get(f):
                try: d[f] = json.loads(d[f])
                except: d[f] = {}
        return d

    async def get_recent_sessions(self, user_id: int, limit: int = 5) -> list[dict]:
        rows = await self.fetch("SELECT * FROM sessions WHERE user_id = ? ORDER BY start_time DESC LIMIT ?", user_id, limit)
        return [self._parse_session_row(r) for r in rows]

    async def get_uncompacted_sessions(self) -> list[dict]:
        rows = await self.fetch("SELECT * FROM sessions WHERE summary IS NULL")
        return [dict(r) for r in rows]

    async def get_episodes_for_session(self, user_id: int, session_id: str) -> list[dict]:
        rows = await self.fetch("SELECT * FROM episodes WHERE user_id = ? AND session_id = ? ORDER BY timestamp ASC", user_id, session_id)
        return [dict(r) for r in rows]

    async def search_sessions(self, user_id: int, query: str, limit: int = 5) -> list[dict]:
        p = f"%{query}%"
        rows = await self.fetch("""
            SELECT * FROM sessions 
            WHERE user_id = ? AND (title LIKE ? OR summary LIKE ? OR memories LIKE ?)
            ORDER BY start_time DESC LIMIT ?
        """, user_id, p, p, p, limit)
        return [self._parse_session_row(r) for r in rows]

    async def get_vector_session_candidates(self, user_id: int, query_vector: list[float], limit: int = 5) -> list[dict]:
        if self._is_postgres:
            vec_str = f"[{','.join(map(str, query_vector))}]"
            rows = await self.fetch("""
                SELECT * FROM sessions 
                WHERE user_id = ? AND embedding IS NOT NULL
                ORDER BY embedding <=> ?::vector
                LIMIT ?
            """, user_id, vec_str, limit)
            return [self._parse_session_row(r) for r in rows]
            
        rows = await self.fetch("SELECT * FROM sessions WHERE user_id = ? AND embedding IS NOT NULL ORDER BY start_time DESC LIMIT 20", user_id)
        if not rows:
            return []
            
        scored = []
        for r in rows:
            try:
                emb_blob = r.get("embedding")
                if not emb_blob:
                    continue
                emb = json.loads(emb_blob.decode('utf-8'))
                
                dot_product = sum(a * b for a, b in zip(query_vector, emb))
                magnitude_q = sum(a * a for a in query_vector) ** 0.5
                magnitude_e = sum(a * a for a in emb) ** 0.5
                sim = dot_product / (magnitude_q * magnitude_e) if magnitude_q * magnitude_e > 0 else 0.0
                scored.append((sim, r))
            except Exception as e:
                logger.error("Failed to compute session similarity: %s", e)
                continue
                
        scored.sort(key=lambda x: x[0], reverse=True)
        return [self._parse_session_row(r) for sim, r in scored[:limit]]


    async def create_backup(self) -> Path | None:
        if self._is_postgres:
            logger.info("Skipping local SQLite backup inside cloud Postgres mode")
            return None
        if not DB_PATH.exists(): return None
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"diary_backup_{ts}.db"
        try:
            await self._db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await asyncio.to_thread(shutil.copy2, str(DB_PATH), str(backup_path))
            backups = sorted(BACKUP_DIR.glob("diary_backup_*.db"))
            if len(backups) > 10:
                for old in backups[:-10]: old.unlink()
            return backup_path
        except Exception as e:
            logger.error("Backup failed: %s", e)
            return None

def get_db() -> DatabaseManager:
    return DatabaseManager()
