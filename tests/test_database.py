"""
Unit Tests for Database Operations
"""

import pytest
from pathlib import Path
from config.settings import settings
from database.db import init_db
from database.models import (
    upsert_user,
    record_task_start,
    record_task_complete,
    register_file_cache,
    get_file_cache,
    mark_file_cache_deleted,
    get_user_stats,
    get_user_recent_tasks
)


@pytest.mark.asyncio
async def test_database_crud(tmp_path: Path, monkeypatch):
    test_db = tmp_path / "test_opt.db"
    monkeypatch.setattr(settings, "DB_PATH", test_db)

    await init_db()
    assert test_db.exists()

    user_id = 999999
    await upsert_user(user_id, "testuser", "Tester")

    task_id = "test_task_123"
    await record_task_start(
        task_id=task_id,
        user_id=user_id,
        file_name="demo.mp4",
        input_size=10485760,
        duration=10.0,
        action="optimize_auto"
    )

    await record_task_complete(
        task_id=task_id,
        output_size=2097152,
        elapsed_time=3.5
    )

    # File cache separate deletion tests
    await register_file_cache(task_id, user_id, "/tmp/in.mp4", "/tmp/out.mp4")
    cache = await get_file_cache(task_id)
    assert cache is not None
    assert cache["input_path"] == "/tmp/in.mp4"
    assert cache["input_deleted"] == 0
    assert cache["output_deleted"] == 0

    from database.models import mark_input_file_deleted, mark_output_file_deleted
    await mark_input_file_deleted(task_id)
    cache_in_del = await get_file_cache(task_id)
    assert cache_in_del["input_deleted"] == 1
    assert cache_in_del["output_deleted"] == 0

    await mark_output_file_deleted(task_id)
    cache_out_del = await get_file_cache(task_id)
    assert cache_out_del["output_deleted"] == 1
    assert cache_out_del["deleted"] == 1

    # Stats
    stats = await get_user_stats(user_id)
    assert stats["total_tasks"] == 1
    assert stats["successful_tasks"] == 1
    assert stats["total_input_bytes"] == 10485760
    assert stats["total_output_bytes"] == 2097152

    # History
    history = await get_user_recent_tasks(user_id, limit=5)
    assert len(history) == 1
    assert history[0]["task_id"] == task_id
