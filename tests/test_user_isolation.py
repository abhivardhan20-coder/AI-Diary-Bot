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
