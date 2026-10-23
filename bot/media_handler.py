"""
Incoming Media Handler & Streaming Downloader
Handles video files, forwarded media, and large document attachments (up to 2GB).
"""

import time
import uuid
import logging
from pathlib import Path
from telethon import events
from config.settings import settings
from core.probe import probe_media
from core.hardware import current_hardware
from database.models import upsert_user
from bot.keyboards import build_main_menu
from bot.progress import ThrottledMessageUpdater, format_progress_status
from bot.task_manager import task_manager, TaskContext

logger = logging.getLogger(__name__)


def is_video_message(message) -> bool:
    """Checks if message contains a video or video-like document."""
    if message.video:
        return True
    if message.document:
        mime = message.document.mime_type or ""
        if mime.startswith("video/"):
            return True
        # Check common video extensions in document file name
        exts = [".mp4", ".mkv", ".avi", ".mov", ".webm", ".flv", ".wmv", ".ts", ".m4v", ".3gp", ".mpg", ".mpeg"]
        name = getattr(message.file, "name", "") or ""
        if any(name.lower().endswith(ext) for ext in exts):
            return True
    return False


async def handle_incoming_media(event: events.NewMessage.Event, client) -> None:
    """Processes newly received video or document."""
    message = event.message
    if not is_video_message(message):
        return

    user = await event.get_sender()
    user_id = event.sender_id
    username = getattr(user, "username", "")
    first_name = getattr(user, "first_name", "")

    # Save/update user profile in DB
    await upsert_user(user_id, username, first_name)

    task_id = uuid.uuid4().hex[:10]
    file_name = getattr(message.file, "name", None) or f"video_{task_id}.mp4"
    
    # Sanitize file name
    clean_name = "".join(c for c in file_name if c.isalnum() or c in (".", "_", "-")).strip()
    if not clean_name:
        clean_name = f"video_{task_id}.mp4"

    save_path = settings.DOWNLOAD_DIR / f"{task_id}_{clean_name}"

    status_msg = await event.reply("*Receiving video from Telegram...*\nConnecting to stream...")
    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    start_time = time.time()

    async def download_progress(current_bytes: int, total_bytes: int):
        text = format_progress_status(
            action_label="*Downloading Video from Telegram:*",
            current_bytes=current_bytes,
            total_bytes=total_bytes,
            start_time=start_time
        )
        await updater.update(text)

    try:
        # Download media natively via Telethon MTProto chunked stream
        await client.download_media(
            message,
            file=str(save_path),
            progress_callback=download_progress
        )
        
        await updater.update("*Analyzing video streams with FFprobe...*", force=True)

        # Probe video
        meta = await probe_media(save_path)

        # Build analysis card and main menu
        summary_text = meta.get_summary_card(hardware_summary=current_hardware.description)
        main_menu = build_main_menu(task_id)

        # Register task in memory
        task_ctx = TaskContext(
            task_id=task_id,
            user_id=user_id,
            input_path=save_path,
            output_path=None,
            meta=meta,
            status_message=status_msg,
            original_message_id=message.id
        )
        task_manager.register_task(task_ctx)

        # Prompt user with options
        await status_msg.edit(summary_text, buttons=main_menu)

    except Exception as e:
        logger.error(f"Error handling incoming video: {e}", exc_info=True)
        if save_path.exists():
            try:
                save_path.unlink()
            except Exception:
                pass
        await status_msg.edit(f"[ERROR] *Failed to download or analyze video:*\n`{str(e)}`")
