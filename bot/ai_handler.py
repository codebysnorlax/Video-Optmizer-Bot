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
from core.probe import format_bytes, format_duration, probe_media, format_before_after_analysis, clean_display_name
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
            preferred_model=active_model,
            assets=task_ctx.get_assets()
        )
    else:
        task_ctx.ai_agent.assets = task_ctx.get_assets()
        task_ctx.ai_agent.tool_executor.assets = task_ctx.get_assets()

    task_ctx.ai_session_active = True
    task_ctx.ai_workplace_dir = settings.get_ai_workplace(user_id, task_id)
    try:
        task_ctx.status_message = await event.get_message()
    except Exception:
        pass

    target_name = clean_display_name(task_ctx.meta.file_name)
    ai_card = (
        "**AI Video Assistant**\n\n"
        f"• **Session Target:** `{target_name}`\n"
        f"• **Current Specs:** `{task_ctx.meta.resolution_label}` | `{format_bytes(task_ctx.meta.file_size_bytes)}` | `{int(task_ctx.meta.duration_seconds)}s`\n"
        f"• **Active Model:** `{active_model}`\n"
        f"• **Hardware:** `{current_hardware.description}`\n\n"
        "**Tell me what you need:**\n"
        "Describe your problem or request in plain English. I will analyze your media and execute the job directly:\n"
        "• \"Why does this lag on my phone?\"\n"
        "• \"Decrease file size to 1080p or 360p without losing quality\"\n"
        "• \"Separate audio and video and give me both files\"\n"
        "• \"Extract audio as MP3\" or \"Mute this video\"\n"
        "• \"Change aspect ratio to 9:16 vertical or reverse video\"\n"
        "• \"Send an audio track or second video into this chat to combine or replace soundtracks\"\n\n"
        "Type your message directly in the chat below:"
    )

    await event.edit(ai_card, buttons=build_ai_session_menu(task_id))
    await event.answer("AI Engineer is ready. Reply with your prompt or problem.")


async def handle_ai_quick_prompt(event, preset: str, task_ctx, client) -> None:
    """Handles preset one-tap prompt buttons if triggered."""
    prompt_map = {
        "split": "Separate this video into two distinct deliverables: a muted video track and an extracted MP3 audio track, and deliver both.",
        "extract_audio": "Extract the audio track from this video as an MP3 audio file.",
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

    if task_ctx.is_processing:
        try:
            await event.reply(
                "**AI is currently busy processing a task.**\n"
                "Please wait for the current operation to finish before sending new instructions."
            )
        except Exception:
            pass
        return

    # Delete incoming user message for cleaner chat interface if possible
    try:
        await event.delete()
    except Exception:
        pass

    status_msg = task_ctx.status_message or await event.respond("**AI Engineer Thinking...**")
    await execute_ai_query(user_text, task_ctx, status_msg, client)


async def execute_ai_query(user_prompt: str, task_ctx, status_msg, client) -> None:
    """Executes an AI reasoning turn with live progress reporting and directs output to transcode or diagnosis."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id

    task_ctx.is_processing = True
    task_ctx.is_cancelled = False
    start_time = time.time()

    # Display initial thinking status
    try:
        await status_msg.edit(
            f"**AI Engineer is analyzing your request...**\n\n_\"{user_prompt[:80]}...\"_",
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
            preferred_model=active_model,
            assets=task_ctx.get_assets()
        )
    else:
        task_ctx.ai_agent.assets = task_ctx.get_assets()
        task_ctx.ai_agent.tool_executor.assets = task_ctx.get_assets()

    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)

    async def transcode_progress(percent: float, speed: str, eta: int, fps: float, step_label: Optional[str] = None):
        if task_ctx.is_cancelled:
            if task_ctx.current_process:
                try:
                    task_ctx.current_process.kill()
                except Exception:
                    pass
            raise asyncio.CancelledError()
        bar = make_progress_bar(percent)
        step_line = f"• **Operation:** `{step_label}`\n" if step_label else ""
        text = (
            f"**AI Transcoding in Progress...**\n\n"
            f"{step_line}"
            f"• **Progress:** {bar} `{percent:.1f}%`\n"
            f"• **Speed:** `{speed}` | **FPS:** `{fps:.1f}`\n"
            f"• **ETA:** `{eta}s`\n"
            f"• **Engine:** `{current_hardware.description}`\n"
            f"• **Sandbox:** `storage/Ai_Workplace/{user_id}_{task_id}/`"
        )
        is_initial = (percent == 0.0 or speed in ("init", "starting"))
        await updater.update(text, force=is_initial, buttons=build_task_progress_menu(task_id))

    try:
        result = await task_ctx.ai_agent.process_message(
            user_prompt,
            progress_callback=transcode_progress,
            on_process_started=lambda p: setattr(task_ctx, "current_process", p)
        )
    except Exception as e:
        logger.error(f"Error during AI reasoning turn: {e}", exc_info=True)
        task_ctx.is_processing = False
        await status_msg.edit(
            f"**AI Processing Error:**\n\n{str(e)}\n\nPlease try again or rephrase your request.",
            buttons=build_ai_session_menu(task_id)
        )
        return

    # 1. Error Response
    if result.get("status") == "error":
        err_text = result.get("error", "Unknown error")
        task_ctx.is_processing = False
        await status_msg.edit(
            f"**AI Operation Failed**\n\n• **Reason:** `{err_text}`\n\nYou can rephrase your request or try again below:",
            buttons=build_ai_session_menu(task_id)
        )
        return

    # 2. Diagnostic Result
    if result.get("type") == "diagnosis":
        task_ctx.is_processing = False
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
        raw_paths = result.get("output_paths") or [result.get("output_path")]
        out_paths = [Path(p) for p in raw_paths if p]
        if out_paths:
            task_ctx.output_path = out_paths[0]
        task_ctx.processing_elapsed_time = max(0.1, time.time() - start_time)
        asyncio.create_task(deliver_ai_results(
            task_ctx,
            result.get("plan_title", "AI Result"),
            out_paths,
            status_msg,
            client,
            deliverables=result.get("deliverables")
        ))
        return

    # 4. Pure Text / Conversational Response
    task_ctx.is_processing = False
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
        executor = task_ctx.ai_agent.tool_executor if task_ctx.ai_agent else SafeToolExecutor(user_id, task_id, task_ctx.input_path, meta, assets=task_ctx.get_assets())
        res = await executor.run_execute_ffmpeg(
            plan_title=plan.get("plan_title", "AI Processing"),
            output_filename=plan.get("output_filename", "ai_output.mp4"),
            operations=plan.get("operations"),
            inputs=plan.get("inputs"),
            extract_audio_only=plan.get("extract_audio_only", False),
            mute_video_only=plan.get("mute_video_only", False),
            vf_filter=plan.get("vf_filter"),
            af_filter=plan.get("af_filter"),
            video_codec=plan.get("video_codec", "libx264"),
            crf=plan.get("crf", 22),
            audio_codec=plan.get("audio_codec", "aac"),
            stream_mapping=plan.get("stream_mapping"),
            extra_args=plan.get("extra_args"),
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

        raw_paths = res.get("output_paths") or [res.get("output_path")]
        out_paths = [Path(p) for p in raw_paths if p]
        if out_paths:
            task_ctx.output_path = out_paths[0]
        task_ctx.processing_elapsed_time = max(0.1, time.time() - start_time)
        await deliver_ai_results(
            task_ctx,
            plan.get("plan_title", "AI Optimization"),
            out_paths,
            status_msg,
            client,
            deliverables=res.get("deliverables")
        )


async def deliver_ai_results(
    task_ctx,
    plan_title: str,
    out_paths: List[Path],
    status_msg,
    client,
    deliverables: Optional[List[Dict[str, Any]]] = None
) -> None:
    """Uploads multiple completed AI deliverables sequentially with progress reporting and cleanup prompt."""
    task_id = task_ctx.task_id
    user_id = task_ctx.user_id
    meta = task_ctx.meta

    valid_paths = [Path(p) for p in out_paths if Path(p).exists()]
    total_deliverables = len(valid_paths)

    if total_deliverables == 0:
        await status_msg.edit(
            "**AI Notice:** No output deliverables were found to upload.",
            buttons=build_ai_session_menu(task_id)
        )
        task_ctx.is_processing = False
        return

    # Live upload progress
    task_ctx.current_stage = "uploading"
    task_ctx.active_stage_label = f"Uploading AI Results ({total_deliverables} files)"
    updater = ThrottledMessageUpdater(status_msg, interval=settings.PROGRESS_UPDATE_INTERVAL)
    total_output_size = 0

    for idx, out_path in enumerate(valid_paths, start=1):
        if task_ctx.is_cancelled:
            task_ctx.is_processing = False
            return

        # Register in file cache
        await register_file_cache(
            task_id=task_id,
            user_id=user_id,
            input_path=str(task_ctx.input_path),
            output_path=str(out_path)
        )

        upload_start = time.time()
        disp_name = clean_display_name(out_path.name)
        file_size_str = format_bytes(out_path.stat().st_size)

        # Notify user immediately that transcoding is complete and upload is beginning
        try:
            await status_msg.edit(
                f"**Transcoding Complete! Uploading Deliverables ({idx}/{total_deliverables})**\n\n"
                f"• **Plan:** `{plan_title}`\n"
                f"• **File:** `{disp_name}`\n"
                f"• **Size:** `{file_size_str}`\n"
                f"• **Status:** Uploading to Telegram...",
                buttons=build_task_progress_menu(task_id)
            )
        except Exception:
            pass

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
                f"**Uploading Deliverables ({idx}/{total_deliverables})**\n\n"
                f"• **Plan:** `{plan_title}`\n"
                f"• **File:** `{disp_name}`\n"
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
            logger.error(f"AI output upload failed for {out_path.name}: {e}")
            await status_msg.edit(
                f"**Upload Failed on {disp_name}:** `{str(e)}`",
                buttons=build_ai_session_menu(task_id)
            )
            task_ctx.is_processing = False
            return

        out_meta = await probe_media(out_path)
        file_size = out_path.stat().st_size
        total_output_size += file_size

        if out_meta and out_meta.has_video:
            before_after = format_before_after_analysis(meta, out_meta)
            caption = (
                f"**AI Deliverable ({idx}/{total_deliverables})**\n"
                f"• **Plan Applied:** `{plan_title}`\n"
                f"• **Deliverable:** `{disp_name}`\n\n"
                f"{before_after}"
            )
            supports_streaming = True
        else:
            dur_str = format_duration(out_meta.duration_seconds) if out_meta else "Unknown"
            caption = (
                f"**AI Deliverable ({idx}/{total_deliverables})**\n"
                f"• **Plan Applied:** `{plan_title}`\n"
                f"• **Deliverable:** `{disp_name}`\n"
                f"• **Type:** Extracted Audio Track\n"
                f"• **Size:** `{format_bytes(file_size)}` | **Duration:** `{dur_str}`"
            )
            supports_streaming = False

        # Deliver file to user
        await client.send_file(
            entity=user_id,
            file=uploaded_doc,
            caption=caption,
            supports_streaming=supports_streaming
        )

    # Record complete in DB
    elapsed_time = getattr(task_ctx, "processing_elapsed_time", 1.0)
    await record_task_complete(task_id, output_size=total_output_size, elapsed_time=elapsed_time)

    # Schedule automatic deletion of sandbox files after 2 minutes (120 seconds)
    async def _delayed_workplace_cleanup(u_id: int, t_id: str, delay_sec: int = 120):
        await asyncio.sleep(delay_sec)
        try:
            settings.clean_ai_workplace(u_id, t_id)
            logger.info(f"Auto-cleaned AI workplace sandbox for user {u_id}, task {t_id} after {delay_sec}s")
        except Exception as cleanup_err:
            logger.warning(f"Error during delayed workplace cleanup: {cleanup_err}")

    asyncio.create_task(_delayed_workplace_cleanup(user_id, task_id, delay_sec=120))

    # Keep AI session active and display clean summary card
    file_plural = "Deliverable" if total_deliverables == 1 else "Deliverables"
    target_name = clean_display_name(task_ctx.meta.file_name)
    summary_card = (
        f"**{file_plural} Delivered Successfully ({total_deliverables} files)**\n\n"
        f"• **Plan Applied:** `{plan_title}`\n"
        f"• **Session Target:** `{target_name}`\n"
        f"• **Auto-Cleanup:** Sandbox files will be automatically deleted in 2 minutes.\n\n"
        "AI Video Engineer is still active. You can type another instruction, request further changes, or send new media assets."
    )
    await status_msg.edit(summary_card, buttons=build_ai_session_menu(task_id))
    task_ctx.is_processing = False
    task_ctx.ai_session_active = True


async def deliver_ai_output(task_ctx, plan_title: str, out_path: Path, status_msg, client) -> None:
    """Compatibility wrapper uploading a single AI output."""
    await deliver_ai_results(task_ctx, plan_title, [out_path], status_msg, client)
