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

