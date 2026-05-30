import os
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from fastapi.testclient import TestClient

# Override DB_PATH to use a separate test database
import app.config
TEST_DB_PATH = Path(app.config.DATA_DIR) / "test_security_diary.db"
app.config.DB_PATH = TEST_DB_PATH
app.config.DATABASE_URL = ""

# Set cron & webhook secrets for testing the webhook setup auth
app.config.CRON_SECRET = "test_security_cron_secret"
app.config.WEBHOOK_SECRET = "test_security_webhook_secret"

from api.index import app as fastapi_app
from app.bot import sanitize_for_prompt
from app.utils import check_rate_limit, _in_mem_counters

@pytest.fixture
def api_client():
    return TestClient(fastapi_app)

def test_prompt_injection_sanitization():
    # 1. Standard messages should remain untouched
    normal_msg = "hello eva, can we talk about python unit testing today?"
    assert sanitize_for_prompt(normal_msg) == normal_msg

    # 2. Messages exceeding length limit should be truncated
    long_msg = "a" * 5000
    assert len(sanitize_for_prompt(long_msg)) == 4000
    assert sanitize_for_prompt(long_msg) == "a" * 4000

def test_setup_webhook_auth(api_client):
    # 1. Request setup-webhook without any credentials -> Forbidden (403)
    response = api_client.get("/setup-webhook")
    assert response.status_code == 403

    # 2. Request setup-webhook with invalid credentials -> Forbidden (403)
    response = api_client.get("/setup-webhook?token=wrong_token")
    assert response.status_code == 403

    response = api_client.get("/setup-webhook", headers={"X-Admin-Token": "wrong_token"})
    assert response.status_code == 403

    # 3. Request setup-webhook with correct credentials via query parameters -> Success (200)
    with patch("telegram.Bot.set_webhook", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = True
        response = api_client.get(f"/setup-webhook?token={app.config.CRON_SECRET}")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        mock_set.assert_called_once()

    # 4. Request setup-webhook with correct credentials via headers -> Success (200)
    with patch("telegram.Bot.set_webhook", new_callable=AsyncMock) as mock_set:
        mock_set.return_value = True
        response = api_client.get("/setup-webhook", headers={"X-Admin-Token": app.config.CRON_SECRET})
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "success"
        mock_set.assert_called_once()

def test_webhook_secret_token_verification(api_client):
    # 1. Post to webhook without secret token header -> 403
    response = api_client.post("/webhook", json={"update_id": 123})
    assert response.status_code == 403

    # 2. Post to webhook with wrong secret token header -> 403
    response = api_client.post("/webhook", json={"update_id": 123}, headers={"X-Telegram-Bot-Api-Secret-Token": "invalid_secret"})
    assert response.status_code == 403

    # 3. Post to webhook with correct secret token -> 200 (Success)
    with patch("app.utils.is_duplicate_update", new_callable=AsyncMock) as mock_dup, \
         patch("telegram.ext.Application.process_update", new_callable=AsyncMock) as mock_proc:
        mock_dup.return_value = False
        mock_proc.return_value = None
        
        response = api_client.post(
            "/webhook",
            json={
                "update_id": 12345,
                "message": {
                    "message_id": 1,
                    "date": 1441645532,
                    "chat": {"id": 1111, "type": "private", "username": "test_user"},
                    "from": {"id": 1111, "first_name": "Test", "is_bot": False},
                    "text": "test message"
                }
            },
            headers={"X-Telegram-Bot-Api-Secret-Token": app.config.WEBHOOK_SECRET}
        )
        assert response.status_code == 200
        mock_dup.assert_called_once()
        mock_proc.assert_called_once()

@pytest.mark.asyncio
async def test_rate_limiting_local_fallback():
    user_id = 999999
    action = "test_rate_limit"
    limit = 3
    window = 10
    
    local_key = f"local:{action}:{user_id}"
    _in_mem_counters.pop(local_key, None)
    
    # 1. Force check_rate_limit to fallback (Redis = None)
    with patch("app.utils.get_session_manager") as mock_manager:
        mock_instance = MagicMock()
        mock_instance._redis = None
        mock_manager.return_value = mock_instance
        
        # Call 1 -> Allowed
        assert await check_rate_limit(user_id, action, limit, window) is True
        # Call 2 -> Allowed
        assert await check_rate_limit(user_id, action, limit, window) is True
        # Call 3 -> Allowed
        assert await check_rate_limit(user_id, action, limit, window) is True
        # Call 4 -> Exceeded (Blocked)
        assert await check_rate_limit(user_id, action, limit, window) is False

    # 2. Verify Redis raising exceptions also falls back gracefully and blocks appropriately
    _in_mem_counters.pop(local_key, None)
    with patch("app.utils.get_session_manager") as mock_manager:
        mock_redis = MagicMock()
        mock_redis.pipeline.side_effect = Exception("Redis connection error")
        mock_instance = MagicMock()
        mock_instance._redis = mock_redis
        mock_manager.return_value = mock_instance
        
        # Call 1 -> Allowed
        assert await check_rate_limit(user_id, action, limit, window) is True
        # Call 2 -> Allowed
        assert await check_rate_limit(user_id, action, limit, window) is True
        # Call 3 -> Allowed
        assert await check_rate_limit(user_id, action, limit, window) is True
        # Call 4 -> Exceeded (Blocked)
        assert await check_rate_limit(user_id, action, limit, window) is False

def test_cron_endpoints_auth(api_client):
    # 1. Request cron/daily without auth -> 401
    response = api_client.get("/cron/daily")
    assert response.status_code == 401

    # 2. Request cron/daily with invalid token -> 401
    response = api_client.get("/cron/daily", headers={"Authorization": "Bearer wrong_token"})
    assert response.status_code == 401

    # 3. Request cron/daily with valid token -> 200 (Success)
    with patch("app.memory_engine.compact_uncompacted_sessions", new_callable=AsyncMock) as mock_compact, \
         patch("api.index.get_db") as mock_get_db:
        
        mock_db = MagicMock()
        mock_db.get_all_users = AsyncMock(return_value=[{"user_id": 12345}])
        mock_get_db.return_value = mock_db
        
        with patch("api.index.check_and_generate_summaries", new_callable=AsyncMock) as mock_summaries, \
             patch("api.index.curate_user_profile", new_callable=AsyncMock) as mock_profile:
            
            response = api_client.get(
                "/cron/daily",
                headers={"Authorization": f"Bearer {app.config.CRON_SECRET}"}
            )
            assert response.status_code == 200
            mock_compact.assert_called_once()
            mock_summaries.assert_called_once_with(12345)
            mock_profile.assert_called_once_with(12345)

    # 4. Request cron/user-daily without auth -> 401
    response = api_client.post("/cron/user-daily", json={"user_id": 12345})
    assert response.status_code == 401

    # 5. Request cron/user-daily with valid auth -> 200 (Success)
    with patch("api.index.check_and_generate_summaries", new_callable=AsyncMock) as mock_summaries, \
         patch("api.index.curate_user_profile", new_callable=AsyncMock) as mock_profile:
        response = api_client.post(
            "/cron/user-daily",
            headers={"Authorization": f"Bearer {app.config.CRON_SECRET}"},
            json={"user_id": 12345}
        )
        assert response.status_code == 200
        mock_summaries.assert_called_once_with(12345)
        mock_profile.assert_called_once_with(12345)
