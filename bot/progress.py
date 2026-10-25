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
                await self.message.edit(text, buttons=None)
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
        f"**Progress:** {bar}\n"
        f"**Size:** `{cur_mb:.1f} MB / {tot_mb:.1f} MB`\n"
        f"**Speed:** `{speed_mb:.1f} MB/s` | **ETA:** `{eta}s`"
    )


def format_time_breakdown(
    download_time: float,
    probe_time: float,
    transcode_time: float,
    upload_time: float,
    total_pipeline_time: float,
    total_overall_time: float
) -> str:
    """Renders a clean text breakdown of all processing stage times."""
    return (
        f"**Time Details:**\n"
        f"• **Receive & Download:** `{download_time:.1f}s`\n"
        f"• **Media Analysis:** `{probe_time:.1f}s`\n"
        f"• **Transcode / Processing:** `{transcode_time:.1f}s`\n"
        f"• **Upload to Telegram:** `{upload_time:.1f}s`\n"
        f"• **Total Active Pipeline:** `{total_pipeline_time:.1f}s`\n"
        f"• **Total Overall Time:** `{total_overall_time:.1f}s`"
    )


def format_confirm_stop_prompt(
    stage_label: str,
    live_progress_text: str = "",
    details_block: str = ""
) -> str:
    """Renders 2-step confirmation prompt displaying that processing continues actively in background."""
    parts = [
        "**Confirm Stop Active Process**\n",
        f"Are you 100% sure you want to stop active **{stage_label}**?\n",
        "**Notice:** Processing & media transfer are **STILL actively running in the background** and will NOT stop until you confirm 100% sure below.\n"
    ]
    if live_progress_text:
        parts.append(f"**Live Background Progress:**\n{live_progress_text}\n")
    if details_block:
        parts.append(f"**File & Quality Details:**\n{details_block}\n")
    parts.append(f"If you confirm, the current {stage_label.lower()} operation will be terminated immediately.")
    return "\n".join(parts)


def format_confirm_stop_recall_prompt(
    file_name: str,
    live_progress_text: str = "",
    details_block: str = ""
) -> str:
    """Renders 2-step confirmation prompt for recall transfers showing background continuation."""
    parts = [
        "**Confirm Stop Sending**\n",
        f"Are you 100% sure you want to stop sending **{file_name}**?\n",
        "**Notice:** File transfer is **STILL actively sending in the background** and will NOT stop until you confirm 100% sure below.\n"
    ]
    if live_progress_text:
        parts.append(f"**Live Background Progress:**\n{live_progress_text}\n")
    if details_block:
        parts.append(f"**File & Quality Details:**\n{details_block}\n")
    parts.append("If you confirm, the file transfer will be aborted immediately.")
    return "\n".join(parts)


def format_queue_status(
    position: int,
    action_label: str,
    file_name: str,
    active_jobs: int = 1,
    total_queued: int = 1
) -> str:
    """Formats queue waiting status message with position in queue."""
    return (
        "**Server is Busy — Task Queued**\n\n"
        "The server is currently processing media for another task.\n"
        "Your request is queued and will start automatically as soon as the active job completes.\n\n"
        f"• **Position in Queue:** `#{position}`\n"
        f"• **Active Processing Jobs:** `{active_jobs}`\n"
        f"• **Total Waiting in Queue:** `{total_queued}`\n"
        f"• **Requested Action:** `{action_label}`\n"
        f"• **File Name:** `{file_name}`\n\n"
        "Please wait, this message will update automatically as the queue advances."
    )

