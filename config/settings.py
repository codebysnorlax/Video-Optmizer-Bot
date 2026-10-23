"""
Application Settings & Configuration Loader
"""

import os
import shutil
from pathlib import Path
from dataclasses import dataclass
from dotenv import load_dotenv

# Base directory
BASE_DIR = Path(__file__).resolve().parent.parent

# Load .env file
load_dotenv(BASE_DIR / ".env")


@dataclass
class Settings:
    # Telegram Credentials
    BOT_TOKEN: str = os.getenv("BOT_TOKEN", "").strip()
    TELEGRAM_API_ID: int = int(os.getenv("TELEGRAM_API_ID", "0"))
    TELEGRAM_API_HASH: str = os.getenv("TELEGRAM_API_HASH", "").strip()

    # Storage Paths
    DOWNLOAD_DIR: Path = BASE_DIR / os.getenv("DOWNLOAD_DIR", "storage/downloads")
    OUTPUT_DIR: Path = BASE_DIR / os.getenv("OUTPUT_DIR", "storage/outputs")
    DB_PATH: Path = BASE_DIR / os.getenv("DB_PATH", "database/optimizer.db")

    # Performance
    MAX_CONCURRENT_ENCODING: int = int(os.getenv("MAX_CONCURRENT_ENCODING", "1"))
    PROGRESS_UPDATE_INTERVAL: float = float(os.getenv("PROGRESS_UPDATE_INTERVAL", "2.5"))

    # Audio/Video Encoding Defaults
    DEFAULT_AUDIO_BITRATE: str = os.getenv("DEFAULT_AUDIO_BITRATE", "128k")
    MAX_VIDEO_BITRATE: str = os.getenv("MAX_VIDEO_BITRATE", "6000k")

    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        self.DOWNLOAD_DIR.mkdir(parents=True, exist_ok=True)
        self.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        self.DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    def get_disk_usage(self) -> dict:
        """Return free, total, and used disk space for the storage mount."""
        path_to_check = self.DOWNLOAD_DIR if self.DOWNLOAD_DIR.exists() else BASE_DIR
        total, used, free = shutil.disk_usage(path_to_check)
        free_gb = free / (1024 ** 3)
        total_gb = total / (1024 ** 3)
        used_gb = used / (1024 ** 3)
        percent_used = (used / total) * 100
        return {
            "free_gb": round(free_gb, 2),
            "total_gb": round(total_gb, 2),
            "used_gb": round(used_gb, 2),
            "percent_used": round(percent_used, 1),
            "formatted": f"{round(free_gb, 2)} GB free / {round(total_gb, 2)} GB total ({round(percent_used, 1)}% used)"
        }


settings = Settings()
settings.ensure_directories()
