"""
Database Models & Query Repository
"""

import os
import aiosqlite
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from config.settings import settings

logger = logging.getLogger(__name__)


async def upsert_user(user_id: int, username: Optional[str], first_name: Optional[str]) -> None:
    """Inserts or updates user information in SQLite."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            INSERT INTO users (user_id, username, first_name)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (user_id, username or "", first_name or ""))
        await db.commit()


async def record_task_start(
    task_id: str,
    user_id: int,
    file_name: str,
    input_size: int,
    duration: float,
    action: str
) -> None:
    """Logs task commencement."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            INSERT INTO tasks (task_id, user_id, file_name, input_size, duration, action, status)
            VALUES (?, ?, ?, ?, ?, ?, 'processing')
            ON CONFLICT(task_id) DO UPDATE SET
                action = excluded.action,
                status = 'processing',
                output_size = 0,
                elapsed_time = 0.0,
                completed_at = NULL
        """, (task_id, user_id, file_name, input_size, duration, action))
        
        # Increment user total tasks
        await db.execute("""
            UPDATE users SET total_tasks = total_tasks + 1 WHERE user_id = ?
        """, (user_id,))
        
        await db.commit()


async def record_task_complete(
    task_id: str,
    output_size: int,
    elapsed_time: float
) -> None:
    """Marks task as successfully completed."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            UPDATE tasks
            SET status = 'completed',
                output_size = ?,
                elapsed_time = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
        """, (output_size, elapsed_time, task_id))
        await db.commit()


async def record_task_failed(task_id: str, elapsed_time: float) -> None:
    """Marks task as failed."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            UPDATE tasks
            SET status = 'failed',
                elapsed_time = ?,
                completed_at = CURRENT_TIMESTAMP
            WHERE task_id = ?
        """, (elapsed_time, task_id))
        await db.commit()


async def register_file_cache(task_id: str, user_id: int, input_path: str, output_path: str) -> None:
    """Registers input and output file paths on server for storage tracking."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("""
            INSERT OR REPLACE INTO file_cache (task_id, user_id, input_path, output_path, input_deleted, output_deleted, deleted)
            VALUES (?, ?, ?, ?, 0, 0, 0)
        """, (task_id, user_id, input_path, output_path))
        await db.commit()


async def get_file_cache(task_id: str) -> Optional[Dict[str, Any]]:
    """Retrieves file cache info for a task."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("SELECT * FROM file_cache WHERE task_id = ?", (task_id,)) as cursor:
            row = await cursor.fetchone()
            if row:
                return dict(row)
    return None


async def mark_input_file_deleted(task_id: str) -> None:
    """Marks input original file as deleted."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("UPDATE file_cache SET input_deleted = 1 WHERE task_id = ?", (task_id,))
        await db.commit()


async def mark_output_file_deleted(task_id: str) -> None:
    """Marks output processed file as deleted."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("UPDATE file_cache SET output_deleted = 1, deleted = 1 WHERE task_id = ?", (task_id,))
        await db.commit()


async def mark_file_cache_deleted(task_id: str) -> None:
    """Marks both file cache entries as deleted."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        await db.execute("UPDATE file_cache SET input_deleted = 1, output_deleted = 1, deleted = 1 WHERE task_id = ?", (task_id,))
        await db.commit()


async def get_user_stats(user_id: int) -> Dict[str, Any]:
    """Computes usage statistics for a specific user."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        
        # Total counts
        async with db.execute("""
            SELECT 
                COUNT(*) as total_tasks,
                SUM(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) as successful_tasks,
                SUM(input_size) as total_input_bytes,
                SUM(output_size) as total_output_bytes,
                AVG(elapsed_time) as avg_time
            FROM tasks
            WHERE user_id = ?
        """, (user_id,)) as cursor:
            stats = dict(await cursor.fetchone() or {})
            
        return stats


async def get_user_recent_tasks(user_id: int, limit: int = 5) -> List[Dict[str, Any]]:
    """Fetches user's most recent tasks."""
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute("""
            SELECT task_id, file_name, input_size, output_size, action, status, created_at
            FROM tasks
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        """, (user_id, limit)) as cursor:
            rows = await cursor.fetchall()
            return [dict(r) for r in rows]


async def get_user_cached_files(user_id: int) -> List[Dict[str, Any]]:
    """Retrieves all cached files for a user that still exist on server disk (deduplicated by path)."""
    items = []
    seen_paths = set()
    async with aiosqlite.connect(settings.DB_PATH) as db:
        db.row_factory = aiosqlite.Row
        async with db.execute(
            "SELECT * FROM file_cache WHERE user_id = ? ORDER BY created_at DESC",
            (user_id,)
        ) as cursor:
            rows = await cursor.fetchall()
            for r in rows:
                data = dict(r)
                task_id = data["task_id"]
                created_at = data.get("created_at", "")

                # Check original input file
                in_path = data.get("input_path")
                if data.get("input_deleted", 0) == 0 and in_path and os.path.exists(in_path):
                    if in_path not in seen_paths:
                        seen_paths.add(in_path)
                        items.append({
                            "task_id": task_id,
                            "file_type": "original",
                            "path": in_path,
                            "name": Path(in_path).name,
                            "size": os.path.getsize(in_path),
                            "created_at": created_at
                        })

                # Check processed output file
                out_path = data.get("output_path")
                if data.get("output_deleted", 0) == 0 and out_path and os.path.exists(out_path):
                    if out_path not in seen_paths:
                        seen_paths.add(out_path)
                        items.append({
                            "task_id": task_id,
                            "file_type": "processed",
                            "path": out_path,
                            "name": Path(out_path).name,
                            "size": os.path.getsize(out_path),
                            "created_at": created_at
                        })
    return items


async def get_single_cached_file(user_id: int, task_id: str, file_type: str) -> Optional[Dict[str, Any]]:
    """Retrieves a single cached file entry if it exists on server disk."""
    cache = await get_file_cache(task_id)
    if not cache or cache.get("user_id") != user_id:
        return None

    path = cache.get("input_path") if file_type == "original" else cache.get("output_path")
    deleted = cache.get("input_deleted") if file_type == "original" else cache.get("output_deleted")
    if deleted or not path or not os.path.exists(path):
        return None

    return {
        "task_id": task_id,
        "file_type": file_type,
        "path": path,
        "name": Path(path).name,
        "size": os.path.getsize(path),
        "created_at": cache.get("created_at", "")
    }


async def get_user_ai_model(user_id: int) -> str:
    """Returns the user's selected AI model or global default."""
    try:
        async with aiosqlite.connect(settings.DB_PATH) as db:
            async with db.execute("SELECT ai_model FROM user_preferences WHERE user_id = ?", (user_id,)) as cursor:
                row = await cursor.fetchone()
                if row and row[0]:
                    return row[0]
    except Exception as e:
        logger.warning(f"Error reading user_preferences: {e}")
    return settings.OPENROUTER_MODEL or "openrouter/free"


async def set_user_ai_model(user_id: int, ai_model: str) -> None:
    """Saves user's chosen AI model."""
    try:
        async with aiosqlite.connect(settings.DB_PATH) as db:
            await db.execute("""
                INSERT INTO user_preferences (user_id, ai_model)
                VALUES (?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    ai_model = excluded.ai_model,
                    updated_at = CURRENT_TIMESTAMP
            """, (user_id, ai_model))
            await db.commit()
    except Exception as e:
        logger.warning(f"Error saving user_preferences: {e}")
