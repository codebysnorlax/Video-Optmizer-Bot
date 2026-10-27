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

        # File cache table (keeps track of server files to prompt user for separate deletion)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS file_cache (
                task_id TEXT PRIMARY KEY,
                user_id INTEGER,
                input_path TEXT,
                output_path TEXT,
                input_deleted INTEGER DEFAULT 0,
                output_deleted INTEGER DEFAULT 0,
                deleted INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # User preferences table (e.g. AI model selection)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS user_preferences (
                user_id INTEGER PRIMARY KEY,
                ai_model TEXT,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)

        # AI session persistence table (conversation history, assets, agent config)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS ai_sessions (
                user_id INTEGER,
                task_id TEXT,
                history_json TEXT,
                assets_json TEXT,
                preferred_model TEXT,
                input_path TEXT,
                is_active INTEGER DEFAULT 1,
                last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                PRIMARY KEY (user_id, task_id)
            )
        """)

        # Backward compatibility column check
        async with db.execute("PRAGMA table_info(file_cache)") as cursor:
            cols = [row[1] for row in await cursor.fetchall()]
            if "input_deleted" not in cols:
                await db.execute("ALTER TABLE file_cache ADD COLUMN input_deleted INTEGER DEFAULT 0")
            if "output_deleted" not in cols:
                await db.execute("ALTER TABLE file_cache ADD COLUMN output_deleted INTEGER DEFAULT 0")

        await db.commit()
    logger.info(f"Database initialized successfully at {db_path}")


async def get_connection() -> aiosqlite.Connection:
    """Returns an active SQLite connection."""
    return await aiosqlite.connect(settings.DB_PATH)
