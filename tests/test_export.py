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
