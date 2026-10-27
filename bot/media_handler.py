"""
Incoming Media Handler & Streaming Downloader
Handles video files, forwarded media, and large document attachments (up to 2GB).
"""

import time
import uuid
import logging
import asyncio
from pathlib import Path
import shutil
from typing import Optional
from telethon import events
from config.settings import settings
from core.probe import probe_media, clean_display_name
from core.hardware import current_hardware
from core.terminal import term_log
from database.models import upsert_user
from bot.keyboards import build_main_menu, build_task_progress_menu, build_confirm_stop_task_menu
from bot.progress import ThrottledMessageUpdater, format_progress_status, format_confirm_stop_prompt, make_progress_bar
from bot.task_manager import task_manager, TaskContext
from core.fast_transfer import fast_download

logger = logging.getLogger(__name__)


def find_existing_server_file(clean_name: str, file_size: Optional[int]) -> Optional[Path]:
    """
    Checks if an identical file is already stored on server disk.
    Matches filename ending with clean_name and matching exact byte size.
    """
    if not clean_name or not file_size or file_size <= 0:
        return None
    try:
        if settings.DOWNLOAD_DIR.exists():
            for p in settings.DOWNLOAD_DIR.iterdir():
                if p.is_file():
                    if (p.name == clean_name or p.name.endswith(f"_{clean_name}")) and p.stat().st_size == file_size:
                        return p
    except Exception as e:
        logger.warning(f"Error checking existing server file: {e}")
    return None


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


def is_audio_message(message) -> bool:
    """Checks if message contains an audio or voice recording."""
    if getattr(message, "audio", None) or getattr(message, "voice", None):
        return True
    doc = getattr(message, "document", None)
    if doc:
        mime = getattr(doc, "mime_type", "") or ""
        if isinstance(mime, str) and mime.startswith("audio/"):
            return True
        exts = [".mp3", ".aac", ".m4a", ".wav", ".flac", ".ogg", ".opus", ".wma"]
        file_obj = getattr(message, "file", None)
        name = getattr(file_obj, "name", "") or ""
        if isinstance(name, str) and any(name.lower().endswith(ext) for ext in exts):
            return True
        attrs = getattr(doc, "attributes", None)
        if attrs and isinstance(attrs, (list, tuple)):
            from telethon.tl.types import DocumentAttributeAudio
            for attr in attrs:
                if isinstance(attr, DocumentAttributeAudio):
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
    message_file_size = getattr(message.file, "size", None) or getattr(message.video, "size", None) or getattr(message.document, "size", None)

    term_log("RECEIVE", f"Incoming video from user {user_id} ({file_name})", task_id=task_id)

    status_msg = await event.reply("**Receiving video from Telegram...**\nConnecting to stream...")
    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    start_time = time.time()
    last_term_log = 0.0

    task_ctx = TaskContext(
        task_id=task_id,
        user_id=user_id,
        input_path=save_path,
        output_path=None,
        meta=None,
        status_message=status_msg,
        original_message_id=message.id,
        t_received=start_time,
        current_stage="downloading",
        active_stage_label="Downloading Video"
    )
    task_manager.register_task(task_ctx)
    task_ctx.active_async_task = asyncio.current_task()

    async def download_progress(current_bytes: int, total_bytes: int):
        nonlocal last_term_log
        if task_ctx.is_cancelled:
            raise asyncio.CancelledError()
        now = time.time()
        percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
        speed_mb = (current_bytes / max(0.1, now - start_time)) / (1024 * 1024)

        if now - last_term_log >= 0.15:
            last_term_log = now
            term_log("DOWNLOAD", f"Downloading {clean_name}", task_id=task_id, progress=percent, extra=f"{speed_mb:.1f} MB/s", live=True)

        if task_ctx.is_confirming_stop:
            bar = make_progress_bar(percent)
            cur_mb = current_bytes / (1024 * 1024)
            tot_mb = total_bytes / (1024 * 1024)
            rem_bytes = max(0, total_bytes - current_bytes)
            speed_bytes = current_bytes / max(0.1, now - start_time)
            eta = int(rem_bytes / speed_bytes) if speed_bytes > 0 else 0
            live_progress = (
                f"• **Progress:** {bar}\n"
                f"• **Downloaded:** `{cur_mb:.1f} MB / {tot_mb:.1f} MB`\n"
                f"• **Speed:** `{speed_mb:.1f} MB/s` | **ETA:** `{eta}s`"
            )
            details_block = f"• **Incoming File:** `{clean_name}`\n• **Total Size:** `{tot_mb:.1f} MB`"
            text = format_confirm_stop_prompt(
                stage_label="Downloading Video",
                live_progress_text=live_progress,
                details_block=details_block
            )
            await updater.update(text, buttons=build_confirm_stop_task_menu(task_id))
            return

        text = format_progress_status(
            action_label="**Downloading Video from Telegram:**",
            current_bytes=current_bytes,
            total_bytes=total_bytes,
            start_time=start_time
        )
        await updater.update(text, buttons=build_task_progress_menu(task_id))

    try:
        # Check for identical cached file on server before downloading
        existing_file = find_existing_server_file(clean_name, message_file_size)
        if existing_file and existing_file.exists():
            term_log("RECEIVE", f"Identical file found on server disk ({existing_file.name}), skipping download", task_id=task_id)
            await status_msg.edit("**Identical video found on server cache! Reusing file...**")
            save_path = existing_file
            download_time = 0.0
        else:
            # Download media via high-speed parallel MTProto stream
            await fast_download(
                client=client,
                location_or_message=message,
                out_path=save_path,
                progress_callback=download_progress
            )
            download_time = round(time.time() - start_time, 1)
            term_log("DOWNLOAD", f"Download finished in {download_time}s: {clean_name}", task_id=task_id, live=False)
        
        term_log("PROBE", f"Inspecting media streams with FFprobe...", task_id=task_id)
        await updater.update("**Analyzing video streams with FFprobe...**", force=True)

        # Probe video
        probe_start = time.time()
        meta = await probe_media(save_path)
        probe_time = round(time.time() - probe_start, 1)
        term_log("PROBE", f"Analyzed in {probe_time}s: {meta.resolution_label} | {meta.duration_seconds:.1f}s | {meta.video_codec} | {meta.overall_bitrate_kbps} kbps", task_id=task_id)

        # Build analysis card and main menu
        summary_text = meta.get_summary_card(hardware_summary=current_hardware.description)
        main_menu = build_main_menu(task_id)

        # Update task context in memory
        task_ctx.input_path = save_path
        task_ctx.meta = meta
        task_ctx.download_time = download_time
        task_ctx.probe_time = probe_time
        task_ctx.active_stage_label = ""

        # Prompt user with options
        await status_msg.edit(summary_text, buttons=main_menu)

    except (asyncio.CancelledError, Exception) as e:
        if task_ctx.is_cancelled or isinstance(e, asyncio.CancelledError):
            term_log("DOWNLOAD", f"Download cancelled by user for {clean_name}", task_id=task_id, live=False)
            if (existing_file is None) and save_path.exists():
                try:
                    save_path.unlink()
                except Exception:
                    pass
            task_manager.remove_task(task_id)
            return

        term_log("ERROR", f"Failed download or analysis: {str(e)}", task_id=task_id)
        logger.error(f"Error handling incoming video: {e}", exc_info=True)
        if (existing_file is None) and save_path.exists():
            try:
                save_path.unlink()
            except Exception:
                pass
        task_manager.remove_task(task_id)
        try:
            await status_msg.edit(f"[ERROR] **Failed to download or analyze video:**\n`{str(e)}`")
        except Exception:
            pass


async def handle_incoming_session_asset(event: events.NewMessage.Event, task_ctx: TaskContext, client) -> None:
    """Attaches a newly uploaded audio or secondary video file to an active AI Manual session."""
    message = event.message
    is_audio = is_audio_message(message)
    media_type = "audio" if is_audio else "video"

    file_name = getattr(message.file, "name", None) or (f"asset_{uuid.uuid4().hex[:6]}.mp3" if is_audio else f"asset_{uuid.uuid4().hex[:6]}.mp4")
    clean_name = "".join(c for c in file_name if c.isalnum() or c in (".", "_", "-")).strip()
    save_path = settings.DOWNLOAD_DIR / f"{task_ctx.task_id}_{clean_name}"

    display_name = clean_display_name(clean_name)
    status_msg = await event.reply(f"**Receiving {media_type} for AI session...**\nConnecting to stream: `{display_name}`")
    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    start_time = time.time()

    async def download_progress(current_bytes: int, total_bytes: int):
        text = format_progress_status(
            action_label=f"**Downloading {media_type.capitalize()} for AI Session:**",
            current_bytes=current_bytes,
            total_bytes=total_bytes,
            start_time=start_time
        )
        await updater.update(text)

    try:
        await fast_download(
            client=client,
            location_or_message=message,
            out_path=save_path,
            progress_callback=download_progress
        )
        await updater.update(f"**Analyzing {media_type} streams with FFprobe...**\n`{display_name}`", force=True)
        meta = await probe_media(save_path)
        asset_key = task_ctx.add_asset(save_path, meta=meta, media_type=media_type)

        from core.ai.debug_logger import ai_debug_logger
        ai_debug_logger.log_session_event(
            user_id=task_ctx.user_id,
            task_id=task_ctx.task_id,
            event_type="asset_uploaded",
            details={
                "asset_key": asset_key,
                "filename": display_name,
                "media_type": media_type,
                "size_bytes": save_path.stat().st_size if save_path.exists() else 0,
                "duration_seconds": meta.duration_seconds if meta else 0.0,
                "resolution": meta.resolution_label if (meta and meta.has_video) else "Audio Track"
            }
        )

        if task_ctx.ai_agent:
            task_ctx.ai_agent.assets = task_ctx.get_assets()
            task_ctx.ai_agent.tool_executor.assets = task_ctx.get_assets()

        await status_msg.edit(
            f"**Asset Added:** `[{asset_key}] {display_name}`\n"
            "AI Video Engineer is resuming your task..."
        )

        from bot.ai_handler import execute_ai_query
        auto_prompt = (
            f"I have uploaded the requested {media_type} file as [{asset_key}] ({display_name}). "
            f"Please proceed with my request."
        )
        task_ctx.status_message = status_msg
        await execute_ai_query(auto_prompt, task_ctx, status_msg, client)

    except Exception as e:
        logger.error(f"Failed to add asset to session: {e}", exc_info=True)
        task_ctx.is_processing = False
        await status_msg.edit(f"**Failed to add {media_type} to AI session:** `{str(e)}`")
