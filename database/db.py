"""
Asynchronous SQLite Database Management
"""

import aiosqlite
import logging
from pathlib import Path
from config.settings import settings

logger = logging.getLogger(__name__)


async def init_db() -> None:
    """Initialize database tables if they do not exist."""
    db_path = settings.DB_PATH
    db_path.parent.mkdir(parents=True, exist_ok=True)

    async with aiosqlite.connect(db_path) as db:
        # Users table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                total_tasks INTEGER DEFAULT 0
            )
        """)

        # Tasks history table
        await db.execute("""
            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER,
                file_name TEXT,
                input_size INTEGER,
                output_size INTEGER DEFAULT 0,
                duration REAL DEFAULT 0.0,
                action TEXT,
                status TEXT,
                elapsed_time REAL DEFAULT 0.0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                completed_at TIMESTAMP,
                FOREIGN KEY (user_id) REFERENCES users (user_id)
            )
        """)

        # File cache table (keeps track of server files to prompt user for deletion)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS file_cache (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER,
                input_path TEXT,
                output_path TEXT,
                deleted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        await db.commit()
    logger.info(f"Database initialized successfully at {db_path}")


async def get_connection() -> aiosqlite.Connection:
    """Returns an active SQLite connection."""
    return await aiosqlite.connect(settings.DB_PATH)
