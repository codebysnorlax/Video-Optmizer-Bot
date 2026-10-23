from .db import init_db, get_connection
from .models import (
    upsert_user,
    record_task_start,
    record_task_complete,
    record_task_failed,
    register_file_cache,
    get_file_cache,
    mark_input_file_deleted,
    mark_output_file_deleted,
    mark_file_cache_deleted,
    get_user_stats,
    get_user_recent_tasks
)

__all__ = [
    "init_db",
    "get_connection",
    "upsert_user",
    "record_task_start",
    "record_task_complete",
    "record_task_failed",
    "register_file_cache",
    "get_file_cache",
    "mark_input_file_deleted",
    "mark_output_file_deleted",
    "mark_file_cache_deleted",
    "get_user_stats",
    "get_user_recent_tasks"
]
