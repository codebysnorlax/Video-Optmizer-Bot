"""
Throttled Live Progress Reporter for Telegram Messages
"""

import time
import math
from telethon.errors import MessageNotModifiedError, FloodWaitError
import logging

logger = logging.getLogger(__name__)


def make_progress_bar(percentage: float, length: int = 10) -> str:
    """Creates a visual progress bar e.g. [██████░░░░]"""
    filled_len = int(round(length * (percentage / 100.0)))
    filled_len = max(0, min(length, filled_len))
    bar = "█" * filled_len + "░" * (length - filled_len)
    return f"[{bar}] {percentage:.1f}%"


class ThrottledMessageUpdater:
    """Throttles Telegram message edits to avoid rate limits."""

    def __init__(self, message, interval: float = 2.5):
        self.message = message
        self.interval = interval
        self.last_update_time = 0.0
        self.last_text = ""

    async def update(self, text: str, force: bool = False, buttons = None) -> None:
        """Edits message if interval has elapsed or force is True."""
        now = time.time()
        if not force and (now - self.last_update_time < self.interval):
            return

        if text == self.last_text and not force:
            return

        self.last_update_time = now
        self.last_text = text

        try:
            if buttons is not None:
                await self.message.edit(text, buttons=buttons)
            else:
                await self.message.edit(text)
        except MessageNotModifiedError:
            pass
        except FloodWaitError as e:
            logger.warning(f"Telegram FloodWait encountered: {e.seconds}s")
        except Exception as e:
            logger.debug(f"Message edit error: {e}")


def format_progress_status(
    action_label: str,
    current_bytes: int,
    total_bytes: int,
    start_time: float
) -> str:
    """Formats download/upload progress status string."""
    now = time.time()
    elapsed = max(0.1, now - start_time)
    speed = current_bytes / elapsed  # bytes/sec

    percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
    bar = make_progress_bar(percent)

    cur_mb = current_bytes / (1024 * 1024)
    tot_mb = total_bytes / (1024 * 1024)
    speed_mb = speed / (1024 * 1024)

    rem_bytes = max(0, total_bytes - current_bytes)
    eta = int(rem_bytes / speed) if speed > 0 else 0

    return (
        f"{action_label}\n"
        f"📊 {bar}\n"
        f"📦 {cur_mb:.1f} MB / {tot_mb:.1f} MB\n"
        f"⚡ Speed: {speed_mb:.1f} MB/s | ⏱ ETA: {eta}s"
    )
