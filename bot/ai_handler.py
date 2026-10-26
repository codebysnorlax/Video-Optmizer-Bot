"""
Telegram Bot AI Manual Mode Handler & Dispatcher
Manages interactive AI engineer chat sessions, quick prompts, and sandboxed execution without emojis.
"""

import os
import time
import asyncio
import logging
from pathlib import Path
from typing import Optional, Dict, Any
from telethon import events, Button
from config.settings import settings
from core.probe import format_bytes, format_duration, probe_media, format_before_after_analysis
from core.hardware import current_hardware
from core.terminal import term_log
from core.engine import run_ffmpeg_with_progress
from core.fast_transfer import fast_upload
from core.ai.agent import VideoAIAgent
from bot.keyboards import (
    build_main_menu,
    build_ai_session_menu,
    build_ai_fix_menu,
    build_cleanup_original_menu,
    build_task_progress_menu
)
from bot.progress import (
    ThrottledMessageUpdater,
    format_progress_status,
    make_progress_bar
)
from database.models import (
    record_task_start,
    record_task_complete,
    record_task_failed,
    register_file_cache,
    get_user_ai_model
)

logger = logging.getLogger(__name__)


async def handle_ai_session_start(event, task_ctx, client) -> None:
    """Initializes and displays the AI Manual greeting and interactive session."""
    if not settings.OPENROUTER_API_KEY:
        await event.answer(
            "OpenRouter API key is not configured in .env!\nPlease set OPENROUTER_API_KEY to activate AI Manual features.",
            alert=True
        )
        return

    task_id = task_ctx.task_id
    user_id = task_ctx.user_id

    # Fetch user's preferred model
    active_model = await get_user_ai_model(user_id)

    # Create agent if needed
    if not task_ctx.ai_agent or task_ctx.ai_agent.preferred_model != active_model:
        task_ctx.ai_agent = VideoAIAgent(
            user_id=user_id,
            task_id=task_id,
            input_path=task_ctx.input_path,
            meta=task_ctx.meta,
            preferred_model=active_model
        )

    task_ctx.ai_session_active = True
    task_ctx.ai_workplace_dir = settings.get_ai_workplace(user_id, task_id)
    try:
        task_ctx.status_message = await event.get_message()
    except Exception:
        pass

    ai_card = (
        "**AI Video Engineer Activated**\n\n"
        f"• **Target Video:** `{task_ctx.meta.file_name}`\n"
        f"• **Current Specs:** `{task_ctx.meta.resolution_label}` | `{format_bytes(task_ctx.meta.file_size_bytes)}` | `{int(task_ctx.meta.duration_seconds)}s`\n"
        f"• **Active Model:** `{active_model}`\n"
        f"• **Engine:** `{current_hardware.description}`\n"
        f"• **Sandbox:** `storage/Ai_Workplace/{user_id}_{task_id}/`\n\n"
        "• **Instructions:** Type your question or instruction directly in the chat below, for example:\n"
        " - *\"Why does this video lag on my phone?\"*\n"
        " - *\"Why is the file size so huge?\"*\n"
        " - *\"Downscale this from 4K to 1080p without losing quality\"*\n"
        " - *\"Change aspect ratio to 9:16 vertical\"*\n"
        " - *\"Reverse this video\"*\n\n"
        "Or select a quick action below:"
    )

    await event.edit(ai_card, buttons=build_ai_session_menu(task_id))
    await event.answer("AI Engineer is ready. Reply with your prompt or select an action.")


async def handle_ai_quick_prompt(event, preset: str, task_ctx, client) -> None:
    """Handles preset one-tap prompt buttons."""
    prompt_map = {
        "lag": "Why does this video lag on my phone, and how can I optimize it for smooth playback?",
        "1080": "Downscale this video from its current resolution to 1080p FHD preserving maximum visual quality and web streaming faststart.",
        "916": "Convert this video to 9:16 vertical aspect ratio with blurred background borders for Instagram Reels / TikTok.",
        "rev": "Reverse the playback of this video and its audio safely."
    }
    user_prompt = prompt_map.get(preset, "Analyze this video and recommend the best optimization.")
    status_msg = await event.get_message()
    await execute_ai_query(user_prompt, task_ctx, status_msg, client)


async def handle_ai_user_text(event, task_ctx, client) -> None:
    """Handles incoming natural language chat messages directed to an active AI session."""
    user_text = event.raw_text.strip()
    if not user_text:
        return

    # Delete incoming user message for cleaner chat interface if possible
    try:
        await event.delete()
    except Exception:
        pass

    status_msg = task_ctx.status_message or await event.respond("**AI Engineer Thinking...**")
    await execute_ai_query(user_text, task_ctx, status_msg, client)


async def execute_ai_query(user_prompt: str, task_ctx, status_msg, client) -> None:
    """Executes an AI reasoning turn and directs output to diagnosis or transcode."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id

    # Display thinking status
    try:
        await status_msg.edit(
            f"**AI Video Engineer is thinking...**\n\n_\"{user_prompt[:80]}...\"_",
            buttons=None
        )
    except Exception:
        pass

    # Ensure agent is initialized with user's model
    active_model = await get_user_ai_model(user_id)
    if not task_ctx.ai_agent or task_ctx.ai_agent.preferred_model != active_model:
        task_ctx.ai_agent = VideoAIAgent(
            user_id=user_id,
            task_id=task_id,
            input_path=task_ctx.input_path,
            meta=task_ctx.meta,
            preferred_model=active_model
        )

    # Run agent reasoning turn
    result = await task_ctx.ai_agent.process_message(user_prompt)

    # 1. Error Response
    if result.get("status") == "error":
        err_text = result.get("error", "Unknown error")
        await status_msg.edit(
            f"**AI Service Notice:**\n\n{err_text}\n\nYou can still use standard options below:",
            buttons=build_ai_session_menu(task_id)
        )
        return

    # 2. Diagnostic Result
    if result.get("type") == "diagnosis":
        explanation = result.get("explanation", "")
        recommendation = result.get("recommended_action", "")
        reduction = result.get("estimated_reduction_percent", 0)

        reduction_str = f" (~{reduction}% size savings)" if reduction > 0 else ""
        text = (
            "**AI Video Diagnostic Analysis**\n\n"
            f"• **Root Cause:**\n{explanation}\n\n"
            f"• **Recommended Fix:**\n{recommendation}{reduction_str}\n\n"
            "Would you like me to apply this fix for you?"
        )
        # Store for 1-tap apply
        task_ctx.ai_last_action_plan = {
            "plan_title": "AI Recommended Fix",
            "vf_filter": "scale=-2:1080" if (task_ctx.meta and (task_ctx.meta.width or 0) > 1920) else None,
            "video_codec": "libx264",
            "crf": 22,
            "audio_codec": "aac",
            "output_filename": f"ai_fixed_{task_ctx.input_path.stem}.mp4"
        }
        await status_msg.edit(text, buttons=build_ai_fix_menu(task_id, "recommended"))
        return

    # 3. Transcode Execution Result (Tool already executed FFmpeg)
    if result.get("type") == "transcode":
        out_path = Path(result["output_path"])
        task_ctx.output_path = out_path
        asyncio.create_task(deliver_ai_output(task_ctx, result["plan_title"], out_path, status_msg, client))
        return

    # 4. Pure Text / Conversational Response
    reply_text = result.get("text") or result.get("assistant_text") or "I have processed your request."
    formatted_reply = f"**AI Video Engineer:**\n\n{reply_text}"
    await status_msg.edit(formatted_reply, buttons=build_ai_session_menu(task_id))


async def handle_ai_apply_fix(event, task_ctx, client) -> None:
    """Applies the recommended fix generated during diagnosis."""
    plan = task_ctx.ai_last_action_plan or {
        "plan_title": "AI Recommended Fix",
        "vf_filter": "scale=-2:1080" if (task_ctx.meta and (task_ctx.meta.width or 0) > 1920) else None,
        "video_codec": "libx264",
        "crf": 22,
        "audio_codec": "aac",
        "output_filename": f"ai_fixed_{task_ctx.input_path.stem}.mp4"
    }
    status_msg = await event.get_message()
    asyncio.create_task(run_and_deliver_ai_plan(task_ctx, plan, status_msg, client))


async def run_and_deliver_ai_plan(task_ctx, plan: Dict[str, Any], status_msg, client) -> None:
    """Executes an FFmpeg plan with live progress reporting inside the Ai_Workplace sandbox."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id
    meta = task_ctx.meta

    if task_ctx.is_processing:
        await status_msg.respond("Another video process is already in progress. Please wait...")
        return

    task_ctx.is_processing = True
    task_ctx.current_stage = "queued"
    task_ctx.active_stage_label = f"AI: {plan.get('plan_title', 'Processing')}"
    task_ctx.is_cancelled = False
    task_ctx.is_confirming_stop = False

    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    await status_msg.edit(
        f"**Queued for {plan.get('plan_title', 'AI Optimization')}...**\nWaiting for processing slot...",
        buttons=build_task_progress_menu(task_id)
    )

    # Concurrency serialization
    from bot.task_manager import task_manager
    async with task_manager.semaphore:
        task_ctx.current_stage = "encoding"
        task_ctx.active_stage_label = f"AI Transcoding: {plan.get('plan_title', 'Video')}"
        start_time = time.time()

        # Progress callback
        async def transcode_progress(percent: float, speed: str, eta: int, fps: float):
            if task_ctx.is_cancelled:
                if task_ctx.current_process:
                    try:
                        task_ctx.current_process.kill()
                    except Exception:
                        pass
                raise asyncio.CancelledError()
            bar = make_progress_bar(percent)
            text = (
                f"**AI Transcoding: {plan.get('plan_title')}**\n\n"
                f"• **Progress:** {bar} `{percent:.1f}%`\n"
                f"• **Speed:** `{speed}` | **FPS:** `{fps:.1f}`\n"
                f"• **ETA:** `{eta}s`\n"
                f"• **Engine:** `{current_hardware.description}`\n"
                f"• **Sandbox:** `Ai_Workplace/{user_id}_{task_id}/`"
            )
            await updater.update(text, buttons=build_task_progress_menu(task_id))

        # Run safe tool executor
        executor = task_ctx.ai_agent.tool_executor if task_ctx.ai_agent else SafeToolExecutor(user_id, task_id, task_ctx.input_path, meta)
        res = await executor.run_execute_ffmpeg(
            plan_title=plan.get("plan_title", "AI Processing"),
            output_filename=plan.get("output_filename", "ai_output.mp4"),
            vf_filter=plan.get("vf_filter"),
            af_filter=plan.get("af_filter"),
            video_codec=plan.get("video_codec", "libx264"),
            crf=plan.get("crf", 22),
            audio_codec=plan.get("audio_codec", "aac"),
            progress_callback=transcode_progress,
            on_process_started=lambda p: setattr(task_ctx, "current_process", p)
        )

        task_ctx.current_process = None

        if res.get("status") == "error":
            task_ctx.is_processing = False
            await status_msg.edit(
                f"**AI Transcoding Failed:**\n\n{res.get('error', 'Execution error')}",
                buttons=build_ai_session_menu(task_id)
            )
            return

        out_path = Path(res["output_path"])
        task_ctx.output_path = out_path
        await deliver_ai_output(task_ctx, plan.get("plan_title", "AI Optimization"), out_path, status_msg, client)


async def deliver_ai_output(task_ctx, plan_title: str, out_path: Path, status_msg, client) -> None:
    """Uploads the completed AI video output with progress reporting and cleanup prompt."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id
    meta = task_ctx.meta

    # Register in file cache
    await register_file_cache(
        task_id=task_id,
        user_id=user_id,
        input_path=str(task_ctx.input_path),
        output_path=str(out_path)
    )

    # Live upload progress
    task_ctx.current_stage = "uploading"
    task_ctx.active_stage_label = "Uploading AI Output"
    upload_start = time.time()
    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)

    async def upload_progress(current_bytes: int, total_bytes: int):
        if task_ctx.is_cancelled:
            raise asyncio.CancelledError()
        percent = (current_bytes / total_bytes * 100) if total_bytes > 0 else 0
        bar = make_progress_bar(percent)
        cur_mb = current_bytes / (1024 * 1024)
        tot_mb = total_bytes / (1024 * 1024)
        elapsed = max(0.1, time.time() - upload_start)
        speed_mb = (current_bytes / elapsed) / (1024 * 1024)
        eta = int((total_bytes - current_bytes) / max(1, current_bytes / elapsed))
        text = (
            f"**Uploading AI Result: {plan_title}**\n\n"
            f"• **Progress:** {bar} `{percent:.1f}%`\n"
            f"• **Uploaded:** `{cur_mb:.1f} MB / {tot_mb:.1f} MB`\n"
            f"• **Speed:** `{speed_mb:.1f} MB/s` | **ETA:** `{eta}s`"
        )
        await updater.update(text, buttons=build_task_progress_menu(task_id))

    try:
        uploaded_doc = await fast_upload(
            client=client,
            file_path=out_path,
            progress_callback=upload_progress
        )
    except Exception as e:
        logger.error(f"AI output upload failed: {e}")
        await status_msg.edit(f"**Upload Failed:** {str(e)}", buttons=build_ai_session_menu(task_id))
        task_ctx.is_processing = False
        return

    # Probe output metadata for before/after comparison card
    out_meta = await probe_media(out_path)
    before_after = format_before_after_analysis(meta, out_meta)

    caption = (
        f"**AI Video Optimization Complete**\n"
        f"• **Plan Applied:** `{plan_title}`\n\n"
        f"{before_after}"
    )

    # Deliver video file
    await client.send_file(
        entity=user_id,
        file=uploaded_doc,
        caption=caption,
        supports_streaming=True
    )

    # Record complete in DB
    saved_bytes = max(0, meta.file_size_bytes - out_path.stat().st_size)
    await record_task_complete(task_id, saved_bytes)

    # Prompt user for disk cleanup
    disk = settings.get_disk_usage()
    cleanup_prompt = (
        "**Video Delivered Successfully**\n\n"
        f"• **Server Storage:** `{disk['free_gb']} GB free` ({disk['percent_used']}% used)\n"
        f"• **Temporary Sandbox:** `storage/Ai_Workplace/{user_id}_{task_id}/`\n\n"
        "Choose file retention options below:"
    )
    await status_msg.edit(cleanup_prompt, buttons=build_cleanup_original_menu(task_id))
    task_ctx.is_processing = False
    task_ctx.ai_session_active = False
