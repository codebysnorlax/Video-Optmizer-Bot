"""
Interactive Button Callback Handler & Transcoding Dispatcher
"""

import os
import time
import asyncio
import logging
from pathlib import Path
from telethon import events
from config.settings import settings
from core.probe import format_bytes, format_duration
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
    mark_file_cache_deleted
)
from bot.keyboards import (
    build_main_menu,
    build_optimize_menu,
    build_convert_menu,
    build_retention_menu
)
from bot.progress import ThrottledMessageUpdater, format_progress_status, make_progress_bar
from bot.task_manager import task_manager

logger = logging.getLogger(__name__)


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
        task_ctx = task_manager.get_task(task_id)
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
        task_ctx = task_manager.get_task(task_id)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        menu = build_optimize_menu(task_id)
        text = (
            "*Select Optimization Mode:*\n\n"
            "*1. Low Device:* Max compatibility (H.264 Baseline/Main), lower bitrate for budget/older devices.\n"
            "*2. Moderate:* Balanced mid-range profile, crisp 1080p/720p clarity.\n"
            "*3. Auto Recommended:* Smart analyzer automatically fixes high bitrate bloat (e.g. 500MB camera file) with zero perceptual quality loss."
        )
        await event.edit(text, buttons=menu)
        return

    # 4. Convert Menu Sub-options
    if action_type == "conv_menu":
        task_id = parts[1]
        task_ctx = task_manager.get_task(task_id)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return
        menu = build_convert_menu(task_id)
        text = (
            "*Select Target Conversion Format:*\n\n"
            "• Video: `MP4`, `MKV`, `WEBM`, `AVI`, `MOV`, `MPG`\n"
            "• Audio Extraction: `MP3`, `AAC`"
        )
        await event.edit(text, buttons=menu)
        return

    # 5. Server File Retention Callbacks
    if action_type == "del_srv":
        task_id = parts[1]
        cache = await get_file_cache(task_id)
        deleted_count = 0
        if cache:
            for p_str in [cache.get("input_path"), cache.get("output_path")]:
                if p_str and os.path.exists(p_str):
                    try:
                        os.remove(p_str)
                        deleted_count += 1
                    except Exception as e:
                        logger.error(f"Failed to delete {p_str}: {e}")
            await mark_file_cache_deleted(task_id)
        
        task_manager.remove_task(task_id)
        disk_info = settings.get_disk_usage()
        term_log("STORAGE", f"Deleted local temporary files for task {task_id}", task_id=task_id, extra=f"{disk_info['free_gb']} GB free")
        await event.edit(
            "*Server storage cleaned!*\n\n"
            "Deleted temporary source & output files from server.\n"
            f"*Updated Server Free Space:* `{disk_info['free_gb']} GB free / {disk_info['total_gb']} GB total`"
        )
        await event.answer("Files deleted from server!", alert=False)
        return

    if action_type == "keep_srv":
        task_id = parts[1]
        task_manager.remove_task(task_id)
        disk_info = settings.get_disk_usage()
        term_log("STORAGE", f"Retained files in server cache for task {task_id}", task_id=task_id)
        await event.edit(
            "*File retained in server cache.*\n\n"
            f"*Current Server Free Space:* `{disk_info['free_gb']} GB free / {disk_info['total_gb']} GB total`\n"
            "You can manage server storage anytime."
        )
        await event.answer("Saved in server cache.", alert=False)
        return

    # 6. Execute Optimization Job
    if action_type == "opt_run":
        sub_mode = parts[1]
        task_id = parts[2]
        task_ctx = task_manager.get_task(task_id)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return

        term_log("PROCESS", f"User selected Optimization ({sub_mode.upper()})", task_id=task_id)
        await event.answer("Starting optimization...")
        asyncio.create_task(process_optimization(task_ctx, sub_mode, client))
        return

    # 7. Execute Conversion Job
    if action_type == "conv_run":
        target_fmt = parts[1]
        task_id = parts[2]
        task_ctx = task_manager.get_task(task_id)
        if not task_ctx:
            await event.answer("Session expired or file was deleted.", alert=True)
            return

        term_log("PROCESS", f"User selected Conversion ({target_fmt.upper()})", task_id=task_id)
        await event.answer(f"Converting to {target_fmt.upper()}...")
        asyncio.create_task(process_conversion(task_ctx, target_fmt, client))
        return


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
    await updater.update(f"*Queued for {mode_label}...*\nWaiting for processing slot...", force=True)

    start_exec = time.time()

    async with task_manager.semaphore:
        try:
            # Build optimization arguments
            explanation = ""
            if mode == "low":
                cmd = VideoOptimizer.build_low_device_args(meta, out_path, current_hardware)
            elif mode == "mod":
                cmd = VideoOptimizer.build_moderate_args(meta, out_path, current_hardware)
            else:
                cmd, explanation = VideoOptimizer.build_auto_recommended_args(meta, out_path, current_hardware)

            last_proc_log = 0.0
            async def transcode_progress(percent: float, speed: str, eta: int, fps: float):
                nonlocal last_proc_log
                now = time.time()
                eta_str = f"{eta}s" if eta is not None else "--"
                if now - last_proc_log >= 2.0:
                    last_proc_log = now
                    term_log("PROCESS", f"Transcoding ({mode_label})", task_id=task_id, progress=percent, extra=f"speed {speed}, fps {fps:.1f}, eta {eta_str}")

                bar = make_progress_bar(percent)
                text = (
                    f"*{mode_label} in Progress:*\n"
                    f"Progress: {bar}\n"
                    f"Speed: `{speed}` | FPS: `{fps:.1f}` | ETA: `{eta_str}`\n"
                    f"Engine: `{current_hardware.description}`"
                )
                await updater.update(text)

            term_log("PROCESS", f"Starting FFmpeg transcode pipeline ({mode_label})", task_id=task_id)
            # Run FFmpeg transcode
            await run_ffmpeg_with_progress(
                cmd=cmd,
                total_duration_seconds=meta.duration_seconds,
                progress_callback=transcode_progress,
                progress_interval=settings.PROGRESS_UPDATE_INTERVAL
            )

            # Transcode completed
            elapsed_time = round(time.time() - start_exec, 1)
            out_size = os.path.getsize(out_path) if out_path.exists() else 0
            
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
                if now - last_up_log >= 2.0:
                    last_up_log = now
                    term_log("UPLOAD", f"Uploading optimized video", task_id=task_id, progress=percent)

                text = format_progress_status(
                    action_label="*Uploading Optimized Video to Telegram:*",
                    current_bytes=current_bytes,
                    total_bytes=total_bytes,
                    start_time=upload_start
                )
                await updater.update(text)

            # Calculate savings
            saved_bytes = max(0, meta.file_size_bytes - out_size)
            ratio = (saved_bytes / meta.file_size_bytes * 100) if meta.file_size_bytes > 0 else 0

            caption = (
                f"*{mode_label} Complete!*\n\n"
                f"*File:* `{meta.file_name}`\n"
                f"*Duration:* `{format_duration(meta.duration_seconds)}`\n"
                f"*Original:* `{format_bytes(meta.file_size_bytes)}` -> *Optimized:* `{format_bytes(out_size)}`\n"
                f"*Reduction:* `{ratio:.1f}% Saved` ({format_bytes(saved_bytes)})\n"
                f"*Processing Time:* `{elapsed_time}s`\n"
                f"*Engine:* `{current_hardware.description}`"
            )
            if explanation:
                caption += f"\n\n*Fixes Applied:*\n{explanation}"

            # Send video file to user
            await client.send_file(
                entity=user_id,
                file=str(out_path),
                caption=caption,
                supports_streaming=True,
                progress_callback=upload_progress
            )

            # Record completion in DB
            await record_task_complete(task_id, out_size, elapsed_time)
            term_log("DONE", f"Delivered to user! Saved {format_bytes(saved_bytes)} ({ratio:.1f}%) in {elapsed_time}s", task_id=task_id)

            # Prompt user for disk cleanup
            disk = settings.get_disk_usage()
            cleanup_prompt = (
                "*Video Delivered Successfully!*\n\n"
                "*Server Storage Status:*\n"
                f"• Free Disk Space: `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
                "Would you like to delete the server copy now to free up space on your host?"
            )
            await status_msg.edit(cleanup_prompt, buttons=build_retention_menu(task_id))

        except Exception as e:
            logger.error(f"Optimization error for task {task_id}: {e}", exc_info=True)
            elapsed_time = round(time.time() - start_exec, 1)
            await record_task_failed(task_id, elapsed_time)
            await status_msg.edit(f"[ERROR] *Optimization Failed:*\n`{str(e)}`")


async def process_conversion(task_ctx: TaskContext, target_fmt: str, client) -> None:
    """Executes the chosen format conversion pipeline."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id
    meta = task_ctx.meta
    status_msg = task_ctx.status_message
    
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
    await updater.update(f"*Queued for conversion to {target_fmt.upper()}...*\nWaiting for processing slot...", force=True)

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
                if now - last_conv_log >= 2.0:
                    last_conv_log = now
                    term_log("PROCESS", f"Converting ({target_fmt.upper()})", task_id=task_id, progress=percent, extra=f"speed {speed}, eta {eta_str}")

                bar = make_progress_bar(percent)
                text = (
                    f"*Converting to {target_fmt.upper()}:*\n"
                    f"Progress: {bar}\n"
                    f"Speed: `{speed}` | ETA: `{eta_str}`\n"
                    f"Engine: `{current_hardware.description}`"
                )
                await updater.update(text)

            # Run FFmpeg transcode
            await run_ffmpeg_with_progress(
                cmd=cmd,
                total_duration_seconds=meta.duration_seconds,
                progress_callback=transcode_progress,
                progress_interval=settings.PROGRESS_UPDATE_INTERVAL
            )

            elapsed_time = round(time.time() - start_exec, 1)
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
                if now - last_up_log >= 2.0:
                    last_up_log = now
                    term_log("UPLOAD", f"Uploading converted {target_fmt.upper()}", task_id=task_id, progress=percent)

                text = format_progress_status(
                    action_label=f"*Uploading {target_fmt.upper()} to Telegram:*",
                    current_bytes=current_bytes,
                    total_bytes=total_bytes,
                    start_time=upload_start
                )
                await updater.update(text)

            caption = (
                f"*Conversion to {target_fmt.upper()} Complete!*\n\n"
                f"*File:* `{meta.file_name}`\n"
                f"*Output Size:* `{format_bytes(out_size)}`\n"
                f"*Processing Time:* `{elapsed_time}s`\n"
                f"*Engine:* `{current_hardware.description}`"
            )

            # Send file or audio
            if is_audio:
                await client.send_file(
                    entity=user_id,
                    file=str(out_path),
                    caption=caption,
                    voice_note=False,
                    progress_callback=upload_progress
                )
            else:
                await client.send_file(
                    entity=user_id,
                    file=str(out_path),
                    caption=caption,
                    supports_streaming=True,
                    progress_callback=upload_progress
                )

            # Record completion in DB
            await record_task_complete(task_id, out_size, elapsed_time)
            term_log("DONE", f"Delivered {target_fmt.upper()} to user! Size: {format_bytes(out_size)} in {elapsed_time}s", task_id=task_id)

            # Prompt user for disk cleanup
            disk = settings.get_disk_usage()
            cleanup_prompt = (
                "*Delivered Successfully!*\n\n"
                "*Server Storage Status:*\n"
                f"• Free Disk Space: `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
                "Would you like to delete the server copy now to free up space on your host?"
            )
            await status_msg.edit(cleanup_prompt, buttons=build_retention_menu(task_id))

        except Exception as e:
            logger.error(f"Conversion error for task {task_id}: {e}", exc_info=True)
            elapsed_time = round(time.time() - start_exec, 1)
            await record_task_failed(task_id, elapsed_time)
            await status_msg.edit(f"[ERROR] *Conversion Failed:*\n`{str(e)}`")
