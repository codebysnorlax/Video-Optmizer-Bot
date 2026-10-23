"""
Interactive Button Callback Handler & Transcoding Dispatcher
"""

import os
import time
import uuid
import asyncio
import logging
from pathlib import Path
from telethon import events, Button
from config.settings import settings
from core.probe import (
    format_bytes,
    format_duration,
    probe_media,
    format_before_after_analysis
)
from core.hardware import current_hardware
from core.terminal import term_log
from core.engine import run_ffmpeg_with_progress
from core.optimizer import VideoOptimizer
from core.converter import VideoConverter
from database.models import (
    record_task_start,
    record_task_complete,
    record_task_failed,
    register_file_cache,
    get_file_cache,
    mark_input_file_deleted,
    mark_output_file_deleted,
    mark_file_cache_deleted,
    get_user_cached_files,
    get_single_cached_file
)
from bot.keyboards import (
    build_main_menu,
    build_optimize_menu,
    build_convert_menu,
    build_cleanup_original_menu,
    build_cleanup_output_menu,
    build_files_list_menu,
    build_file_action_menu,
    build_recall_progress_menu,
    build_confirm_stop_menu
)
from bot.progress import (
    ThrottledMessageUpdater,
    format_progress_status,
    make_progress_bar,
    format_time_breakdown
)
from bot.task_manager import task_manager, TaskContext, RecallContext

logger = logging.getLogger(__name__)


async def get_or_restore_task_ctx(task_id: str, event: events.CallbackQuery.Event) -> Optional[TaskContext]:
    """
    Retrieves active TaskContext, or seamlessly reloads it from the server's original downloaded file cache.
    Guarantees that input_path always points to the pristine original file (never an optimized output).
    """
    task_ctx = task_manager.get_task(task_id)
    if task_ctx and task_ctx.input_path and task_ctx.input_path.exists():
        try:
            task_ctx.status_message = await event.get_message()
        except Exception:
            pass
        return task_ctx

    cache = await get_file_cache(task_id)
    in_path = None
    if cache and cache.get("input_path") and (cache.get("input_deleted", 0) == 0):
        cand = Path(cache["input_path"])
        if cand.exists():
            in_path = cand

    if not in_path or not in_path.exists():
        matches = [p for p in settings.DOWNLOAD_DIR.glob(f"{task_id}_*") if p.is_file()]
        if matches and matches[0].exists():
            in_path = matches[0]

    if not in_path or not in_path.exists():
        return None

    try:
        meta = await probe_media(in_path)
        msg = await event.get_message()
        task_ctx = TaskContext(
            task_id=task_id,
            user_id=event.sender_id,
            input_path=in_path,
            output_path=None,
            meta=meta,
            status_message=msg,
            original_message_id=0,
            t_received=time.time(),
            download_time=0.0,
            probe_time=0.0
        )
        task_manager.register_task(task_ctx)
        return task_ctx
    except Exception as e:
        logger.error(f"Failed to restore task context for {task_id}: {e}")
        return None


async def handle_callback_query(event: events.CallbackQuery.Event, client) -> None:
    """Dispatches button clicks to appropriate workflows."""
    data = event.data.decode(errors="ignore")
    parts = data.split(":")
    action_type = parts[0]

    # 1. AI Manual Notice
    if action_type == "ai_manual":
        await event.answer("AI Manual Mode is planned for future updates! Please use Optimize or Convert.", alert=True)
        return

    # 2. Main Menu Navigation
    if action_type == "main_menu":
        task_id = parts[1]
        task_ctx = await get_or_restore_task_ctx(task_id, event)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        menu = build_main_menu(task_id)
        summary = task_ctx.meta.get_summary_card(current_hardware.description)
        await event.edit(summary, buttons=menu)
        return

    # 3. Optimize Menu Sub-options
    if action_type == "opt_menu":
        task_id = parts[1]
        task_ctx = await get_or_restore_task_ctx(task_id, event)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        menu = build_optimize_menu(task_id)
        text = (
            "**Select Optimization Mode:**\n\n"
            "**1. Low Device (Max Compatibility):** Downscales >720p to 720p (never upscales <=720p), caps >60fps at 60fps (never upsamples), moderates high bitrates for older & budget phones.\n"
            "**2. Moderate (Mid-Range Device):** Downscales >1080p to 1080p (never upscales <=1080p), caps >60fps at 60fps, optimizes bitrate for smooth mid-range smartphone playback.\n"
            "**3. Auto Recommended (Smart Fix):** Universal smartphone compatibility, downscales >1080p to 1080p (never upscales), fixes audio issues, strictly reduces file size."
        )
        await event.edit(text, buttons=menu)
        return

    # 4. Convert Menu Sub-options
    if action_type == "conv_menu":
        task_id = parts[1]
        task_ctx = await get_or_restore_task_ctx(task_id, event)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        menu = build_convert_menu(task_id)
        text = (
            "**Select Target Conversion Format:**\n\n"
            "• Quick Fix: `Fix Audio (Smartphone AAC)`\n"
            "• Video: `MP4`, `MKV`, `WEBM`, `AVI`, `MOV`, `MPG`\n"
            "• Audio Extraction: `MP3`, `AAC`"
        )
        await event.edit(text, buttons=menu)
        return

    # 5. Server File Retention Callbacks (Step 1: Original File)
    if action_type in ("del_orig", "keep_orig"):
        task_id = parts[1]
        cache = await get_file_cache(task_id)
        if not cache:
            await event.answer("Session expired or cache not found.", alert=True)
            return

        in_path = cache.get("input_path")
        orig_name = Path(in_path).name if in_path else "original_video"

        if action_type == "del_orig":
            if in_path and os.path.exists(in_path):
                try:
                    os.remove(in_path)
                except Exception as e:
                    logger.error(f"Failed to delete original {in_path}: {e}")
            await mark_input_file_deleted(task_id)
            term_log("STORAGE", f"Deleted original file {orig_name}", task_id=task_id)
            orig_status = "Deleted from server"
            alert_msg = "Original file deleted from server."
        else:
            term_log("STORAGE", f"Kept original file {orig_name}", task_id=task_id)
            orig_status = "Kept in server cache"
            alert_msg = "Original file kept in server cache."

        # Advance to Step 2: Optimized/Processed file deletion
        disk = settings.get_disk_usage()
        out_path = cache.get("output_path")
        opt_name = Path(out_path).name if out_path else "processed_video"
        opt_size = os.path.getsize(out_path) if out_path and os.path.exists(out_path) else 0
        opt_size_str = format_bytes(opt_size)

        step2_text = (
            f"**Step 1 of 2 Complete:**\n"
            f"• **Original File:** `{orig_name}` ({orig_status})\n\n"
            "**Step 2 of 2: Processed File Cleanup**\n"
            f"• **Processed File:** `{opt_name}` ({opt_size_str})\n"
            f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
            "Would you like to delete the processed file from the server now?"
        )
        await event.edit(step2_text, buttons=build_cleanup_output_menu(task_id))
        await event.answer(alert_msg, alert=False)
        return

    # 5b. Server File Retention Callbacks (Step 2: Optimized/Processed File)
    if action_type in ("del_opt", "keep_opt"):
        task_id = parts[1]
        cache = await get_file_cache(task_id)
        if not cache:
            await event.answer("Session expired or cache not found.", alert=True)
            return

        out_path = cache.get("output_path")
        opt_name = Path(out_path).name if out_path else "processed_video"

        if action_type == "del_opt":
            if out_path and os.path.exists(out_path):
                try:
                    os.remove(out_path)
                except Exception as e:
                    logger.error(f"Failed to delete processed {out_path}: {e}")
            await mark_output_file_deleted(task_id)
            term_log("STORAGE", f"Deleted processed file {opt_name}", task_id=task_id)
            opt_status = "Deleted from server"
            alert_msg = "Processed file deleted from server."
        else:
            term_log("STORAGE", f"Kept processed file {opt_name}", task_id=task_id)
            opt_status = "Kept in server cache"
            alert_msg = "Processed file kept in server cache."

        # Fetch updated cache to reflect original file status
        updated_cache = await get_file_cache(task_id) or cache
        orig_deleted = updated_cache.get("input_deleted", 0) == 1
        orig_name = Path(cache.get("input_path", "")).name
        orig_status = "Deleted from server" if orig_deleted else "Kept in server cache"

        task_manager.remove_task(task_id)
        disk = settings.get_disk_usage()

        final_text = (
            "**Server Storage Cleanup Complete:**\n\n"
            f"• **Original File:** `{orig_name}` -> {orig_status}\n"
            f"• **Processed File:** `{opt_name}` -> {opt_status}\n\n"
            f"**Current Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
            "All set! Ready for your next video."
        )
        await event.edit(final_text, buttons=None)
        await event.answer(alert_msg, alert=False)
        return

    # 5c. Try Another Optimization / Change with cached original file
    if action_type == "retry_opt":
        task_id = parts[1]
        task_ctx = await get_or_restore_task_ctx(task_id, event)
        if not task_ctx:
            await event.answer("Original file is no longer available on server. Please send the video again.", alert=True)
            return

        # Ensure output is cleared and fresh original metadata is loaded from original input file
        task_ctx.output_path = None
        task_ctx.meta = await probe_media(task_ctx.input_path)

        term_log("PROCESS", f"Reloaded original source file for re-processing: {task_ctx.input_path.name}", task_id=task_id)
        summary = task_ctx.meta.get_summary_card(current_hardware.description)
        menu = build_main_menu(task_id)

        # Delete the previous reply/cleanup prompt so Telegram does not display the quote of the processed video
        try:
            await event.delete()
        except Exception:
            pass

        # Send a fresh standalone message presenting the original file analysis
        new_msg = await client.send_message(
            entity=event.sender_id,
            message=summary,
            buttons=menu
        )
        task_ctx.status_message = new_msg
        await event.answer("Loaded original file! Select another optimization or conversion mode:", alert=False)
        return

    # 6. Execute Optimization Job
    if action_type == "opt_run":
        sub_mode = parts[1]
        task_id = parts[2]
        task_ctx = await get_or_restore_task_ctx(task_id, event)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        if task_ctx.is_processing:
            await event.answer("Processing is already in progress. Please wait...", alert=True)
            return

        task_ctx.is_processing = True
        task_ctx.output_path = None
        try:
            task_ctx.status_message = await event.get_message()
        except Exception:
            pass

        mode_names = {"low": "Low Device", "mod": "Moderate", "auto": "Auto Recommended"}
        mode_label = mode_names.get(sub_mode, "Optimization")
        # Remove buttons immediately so user cannot click options while processing is running
        await event.edit(f"**Queued for {mode_label}...**\nWaiting for processing slot...", buttons=None)

        term_log("PROCESS", f"User selected Optimization ({sub_mode.upper()}) using original source: {task_ctx.input_path.name}", task_id=task_id)
        await event.answer("Starting optimization...")
        asyncio.create_task(process_optimization(task_ctx, sub_mode, client))
        return

    # 7. Execute Conversion Job
    if action_type == "conv_run":
        target_fmt = parts[1]
        task_id = parts[2]
        task_ctx = await get_or_restore_task_ctx(task_id, event)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        if task_ctx.is_processing:
            await event.answer("Processing is already in progress. Please wait...", alert=True)
            return

        task_ctx.is_processing = True
        task_ctx.output_path = None
        try:
            task_ctx.status_message = await event.get_message()
        except Exception:
            pass

        label = "Smartphone Audio Fix (AAC)" if target_fmt == "audio_fix" else f"Conversion ({target_fmt.upper()})"
        # Remove buttons immediately
        await event.edit(f"**Queued for {label}...**\nWaiting for processing slot...", buttons=None)

        term_log("PROCESS", f"User selected {label} using original source: {task_ctx.input_path.name}", task_id=task_id)
        await event.answer("Fixing audio for smartphones..." if target_fmt == "audio_fix" else f"Converting to {target_fmt.upper()}...")
        asyncio.create_task(process_conversion(task_ctx, target_fmt, client))
        return

    # 8. Files List Navigation & Pagination
    if action_type == "flist":
        page = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0
        user_id = event.sender_id
        files = await get_user_cached_files(user_id)
        disk = settings.get_disk_usage()

        if not files:
            text = (
                "**Server Cached Files**\n\n"
                "You have no active files currently cached on the server.\n"
                "All past files have either been deleted or cleaned up.\n\n"
                f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)"
            )
            await event.edit(text, buttons=[[Button.inline("Refresh List", data=b"flist:0")]])
            await event.answer()
            return

        total_size = sum(f["size"] for f in files)
        text = (
            f"**Server Cached Files ({len(files)} stored)**\n\n"
            f"• **Total Cache Stored:** `{format_bytes(total_size)}`\n"
            f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
            "Select any file below to **Recall** (send back to you) or **Delete** from server:"
        )
        menu = build_files_list_menu(files, page=page)
        await event.edit(text, buttons=menu)
        await event.answer()
        return

    # 9. View Specific Cached File
    if action_type == "fview":
        file_type = parts[1]
        task_id = parts[2]
        user_id = event.sender_id
        file_info = await get_single_cached_file(user_id, task_id, file_type)
        if not file_info:
            await event.answer("This file is no longer available on server.", alert=True)
            files = await get_user_cached_files(user_id)
            if files:
                await event.edit(
                    f"**Server Cached Files ({len(files)} stored):**",
                    buttons=build_files_list_menu(files, page=0)
                )
            return

        disk = settings.get_disk_usage()
        type_label = "Original Source Video" if file_type == "original" else "Processed / Optimized Output"
        text = (
            "**File Details:**\n\n"
            f"• **Name:** `{file_info['name']}`\n"
            f"• **Role:** {type_label}\n"
            f"• **Size:** `{format_bytes(file_info['size'])}`\n"
            f"• **Date Cached:** `{file_info['created_at']}`\n"
            f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB`\n\n"
            "Choose an action below:"
        )
        await event.edit(text, buttons=build_file_action_menu(task_id, file_type))
        await event.answer()
        return

    # 10. Delete Individual Cached File
    if action_type == "del_f":
        file_type = parts[1]
        task_id = parts[2]
        user_id = event.sender_id
        file_info = await get_single_cached_file(user_id, task_id, file_type)
        if not file_info:
            await event.answer("File already removed or not found.", alert=True)
            return

        file_path = file_info["path"]
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except Exception as e:
                logger.error(f"Error removing cached file {file_path}: {e}")

        if file_type == "original":
            await mark_input_file_deleted(task_id)
        else:
            await mark_output_file_deleted(task_id)

        term_log("STORAGE", f"Deleted {file_type} file {file_info['name']}", task_id=task_id)
        disk = settings.get_disk_usage()
        freed = format_bytes(file_info["size"])

        text = (
            "**File Deleted from Server**\n\n"
            f"• **Deleted File:** `{file_info['name']}`\n"
            f"• **Freed Space:** `{freed}`\n"
            f"• **Current Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
            "The file has been permanently removed from the host machine."
        )
        await event.edit(text, buttons=[[Button.inline("< Back to Files List", data=b"flist:0")]])
        await event.answer("File deleted from server.", alert=False)
        return

    # 11. Initiate Recall (Re-send file to user)
    if action_type == "recall":
        file_type = parts[1]
        task_id = parts[2]
        user_id = event.sender_id
        file_info = await get_single_cached_file(user_id, task_id, file_type)
        if not file_info:
            await event.answer("File is no longer available on server.", alert=True)
            return

        recall_id = uuid.uuid4().hex[:8]
        file_path = Path(file_info["path"])
        file_name = file_info["name"]
        file_size = file_info["size"]

        recall_ctx = RecallContext(
            recall_id=recall_id,
            user_id=user_id,
            task_id=task_id,
            file_type=file_type,
            file_path=file_path,
            file_name=file_name,
            file_size=file_size,
            status_msg=event
        )
        task_manager.register_recall(recall_ctx)

        term_log("UPLOAD", f"User requested recall for {file_name}", task_id=task_id)
        await event.answer("Starting recall upload...")

        # Initial status edit with Stop Sending button
        init_text = (
            f"**Recalling `{file_name}` to Telegram:**\n"
            f"**Progress:** [░░░░░░░░░░] 0.0%\n"
            f"**Size:** `0.0 MB / {file_size / (1024 * 1024):.1f} MB`\n"
            "**Status:** Preparing upload..."
        )
        await event.edit(init_text, buttons=build_recall_progress_menu(recall_id))

        upload_task = asyncio.create_task(do_recall_upload(recall_ctx, client))
        recall_ctx.upload_task = upload_task
        return

    # 12. User Clicked Stop Sending -> Show 2-Step Confirmation
    if action_type == "stop_rec":
        recall_id = parts[1]
        recall_ctx = task_manager.get_recall(recall_id)
        if not recall_ctx:
            await event.answer("Transfer is no longer active or already completed.", alert=True)
            return

        recall_ctx.is_confirming_stop = True
        confirm_text = (
            "**Confirm Stop Sending**\n\n"
            f"Are you sure you want to stop sending **{recall_ctx.file_name}**?\n\n"
            "If stopped, the file transfer will be aborted immediately."
        )
        await event.edit(confirm_text, buttons=build_confirm_stop_menu(recall_id))
        await event.answer()
        return

    # 13. Confirmed Stop Sending (Yes)
    if action_type == "yes_stop":
        recall_id = parts[1]
        recall_ctx = task_manager.get_recall(recall_id)
        if not recall_ctx:
            await event.edit(
                "**Transfer Already Ended**\n\nThe upload has already stopped or completed.",
                buttons=[[Button.inline("< Back to Files List", data=b"flist:0")]]
            )
            await event.answer()
            return

        recall_ctx.is_cancelled = True
        recall_ctx.is_confirming_stop = False
        if recall_ctx.upload_task and not recall_ctx.upload_task.done():
            recall_ctx.upload_task.cancel()
        task_manager.remove_recall(recall_id)

        term_log("UPLOAD", f"User confirmed STOP transfer for {recall_ctx.file_name}", task_id=recall_ctx.task_id)
        stop_text = (
            "**Recall Stopped**\n\n"
            f"The file upload for **{recall_ctx.file_name}** was cancelled by user."
        )
        await event.edit(stop_text, buttons=[[Button.inline("< Back to Files List", data=b"flist:0")]])
        await event.answer("Transfer cancelled.", alert=False)
        return

    # 14. Abort Stop Sending (No, Continue Sending)
    if action_type == "no_stop":
        recall_id = parts[1]
        recall_ctx = task_manager.get_recall(recall_id)
        if not recall_ctx:
            await event.answer("Transfer has already completed or expired.", alert=True)
            return

        recall_ctx.is_confirming_stop = False
        resume_text = (
            f"**Recalling `{recall_ctx.file_name}` to Telegram:**\n"
            "**Status:** Resuming transfer..."
        )
        await event.edit(resume_text, buttons=build_recall_progress_menu(recall_id))
        await event.answer("Resuming upload...", alert=False)
        return


async def do_recall_upload(recall_ctx: RecallContext, client) -> None:
    """Handles background upload of recalled file with live progress and cancellation."""
    task_id = recall_ctx.task_id
    user_id = recall_ctx.user_id
    recall_id = recall_ctx.recall_id
    file_path = recall_ctx.file_path
    file_name = recall_ctx.file_name
    file_size = recall_ctx.file_size
    msg = recall_ctx.status_msg

    updater = ThrottledMessageUpdater(msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    upload_start = time.time()
    last_log = 0.0

    async def recall_progress(current_bytes: int, total_bytes: int):
        nonlocal last_log
        if recall_ctx.is_cancelled:
            raise asyncio.CancelledError()
        if recall_ctx.is_confirming_stop:
            return  # Do not overwrite confirmation prompt

        now = time.time()
        percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
        if now - last_log >= 0.15:
            last_log = now
            term_log("UPLOAD", f"Recalling {file_name}", task_id=task_id, progress=percent, live=True)

        text = format_progress_status(
            action_label=f"**Recalling `{file_name}` to Telegram:**",
            current_bytes=current_bytes,
            total_bytes=total_bytes,
            start_time=upload_start
        )
        await updater.update(text, buttons=build_recall_progress_menu(recall_id))

    try:
        ext = file_path.suffix.lower()
        is_audio = ext in (".mp3", ".aac", ".wav", ".m4a", ".flac")
        if is_audio:
            await client.send_file(
                entity=user_id,
                file=str(file_path),
                caption=f"`{file_name}`",
                voice_note=False,
                progress_callback=recall_progress
            )
        else:
            await client.send_file(
                entity=user_id,
                file=str(file_path),
                caption=f"`{file_name}`",
                supports_streaming=True,
                progress_callback=recall_progress
            )

        if not recall_ctx.is_cancelled:
            upload_time = round(time.time() - upload_start, 1)
            term_log("UPLOAD", f"Recall completed for {file_name} in {upload_time}s", task_id=task_id, live=False)
            complete_text = (
                "**File Recall Complete!**\n\n"
                f"• **File:** `{file_name}`\n"
                f"• **Size:** `{format_bytes(file_size)}`\n"
                f"• **Transfer Time:** `{upload_time}s`\n"
                "• **Status:** Delivered to your chat."
            )
            try:
                await msg.edit(complete_text, buttons=[[Button.inline("< Back to Files List", data=b"flist:0")]])
            except Exception:
                pass
    except (asyncio.CancelledError, Exception) as e:
        if recall_ctx.is_cancelled or isinstance(e, asyncio.CancelledError):
            term_log("UPLOAD", f"Recall transfer cancelled for {file_name}", task_id=task_id, live=False)
            try:
                await msg.edit(
                    f"**Recall Cancelled**\n\nThe file transfer for `{file_name}` was stopped.",
                    buttons=[[Button.inline("< Back to Files List", data=b"flist:0")]]
                )
            except Exception:
                pass
        else:
            logger.error(f"Recall upload failed for {file_name}: {e}", exc_info=True)
            try:
                await msg.edit(
                    f"[ERROR] **Recall Failed:**\n`{str(e)}`",
                    buttons=[[Button.inline("< Back to Files List", data=b"flist:0")]]
                )
            except Exception:
                pass
    finally:
        task_manager.remove_recall(recall_id)


async def process_optimization(task_ctx: TaskContext, mode: str, client) -> None:
    """Executes the chosen video optimization pipeline."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id
    meta = task_ctx.meta
    status_msg = task_ctx.status_message
    
    mode_names = {
        "low": "Low Device Optimize",
        "mod": "Moderate Optimize",
        "auto": "Auto Recommended"
    }
    mode_label = mode_names.get(mode, "Optimization")
    
    out_name = f"opt_{mode}_{task_ctx.input_path.stem}.mp4"
    out_path = settings.OUTPUT_DIR / f"{task_id}_{out_name}"
    task_ctx.output_path = out_path

    # Log task start in DB
    await record_task_start(
        task_id=task_id,
        user_id=user_id,
        file_name=meta.file_name,
        input_size=meta.file_size_bytes,
        duration=meta.duration_seconds,
        action=f"optimize_{mode}"
    )

    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    await updater.update(f"**Queued for {mode_label}...**\nWaiting for processing slot...", force=True)

    start_exec = time.time()

    async with task_manager.semaphore:
        try:
            # Build optimization arguments
            explanation = ""
            if mode == "low":
                cmd, explanation = VideoOptimizer.build_low_device_args(meta, out_path, current_hardware)
            elif mode == "mod":
                cmd, explanation = VideoOptimizer.build_moderate_args(meta, out_path, current_hardware)
            else:
                cmd, explanation = VideoOptimizer.build_auto_recommended_args(meta, out_path, current_hardware)

            active_engine_str = current_hardware.description if "-c:v" in cmd and "h264_vaapi" in cmd else (
                "Pristine Stream (FastStart)" if "-c:v" in cmd and "copy" in cmd else f"CPU Multi-Thread ({current_hardware.cpu_cores} cores)"
            )

            last_proc_log = 0.0
            async def transcode_progress(percent: float, speed: str, eta: int, fps: float):
                nonlocal last_proc_log
                now = time.time()
                eta_str = f"{eta}s" if eta is not None else "--"
                if now - last_proc_log >= 0.15:
                    last_proc_log = now
                    term_log("PROCESS", f"Transcoding ({mode_label})", task_id=task_id, progress=percent, extra=f"speed {speed}, fps {fps:.1f}, eta {eta_str}", live=True)

                bar = make_progress_bar(percent)
                text = (
                    f"**{mode_label} in Progress:**\n"
                    f"**Progress:** {bar}\n"
                    f"**Speed:** `{speed}` | **FPS:** `{fps:.1f}` | **ETA:** `{eta_str}`\n"
                    f"**Engine:** `{active_engine_str}`"
                )
                await updater.update(text)

            term_log("PROCESS", f"Starting FFmpeg transcode pipeline ({mode_label})", task_id=task_id)
            t_transcode_start = time.time()
            # Run FFmpeg transcode with 0.2s interval for smooth terminal rendering
            await run_ffmpeg_with_progress(
                cmd=cmd,
                total_duration_seconds=meta.duration_seconds,
                progress_callback=transcode_progress,
                progress_interval=0.2
            )
            transcode_time = round(time.time() - t_transcode_start, 1)
            term_log("PROCESS", f"Transcode finished ({mode_label}) in {transcode_time}s", task_id=task_id, live=False)

            # Transcode completed
            out_size = os.path.getsize(out_path) if out_path.exists() else 0

            # Absolute Failsafe: An optimizer must NEVER deliver an output larger than the input!
            if out_size >= meta.file_size_bytes:
                term_log("PROCESS", f"Initial output ({format_bytes(out_size)}) >= input ({format_bytes(meta.file_size_bytes)}). Running second-pass safe bitrate compression.", task_id=task_id)
                dur = max(meta.duration_seconds, 1.0)
                safe_kbps = max(200, int((meta.file_size_bytes * 8 * 0.65) / (dur * 1000)))
                safe_maxrate = safe_kbps
                safe_bufsize = safe_kbps * 2

                cap_dim = 720 if mode == "low" else 1080
                scale_part = VideoOptimizer.get_scale_filter(meta, cap_dim)
                fps_part = VideoOptimizer.get_fps_filter(meta, 60.0)
                filters = [f for f in [scale_part, fps_part, "format=yuv420p"] if f]
                vf_arg = ",".join(filters)

                fallback_out = settings.OUTPUT_DIR / f"{task_id}_safe_{task_ctx.input_path.stem}.mp4"
                cmd_safe = [
                    "-i", str(task_ctx.input_path),
                    "-c:v", "libx264",
                    "-preset", "veryfast",
                    "-b:v", f"{safe_kbps}k",
                    "-maxrate", f"{safe_maxrate}k",
                    "-bufsize", f"{safe_bufsize}k",
                    "-threads", str(current_hardware.cpu_cores),
                ]
                if vf_arg:
                    cmd_safe.extend(["-vf", vf_arg])
                if meta.has_audio:
                    cmd_safe.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
                else:
                    cmd_safe.extend(["-an"])
                cmd_safe.extend(["-movflags", "+faststart", str(fallback_out)])

                try:
                    await run_ffmpeg_with_progress(
                        cmd=cmd_safe,
                        total_duration_seconds=meta.duration_seconds,
                        progress_callback=transcode_progress,
                        progress_interval=0.2
                    )
                except Exception as e:
                    logger.warning(f"Safe second pass transcode failed: {e}")

                if fallback_out.exists() and os.path.getsize(fallback_out) < meta.file_size_bytes:
                    try:
                        out_path.unlink()
                    except Exception:
                        pass
                    out_path = fallback_out
                    task_ctx.output_path = out_path
                    out_size = os.path.getsize(out_path)
                    explanation += f"\n • Applied safe bitrate constraint ({safe_kbps} kbps) ensuring strictly reduced size."
                else:
                    # Last resort fallback: FastStart stream copy
                    cmd_fallback = [
                        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                        "-i", str(task_ctx.input_path),
                        "-c", "copy",
                        "-movflags", "+faststart",
                        str(fallback_out)
                    ]
                    proc = await asyncio.create_subprocess_exec(*cmd_fallback)
                    await proc.wait()
                    if fallback_out.exists():
                        try:
                            out_path.unlink()
                        except Exception:
                            pass
                        out_path = fallback_out
                        task_ctx.output_path = out_path
                        out_size = os.path.getsize(out_path)
                        explanation = " • Original video was already at maximum compression.\n • Preserved original pristine stream with FastStart enabled for instant streaming."

            # Register in file cache
            await register_file_cache(
                task_id=task_id,
                user_id=user_id,
                input_path=str(task_ctx.input_path),
                output_path=str(out_path)
            )

            # Upload to Telegram with live progress
            upload_start = time.time()
            last_up_log = 0.0
            async def upload_progress(current_bytes: int, total_bytes: int):
                nonlocal last_up_log
                now = time.time()
                percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
                if now - last_up_log >= 0.15:
                    last_up_log = now
                    term_log("UPLOAD", f"Uploading optimized video", task_id=task_id, progress=percent, live=True)

                text = format_progress_status(
                    action_label="**Uploading Optimized Video to Telegram:**",
                    current_bytes=current_bytes,
                    total_bytes=total_bytes,
                    start_time=upload_start
                )
                await updater.update(text)

            # Calculate savings
            if out_size < meta.file_size_bytes:
                saved_bytes = meta.file_size_bytes - out_size
                ratio = (saved_bytes / meta.file_size_bytes * 100)
                reduction_str = f"{ratio:.1f}% Saved ({format_bytes(saved_bytes)})"
            elif out_size == meta.file_size_bytes:
                reduction_str = "Identical Size"
            else:
                inc_bytes = out_size - meta.file_size_bytes
                inc_pct = (inc_bytes / meta.file_size_bytes * 100)
                reduction_str = f"+{inc_pct:.1f}% ({format_bytes(inc_bytes)} added for standard compatibility)"

            # 1. Probe output media for Before & After comparison and rich video caption
            meta_after = await probe_media(out_path)
            v_codec = (meta_after.video_codec or "h264").upper()
            a_codec = (meta_after.audio_codec or "none").upper()
            fps_val = round(meta_after.fps) if meta_after.fps else 0
            fps_str = f" @ {fps_val}fps" if fps_val > 0 else ""

            video_caption = (
                f"**File:** `{out_name}`\n"
                f"• **Quality:** {meta_after.resolution_label}{fps_str}\n"
                f"• **Size:** {format_bytes(out_size)} ({reduction_str})\n"
                f"• **Format:** MP4 ({v_codec} / {a_codec})"
            )

            # 2. Send media file with structured caption
            sent_msg = await client.send_file(
                entity=user_id,
                file=str(out_path),
                caption=video_caption,
                supports_streaming=True,
                progress_callback=upload_progress
            )
            upload_time = round(time.time() - upload_start, 1)
            term_log("UPLOAD", f"Upload finished in {upload_time}s", task_id=task_id, live=False)

            total_pipeline_time = round(task_ctx.download_time + task_ctx.probe_time + transcode_time + upload_time, 1)
            total_overall_time = round(time.time() - task_ctx.t_received, 1) if task_ctx.t_received > 0 else total_pipeline_time

            analysis_md = format_before_after_analysis(before=meta, after=meta_after)

            # Format full time breakdown
            time_breakdown_str = format_time_breakdown(
                download_time=task_ctx.download_time,
                probe_time=task_ctx.probe_time,
                transcode_time=transcode_time,
                upload_time=upload_time,
                total_pipeline_time=total_pipeline_time,
                total_overall_time=total_overall_time
            )

            # 3. Assemble full report text
            disk = settings.get_disk_usage()
            orig_name = Path(task_ctx.input_path).name
            orig_size_str = format_bytes(meta.file_size_bytes)

            report_parts = [
                f"**{mode_label} Complete!**\n",
                analysis_md,
                "\n" + time_breakdown_str,
                f"\n• **Engine:** `{active_engine_str}`"
            ]
            if explanation:
                report_parts.append(f"\n• **Fixes Applied:**\n{explanation}")

            report_parts.append(
                "\n**Step 1 of 2: Original File Cleanup**\n"
                f"• **Original Source File:** `{orig_name}` ({orig_size_str})\n"
                f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
                "Would you like to delete the original source file from the server now?"
            )
            report_text = "\n".join(report_parts)

            # 4. Send reply message directly replying to the delivered video
            reply_msg = await client.send_message(
                entity=user_id,
                message=report_text,
                reply_to=sent_msg.id,
                buttons=build_cleanup_original_menu(task_id)
            )

            # 5. Delete previous progress status message
            try:
                await status_msg.delete()
            except Exception:
                pass

            task_ctx.status_message = reply_msg

            # Record completion in DB
            await record_task_complete(task_id, out_size, total_pipeline_time)
            term_log(
                "DONE",
                f"Delivered to user! Output: {format_bytes(out_size)} | {reduction_str} | Total: {total_pipeline_time}s (Overall: {total_overall_time}s) [Down: {task_ctx.download_time}s, Probe: {task_ctx.probe_time}s, Transcode: {transcode_time}s, Up: {upload_time}s]",
                task_id=task_id
            )

        except Exception as e:
            logger.error(f"Optimization error for task {task_id}: {e}", exc_info=True)
            elapsed_time = round(time.time() - start_exec, 1)
            await record_task_failed(task_id, elapsed_time)
            await status_msg.edit(f"[ERROR] **Optimization Failed:**\n`{str(e)}`")
        finally:
            task_ctx.is_processing = False


async def process_conversion(task_ctx: TaskContext, target_fmt: str, client) -> None:
    """Executes the chosen format conversion pipeline."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id
    meta = task_ctx.meta
    status_msg = task_ctx.status_message
    
    if target_fmt == "audio_fix":
        conv_label = "Smartphone Audio Fix (AAC)"
        out_name = f"audiofix_{task_ctx.input_path.stem}.mp4"
    else:
        conv_label = f"Conversion to {target_fmt.upper()}"
        out_name = f"conv_{task_ctx.input_path.stem}.{target_fmt}"
    
    out_path = settings.OUTPUT_DIR / f"{task_id}_{out_name}"
    task_ctx.output_path = out_path

    # Log task start in DB
    await record_task_start(
        task_id=task_id,
        user_id=user_id,
        file_name=meta.file_name,
        input_size=meta.file_size_bytes,
        duration=meta.duration_seconds,
        action=f"convert_{target_fmt}"
    )

    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    await updater.update(f"**Queued for {conv_label}...**\nWaiting for processing slot...", force=True)

    start_exec = time.time()

    async with task_manager.semaphore:
        try:
            cmd, is_audio = VideoConverter.build_convert_args(meta, target_fmt, out_path, current_hardware)

            term_log("PROCESS", f"Starting conversion pipeline ({target_fmt.upper()})", task_id=task_id)
            last_conv_log = 0.0
            async def transcode_progress(percent: float, speed: str, eta: int, fps: float):
                nonlocal last_conv_log
                now = time.time()
                eta_str = f"{eta}s" if eta is not None else "--"
                if now - last_conv_log >= 0.15:
                    last_conv_log = now
                    term_log("PROCESS", f"Converting ({target_fmt.upper()})", task_id=task_id, progress=percent, extra=f"speed {speed}, eta {eta_str}", live=True)

                bar = make_progress_bar(percent)
                text = (
                    f"**Converting to {target_fmt.upper()}:**\n"
                    f"**Progress:** {bar}\n"
                    f"**Speed:** `{speed}` | **ETA:** `{eta_str}`\n"
                    f"**Engine:** `{current_hardware.description}`"
                )
                await updater.update(text)

            # Run FFmpeg transcode
            t_transcode_start = time.time()
            await run_ffmpeg_with_progress(
                cmd=cmd,
                total_duration_seconds=meta.duration_seconds,
                progress_callback=transcode_progress,
                progress_interval=0.2
            )
            transcode_time = round(time.time() - t_transcode_start, 1)
            term_log("PROCESS", f"Transcode finished ({target_fmt.upper()}) in {transcode_time}s", task_id=task_id, live=False)

            out_size = os.path.getsize(out_path) if out_path.exists() else 0

            # Register in file cache
            await register_file_cache(
                task_id=task_id,
                user_id=user_id,
                input_path=str(task_ctx.input_path),
                output_path=str(out_path)
            )

            # Upload with progress
            upload_start = time.time()
            last_up_log = 0.0
            async def upload_progress(current_bytes: int, total_bytes: int):
                nonlocal last_up_log
                now = time.time()
                percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
                if now - last_up_log >= 0.15:
                    last_up_log = now
                    term_log("UPLOAD", f"Uploading converted {target_fmt.upper()}", task_id=task_id, progress=percent, live=True)

                text = format_progress_status(
                    action_label=f"**Uploading {target_fmt.upper()} to Telegram:**",
                    current_bytes=current_bytes,
                    total_bytes=total_bytes,
                    start_time=upload_start
                )
                await updater.update(text)

            # 1. Probe output media for Before & After comparison and rich caption
            meta_after = await probe_media(out_path)
            v_codec = (meta_after.video_codec or target_fmt).upper()
            a_codec = (meta_after.audio_codec or "none").upper()
            fps_val = round(meta_after.fps) if meta_after.fps else 0
            fps_str = f" @ {fps_val}fps" if fps_val > 0 else ""

            # 2. Send file or audio with structured caption
            if is_audio:
                audio_caption = (
                    f"**File:** `{out_name}`\n"
                    f"• **Format:** {target_fmt.upper()} Audio ({a_codec})\n"
                    f"• **Size:** {format_bytes(out_size)}"
                )
                sent_msg = await client.send_file(
                    entity=user_id,
                    file=str(out_path),
                    caption=audio_caption,
                    voice_note=False,
                    progress_callback=upload_progress
                )
            else:
                video_caption = (
                    f"**File:** `{out_name}`\n"
                    f"• **Quality:** {meta_after.resolution_label}{fps_str}\n"
                    f"• **Size:** {format_bytes(out_size)}\n"
                    f"• **Format:** {target_fmt.upper()} ({v_codec} / {a_codec})"
                )
                sent_msg = await client.send_file(
                    entity=user_id,
                    file=str(out_path),
                    caption=video_caption,
                    supports_streaming=True,
                    progress_callback=upload_progress
                )
            upload_time = round(time.time() - upload_start, 1)
            term_log("UPLOAD", f"Upload finished in {upload_time}s", task_id=task_id, live=False)

            total_pipeline_time = round(task_ctx.download_time + task_ctx.probe_time + transcode_time + upload_time, 1)
            total_overall_time = round(time.time() - task_ctx.t_received, 1) if task_ctx.t_received > 0 else total_pipeline_time

            analysis_md = format_before_after_analysis(before=meta, after=meta_after)

            # Format full time breakdown
            time_breakdown_str = format_time_breakdown(
                download_time=task_ctx.download_time,
                probe_time=task_ctx.probe_time,
                transcode_time=transcode_time,
                upload_time=upload_time,
                total_pipeline_time=total_pipeline_time,
                total_overall_time=total_overall_time
            )

            # 3. Assemble full report text
            disk = settings.get_disk_usage()
            orig_name = Path(task_ctx.input_path).name
            orig_size_str = format_bytes(meta.file_size_bytes)

            report_parts = [
                f"**{conv_label} Complete!**\n",
                analysis_md,
                "\n" + time_breakdown_str,
                f"\n• **Engine:** `{current_hardware.description}`"
            ]
            if target_fmt == "audio_fix":
                report_parts.append("\n• **Fixes Applied:**\n  - Copied video stream untouched without re-encoding (zero quality loss)\n  - Transcoded audio to universal stereo AAC 192k for smartphone & TV compatibility\n  - Optimized MP4 faststart for instant playback streaming")

            report_parts.append(
                "\n**Step 1 of 2: Original File Cleanup**\n"
                f"• **Original Source File:** `{orig_name}` ({orig_size_str})\n"
                f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
                "Would you like to delete the original source file from the server now?"
            )
            report_text = "\n".join(report_parts)

            # 4. Send reply message directly replying to the delivered media
            reply_msg = await client.send_message(
                entity=user_id,
                message=report_text,
                reply_to=sent_msg.id,
                buttons=build_cleanup_original_menu(task_id)
            )

            # 5. Delete previous progress status message
            try:
                await status_msg.delete()
            except Exception:
                pass

            task_ctx.status_message = reply_msg

            # Record completion in DB
            await record_task_complete(task_id, out_size, total_pipeline_time)
            term_log(
                "DONE",
                f"Delivered {target_fmt.upper()} to user! Size: {format_bytes(out_size)} | Total: {total_pipeline_time}s (Overall: {total_overall_time}s) [Down: {task_ctx.download_time}s, Probe: {task_ctx.probe_time}s, Transcode: {transcode_time}s, Up: {upload_time}s]",
                task_id=task_id
            )

        except Exception as e:
            logger.error(f"Conversion error for task {task_id}: {e}", exc_info=True)
            elapsed_time = round(time.time() - start_exec, 1)
            await record_task_failed(task_id, elapsed_time)
            await status_msg.edit(f"[ERROR] **Conversion Failed:**\n`{str(e)}`")
        finally:
            task_ctx.is_processing = False
