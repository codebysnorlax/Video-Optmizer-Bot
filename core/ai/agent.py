"""
AI Video Engineer Agent (ReAct Loop)
Manages multi-turn conversation, dynamically injects skills, executes sandboxed tools,
and provides technical diagnosis or video optimization.
"""

import asyncio
import json
import logging
import re
import time
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable, Tuple
from core.probe import MediaMetadata, format_bytes, format_duration
from core.hardware import current_hardware
from core.engine import ProgressCallback
from core.ai.client import ai_client, OpenRouterClient
from core.ai.skills import skill_registry
from core.ai.tools import AI_TOOLS_SCHEMA, SafeToolExecutor
from core.ai.debug_logger import ai_debug_logger
from config.settings import settings

logger = logging.getLogger(__name__)


def sanitize_message_history(history: List[Dict[str, Any]], complete_trailing: bool = True) -> List[Dict[str, Any]]:
    """
    Sanitizes message history for OpenAI / OpenRouter Chat Completion API compliance.
    Ensures that every 'tool' message sequence is properly opened by an 'assistant' message
    with 'tool_calls' and properly closed by an 'assistant' response before any subsequent 'user' message.
    If complete_trailing is True, also appends a synthetic assistant message if the history ends with a tool message.
    When complete_trailing is False (default for follow-up turns in ReAct loop), trailing tool messages are kept
    so the LLM receives the tool response and generates the final assistant response.
    """
    if not history:
        return []

    sanitized: List[Dict[str, Any]] = []
    in_tool_sequence = False

    for msg in history:
        role = msg.get("role")

        if role == "tool":
            sanitized.append(msg)
            in_tool_sequence = True
        elif role == "user":
            if in_tool_sequence:
                # Close the tool sequence with a synthetic assistant message before user message
                sanitized.append({
                    "role": "assistant",
                    "content": "Operation completed."
                })
                in_tool_sequence = False
            sanitized.append(msg)
        elif role == "assistant":
            in_tool_sequence = False
            sanitized.append(msg)
        else:
            in_tool_sequence = False
            sanitized.append(msg)

    if complete_trailing and in_tool_sequence:
        sanitized.append({
            "role": "assistant",
            "content": "Operation completed."
        })

    # Post-sanitization check: if the first message has role == "tool", prepend synthetic assistant
    if sanitized and sanitized[0].get("role") == "tool":
        sanitized.insert(0, {
            "role": "assistant",
            "content": "I performed the previous operation."
        })

    return sanitized


def compress_old_history(history: List[Dict[str, Any]], keep_recent: int = 20) -> Tuple[str, List[Dict[str, Any]]]:
    """
    Splits history into old (everything before the last keep_recent messages) and recent (last keep_recent messages).
    Builds a one-paragraph summary of old turns listing all asset keys, operations performed, and user goals.
    Returns (summary_text, recent_messages).
    """
    if not history:
        return "", []

    if len(history) <= keep_recent:
        return "", list(history)

    old = history[:-keep_recent]
    recent = list(history[-keep_recent:])

    user_goals: List[str] = []
    operations: List[str] = []
    asset_keys: List[str] = []
    seen_assets = set()

    for msg in old:
        role = msg.get("role")
        content = msg.get("content")

        # Extract user goals
        if role == "user" and isinstance(content, str):
            goal = content.strip()
            if goal and goal not in user_goals:
                if len(goal) > 120:
                    goal = goal[:117] + "..."
                user_goals.append(goal)

        # Extract operation names from tool messages
        if role == "tool":
            op_name = None
            if isinstance(content, str):
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        op_name = (
                            data.get("plan_title")
                            or data.get("operation")
                            or data.get("name")
                            or data.get("recommended_action")
                        )
                except Exception:
                    pass
                if not op_name:
                    op_name = msg.get("name") or content[:60].strip()
            elif isinstance(content, dict):
                op_name = (
                    content.get("plan_title")
                    or content.get("operation")
                    or content.get("name")
                    or content.get("recommended_action")
                )
            if not op_name and msg.get("name"):
                op_name = msg.get("name")

            if op_name:
                op_str = str(op_name).strip()
                if op_str and op_str not in operations:
                    operations.append(op_str)

        # Extract asset keys like [v1], [a1], etc. from any message
        msg_str = json.dumps(msg, default=str) if isinstance(msg, dict) else str(msg)
        for match in re.findall(r'\[([a-zA-Z0-9_-]+)\]', msg_str):
            key = f"[{match}]"
            if key not in seen_assets:
                seen_assets.add(key)
                asset_keys.append(key)
        for match in re.findall(r'\b([vas]\d+)\b', msg_str, re.IGNORECASE):
            key = f"[{match.lower()}]"
            if key not in seen_assets:
                seen_assets.add(key)
                asset_keys.append(key)

    summary_parts = []
    if user_goals:
        summary_parts.append(f"User goals: {'; '.join(user_goals)}.")
    if operations:
        summary_parts.append(f"Operations performed: {', '.join(operations)}.")
    if asset_keys:
        summary_parts.append(f"Assets referenced: {', '.join(asset_keys)}.")

    summary_text = " ".join(summary_parts)
    if not summary_text and old:
        summary_text = "Previous conversation turns took place."

    return summary_text, recent


def guard_truncation_boundary(
    full_history: List[Dict[str, Any]],
    recent: Optional[List[Dict[str, Any]]] = None
) -> List[Dict[str, Any]]:
    """
    Ensures that a truncated history does not begin with an orphaned 'tool' message.
    If recent begins with a 'tool' message, scans backward in full_history from the cut point
    to include the preceding 'assistant' message (which contains 'tool_calls').
    Supports both signatures:
      guard_truncation_boundary(full_history, recent)
      guard_truncation_boundary(messages)
    """
    if recent is None:
        messages = list(full_history)
        if messages and messages[0].get("role") == "tool":
            return [{
                "role": "assistant",
                "content": "I performed the previous operation."
            }] + messages
        return messages

    if not recent:
        return []

    if recent[0].get("role") != "tool":
        return list(recent)

    cut_point = len(full_history) - len(recent)
    if cut_point < 0 or cut_point >= len(full_history) or full_history[cut_point] != recent[0]:
        found = False
        for k in range(len(full_history) - 1, -1, -1):
            if full_history[k] == recent[0]:
                cut_point = k
                found = True
                break
        if not found:
            cut_point = max(0, len(full_history) - len(recent))

    target_assistant = None
    for i in range(cut_point - 1, -1, -1):
        msg = full_history[i]
        if msg.get("role") == "assistant":
            target_assistant = msg
            if msg.get("tool_calls"):
                break

    if target_assistant:
        return [target_assistant] + list(recent)

    return [{
        "role": "assistant",
        "content": "I performed the previous operation."
    }] + list(recent)


class VideoAIAgent:
    """Autonomous Video Optimization & Diagnostic Agent."""

    def __init__(
        self,
        user_id: int,
        task_id: str,
        input_path: Path,
        meta: Optional[MediaMetadata] = None,
        client: Optional[OpenRouterClient] = None,
        preferred_model: Optional[str] = None,
        assets: Optional[Dict[str, Any]] = None
    ):
        self.user_id = user_id
        self.task_id = task_id
        self.input_path = input_path
        self.meta = meta
        self.client = client or ai_client
        self.preferred_model = preferred_model
        self.assets = assets or {}
        self.history: List[Dict[str, Any]] = []
        self.turn_counter: int = 0
        self.tool_executor = SafeToolExecutor(user_id, task_id, input_path, meta, assets=self.assets)

    def _build_system_prompt(self, user_prompt: str) -> str:
        """Constructs a comprehensive, token-efficient system prompt with video metadata, session assets, and dynamic skills."""
        # Video context summary
        video_info = "No video metadata available."
        if self.meta:
            fps_str = f"{int(self.meta.fps)}fps" if self.meta.fps else "N/A"
            video_info = (
                f"- Filename: {self.meta.file_name}\n"
                f"- Size: {format_bytes(self.meta.file_size_bytes)} ({self.meta.file_size_bytes} bytes)\n"
                f"- Duration: {format_duration(self.meta.duration_seconds)} ({int(self.meta.duration_seconds)}s)\n"
                f"- Resolution: {self.meta.resolution_label} ({self.meta.width}x{self.meta.height})\n"
                f"- Video Codec: {self.meta.video_codec} @ ~{self.meta.video_bitrate_kbps or 'unknown'} kbps, {fps_str}\n"
                f"- Audio Codec: {self.meta.audio_codec or 'None'} @ ~{self.meta.audio_bitrate_kbps or 'unknown'} kbps\n"
                f"- Overall Bitrate: ~{self.meta.overall_bitrate_kbps} kbps"
            )

        # Available session assets
        assets_section = ""
        if self.assets:
            lines = []
            for aid, a in self.assets.items():
                m = a.get("meta")
                res = f"Resolution: {m.resolution_label}, " if (m and m.has_video) else ""
                dur = f"Duration: {int(m.duration_seconds)}s, " if m else ""
                size = f"Size: {format_bytes(m.file_size_bytes)}" if m else ""
                tag = " [ORIGINAL USER SOURCE VIDEO]" if aid == "v1" else ""
                lines.append(f"- [{aid}] {a['filename']} ({a.get('media_type', 'media').upper()}: {res}{dur}{size}){tag}")
            assets_section = "### AVAILABLE SESSION ASSETS:\n" + "\n".join(lines) + "\n\n"
        elif self.meta:
            assets_section = f"### AVAILABLE SESSION ASSETS:\n- [v1] {self.meta.file_name} (VIDEO: {self.meta.resolution_label}, {int(self.meta.duration_seconds)}s) [ORIGINAL USER SOURCE VIDEO]\n\n"

        # Host environment info
        host_info = (
            f"- Host OS: {current_hardware.os_name} ({current_hardware.arch})\n"
            f"- Platform: {current_hardware.description}\n"
            f"- CPU Cores: {current_hardware.cpu_cores}\n"
            f"- Available RAM: ~{current_hardware.available_ram_mb} MB"
        )

        # Dynamically inject relevant skills
        skills_context = skill_registry.get_relevant_skills(user_prompt)

        return (
            "You are the **AI Video & Audio Engineer** for a high-performance Telegram bot.\n"
            "You assist users by diagnosing why media lags or has huge file size, and executing custom "
            "optimizations, format conversions, stream extractions (audio-only, video-only), audio replacement, or multi-file edits.\n\n"
            f"{assets_section}"
            "### CURRENT PRIMARY VIDEO METADATA:\n"
            f"{video_info}\n\n"
            "### HOST ENVIRONMENT CONSTRAINTS:\n"
            f"{host_info}\n\n"
            "### RELEVANT KNOWLEDGE BASE & RECIPES:\n"
            f"{skills_context}\n\n"
            "### OPERATIONAL GUIDELINES (STRICT):\n"
            "1. **Action Requests vs Informational Questions:**\n"
            "   - You ALREADY have full verified metadata (resolution, codecs, bitrate, duration) for all files under 'CURRENT PRIMARY VIDEO METADATA' and 'AVAILABLE SESSION ASSETS'. You do NOT need to inspect or diagnose before editing!\n"
            "   - If the user asks you to DO or CHANGE something (e.g. 'optimize it', 'remove audio', 'mute', 'add audio', 'replace audio', 'fix lag', 'make it smooth', 'convert', 'downscale', 'black and white'):\n"
            "     NEVER call `diagnose_video`! `diagnose_video` is STRICTLY reserved for pure questions like 'Why does this video lag on my phone?' when the user asks for an explanation without any editing command.\n"
            "     For any action request, you must call `execute_ffmpeg_plan` or ask the user for missing files directly.\n"
            "2. **Resolution & Quality Guardrails (DO NOT DROP QUALITY):**\n"
            "   - Users prioritize visual clarity. It is completely acceptable if preserving high quality requires extra file size.\n"
            "   - When optimizing, fixing lag, or compressing without losing quality:\n"
            "     * If source is > 1080p (e.g. 4K UHD or 1440p): Downscale ONLY to 1080p FHD (for landscape: scale=-2:1080, for portrait/vertical: scale=1080:-2). NEVER downscale to 720p, 480p, or 360p!\n"
            "     * If source is <= 1080p (e.g. 1080p or 720p): KEEP NATIVE RESOLUTION (do NOT add any scale filter).\n"
            "     * NEVER drop resolution down to 480p or 360p unless the user explicitly types the words '480p', '360p', or 'lowest resolution'.\n"
            "     * Use CRF 20-22 with libx264 for crisp, high-fidelity visual output.\n"
            "   - For ANY video processing (including creative filters like black and white, sepia, saturation) on sources > 1080p (e.g. 4K UHD): Always combine your filter with downscaling to 1080p FHD (portrait: scale=1080:-2, landscape: scale=-2:1080, e.g. vf_filter='hue=s=0,scale=1080:-2') so encoding finishes quickly and smoothly without exhausting CPU or memory while delivering crisp 1080p.\n"
            "3. **Audio Removal & Missing Audio Requests:**\n"
            "   - If the user asks to remove/mute audio AND add a new audio track, but the new audio track is not yet in the session assets ([a1], [a2], etc.):\n"
            "     * If the user ALSO asked to optimize or mute right now: Call `execute_ffmpeg_plan` with mute_video_only=True (audio_codec='none') and 1080p scale to deliver the optimized muted video immediately, AND in your conversational response tell the user: 'I have optimized your video to 1080p without losing quality and removed the existing audio. Please upload or forward the new audio file you want to add, and I will merge it with this video!'\n"
            "     * OR reply in plain English: 'I can optimize the video to 1080p and replace the audio. Please drop or forward the audio file you want to add into this chat now, and I will remove the original audio and merge your new soundtrack!'\n"
            "   - Always clearly prompt the user to drop the file they want to add.\n"
            "4. **Post-Execution Logical Response:**\n"
            "   - When reviewing tool execution results, formulate a logical assistant response:\n"
            "     * If the overall user goal was only partially completed because it requires additional input from the user (such as sending a new audio track or another clip), DO NOT ask if they are satisfied. Instead, clearly prompt them for the required missing file or next step.\n"
            "     * If the requested operation is fully completed, ask what they would like to do next with this file or if they got the expected result.\n"
            "5. **Multi-Asset Referencing & Target Selection ('original' vs 'latest'):**\n"
            "   - If the user explicitly asks for the 'original video', 'source video', 'from scratch', 'initial video', or 'first video':\n"
            "     You MUST set `inputs: ['v1']` so that the operation applies directly to the original user source video! NEVER use an intermediate deliverable like `original_slowed.mp4` or a muted file when the user asks for the original video.\n"
            "   - When deliverables are generated, they are registered as session assets (e.g. [v2], [a1]). If the user says 'this video', 'with this', 'continue', 'in this', or does not specify 'original', use the latest processed deliverable as the primary input.\n"
            "6. **Memory Safety on Snapdragon / Low RAM:** NEVER buffer unbounded frames into RAM. If user asks to reverse a video "
            "> 30s or > 720p, inform them or downscale to 720p.\n"
            "7. **No Emojis:** Do NOT include any emojis or emoji symbols in your responses. Output only plain, professional text.\n"
            "8. **New Asset Uploaded:** If the user provides a new asset (e.g. [a1]) following a previous discussion or request to merge/replace/add audio or video, immediately call `execute_ffmpeg_plan` using the latest video asset (e.g. [v2] or [v1]) and the new audio asset (e.g. [a1]) to perform the merge/replacement.\n"
            "9. **Unrealistic & Impossible Demands (STRICT REFUSAL GUARDRAIL):**\n"
            "   - If the user demands an impossible, unrealistic, or fake operation, such as:\n"
            "     * Upscaling a 1080p, 720p, 480p, or 360p video to 4K UHD, 1440p, or 8K.\n"
            "     * 'Make it 4K', 'convert 1080p to 4K', 'upscale to UHD', 'improve resolution to 4K'.\n"
            "     * Extracting or recovering audio from a silent video that has no audio stream (`has_audio: False` or `audio_codec: None`).\n"
            "     * Generative changes that FFmpeg cannot do (e.g. 'remove person from background', 'change camera angle', 'invent new scenes').\n"
            "     DO NOT call `execute_ffmpeg_plan`! DO NOT fake resolution scaling (e.g. `scale=3840:2160`)! Upscaling creates bloated, unplayable files without adding any real optical detail.\n"
            "     Instead, directly refuse the request in plain conversational text. Explicitly state: 'I cannot fulfill this request because upscaling a 1080p video to 4K is an unrealistic demand. True resolution and optical detail cannot be synthesized with FFmpeg and only inflates file size with no quality improvement. I can, however, optimize, sharpen, or apply color grading at its native resolution.'\n"
            "10. **Creative & Visual Filters (DO NOT EXTRACT FRAMES UNLESS REQUESTED):**\n"
            "   - When the user asks for 'black and white', 'b&w', 'grayscale', or 'monochrome':\n"
            "     Always use video filter `vf_filter='hue=s=0'`, keep `video_codec='libx264'`, `crf=22`, and preserve audio with `audio_codec='aac'`! NEVER set `-frames:v 1`, `-ss`, or create single image files (like `.jpg.mp4`)!\n"
            "   - When the user asks for sepia: use `vf_filter='colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131'`.\n"
            "   - When the user asks for vibrant / saturation: use `vf_filter='hue=s=1.5'`.\n"
            "   - When the user asks for brightness/contrast: use `vf_filter='eq=brightness=0.06:contrast=1.15'`.\n"
            "   - When the user asks for mirror / flip: use `vf_filter='hflip'` or `vf_filter='vflip'`.\n"
            "   - When the user asks for rotate: use `vf_filter='transpose=1'` (90 deg clockwise) or `transpose=2` (counter-clockwise).\n"
            "   - When the user asks for negative / invert: use `vf_filter='negate'`.\n"
            "   - When the user asks for sharpness: use `vf_filter='unsharp=5:5:1.0:5:5:0.0'`.\n"
            "   - When the user asks for blur: use `vf_filter='boxblur=5:2'`.\n"
            "11. **Output FFprobe Awareness & Post-Execution Response:**\n"
            "   - When a tool produces output deliverables, you will receive its exact verified `ffprobe` metadata (resolution, codec, bitrate, duration, file size).\n"
            "   - In your follow-up conversational response, explicitly acknowledge what you generated citing these verified specs (e.g. resolution, duration, file size).\n"
            "   - If the task is finished, ask what they would like to do next or if they are satisfied with the result. If a further step is needed (such as sending a new soundtrack), ask them to send the file.\n"
            "12. **Audio Looping & Duration Matching:**\n"
            "   - When asked to loop, repeat, or extend audio to fill video duration:\n"
            "     * FFmpeg does NOT have an 'arepeat' filter! NEVER use 'arepeat'.\n"
            "     * The correct FFmpeg audio loop filter is `aloop=loop=-1:size=2e+09` (or `aloop=loop=N:size=2e+09`).\n"
            "     * When looping audio with `aloop`, ALWAYS include `-t <duration>` in `extra_args` or `-shortest` to prevent infinite rendering.\n"
            "     * Use `audio_codec='aac'` (filters cannot be applied with audio_codec='copy')."
        )

    async def process_message(
        self,
        user_text: str,
        progress_callback: Optional[ProgressCallback] = None,
        on_process_started: Optional[Callable[[Any], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes one turn of the ReAct reasoning loop.
        Returns a structured dictionary with agent response, tool execution results, and status.
        """
        self.turn_counter += 1
        turn_idx = self.turn_counter
        start_time = time.time()
        media_before = self.meta
        assets_before = dict(self.assets) if self.assets else {}
        active_model = self.preferred_model or getattr(self.client, "default_model", settings.OPENROUTER_MODEL)

        # Append user message
        self.history.append({"role": "user", "content": user_text})

        # Build payload with dynamic system prompt and sanitized history
        system_prompt = self._build_system_prompt(user_text)
        sanitized_history = sanitize_message_history(self.history)
        summary_text, recent_window = compress_old_history(sanitized_history)
        recent_window = guard_truncation_boundary(sanitized_history, recent_window)
        messages = [{"role": "system", "content": system_prompt}]
        if summary_text:
            messages.append({"role": "system", "content": f"Previous session context: {summary_text}"})
        messages.extend(recent_window)

        try:
            assistant_msg = await self.client.chat_completion(
                messages=messages,
                tools=AI_TOOLS_SCHEMA,
                preferred_model=self.preferred_model
            )
        except Exception as e:
            logger.error(f"OpenRouter API error: {e}")
            err_dict = {
                "status": "error",
                "error": f"AI service error: {str(e)}",
                "text": "Sorry, I encountered an issue connecting to the AI service. Please verify your OpenRouter API key or try again."
            }
            ai_debug_logger.log_turn(
                user_id=self.user_id,
                task_id=self.task_id,
                turn_index=turn_idx,
                user_input=user_text,
                model=active_model,
                media_before=media_before,
                assets_before=assets_before,
                system_prompt=system_prompt,
                injected_skills=skill_registry.get_relevant_skills(user_text),
                assistant_raw=None,
                tool_calls=None,
                tool_results=None,
                primary_result=err_dict,
                final_reply=err_dict["text"],
                elapsed_seconds=time.time() - start_time
            )
            return err_dict

        tool_calls = assistant_msg.get("tool_calls")
        content = (assistant_msg.get("content") or "").strip()

        # If no tool calls, pure conversational reply
        if not tool_calls:
            if not content:
                content = "I have processed your request. What would you like to do next?"
            self.history.append({"role": "assistant", "content": content})
            text_result = {
                "status": "success",
                "type": "text",
                "text": content
            }
            ai_debug_logger.log_turn(
                user_id=self.user_id,
                task_id=self.task_id,
                turn_index=turn_idx,
                user_input=user_text,
                model=active_model,
                media_before=media_before,
                assets_before=assets_before,
                system_prompt=system_prompt,
                injected_skills=skill_registry.get_relevant_skills(user_text),
                assistant_raw=assistant_msg,
                tool_calls=None,
                tool_results=None,
                primary_result=text_result,
                final_reply=content,
                elapsed_seconds=time.time() - start_time
            )
            try:
                from database.models import save_ai_session
                await save_ai_session(
                    self.user_id,
                    self.task_id,
                    self.history,
                    self.assets,
                    self.preferred_model or "",
                    str(self.input_path) if self.input_path else ""
                )
            except Exception as e:
                logger.warning(f"Error persisting AI session: {e}")
            return text_result

        # Handle Tool Calls
        self.history.append(assistant_msg)
        execution_results = []

        for call in tool_calls:
            fn_name = call.get("function", {}).get("name")
            raw_args = call.get("function", {}).get("arguments", "{}")
            call_id = call.get("id", "call_default")

            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
            except json.JSONDecodeError:
                args = {}

            logger.info(f"AI Agent executing tool: {fn_name} with args: {args}")

            if fn_name == "diagnose_video":
                res = await self.tool_executor.run_diagnose(
                    explanation=args.get("explanation", ""),
                    recommended_action=args.get("recommended_action", ""),
                    estimated_reduction_percent=args.get("estimated_reduction_percent", 0)
                )
                execution_results.append(res)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(res, default=str)
                })

            elif fn_name == "execute_ffmpeg_plan":
                plan_title = args.get("plan_title", "Custom AI Optimization")
                if progress_callback:
                    try:
                        await progress_callback(
                            0.0,
                            "starting",
                            0,
                            0.0,
                            step_label=f"Reasoning complete. Starting {plan_title}..."
                        )
                    except Exception as p_err:
                        logger.debug(f"Initial progress notification error: {p_err}")

                # Plan preview notification
                if progress_callback:
                    codec = args.get("video_codec", "libx264")
                    crf_val = args.get("crf", 22)
                    vf = args.get("vf_filter", "")
                    resolution = ""
                    if vf and "scale" in vf:
                        import re as _re
                        scale_match = _re.search(r'scale[=:]([^,;]+)', vf)
                        if scale_match:
                            resolution = scale_match.group(1)

                    est_time = ""
                    if self.meta and self.meta.duration_seconds > 0:
                        # Estimate: input_duration / 1.5x speed factor for libx264 on SDM636
                        est_seconds = int(self.meta.duration_seconds / 1.5)
                        from core.ai.tools import _parse_speed_factor
                        speed_factor = _parse_speed_factor(f"{vf} {args.get('af_filter', '')}")
                        if speed_factor and speed_factor > 0:
                            est_seconds = int(est_seconds * speed_factor)
                        est_time = f"~{est_seconds}s"

                    preview_parts = [f"AI Plan: {plan_title}"]
                    if codec and codec != "none":
                        crf_str = f" CRF {crf_val}" if crf_val else ""
                        preview_parts.append(f"Codec: {codec}{crf_str}")
                    if resolution:
                        preview_parts.append(f"Resolution: {resolution}")
                    if est_time:
                        preview_parts.append(f"Estimated time: {est_time}")

                    preview_msg = " | ".join(preview_parts)
                    try:
                        await progress_callback(0.0, "starting", 0, 0.0, step_label=preview_msg)
                    except Exception:
                        pass

                res = await self.tool_executor.run_execute_ffmpeg(
                    plan_title=plan_title,
                    output_filename=args.get("output_filename", "ai_optimized.mp4"),
                    operations=args.get("operations"),
                    inputs=args.get("inputs"),
                    extract_audio_only=args.get("extract_audio_only", False),
                    mute_video_only=args.get("mute_video_only", False),
                    vf_filter=args.get("vf_filter"),
                    af_filter=args.get("af_filter"),
                    video_codec=args.get("video_codec", "libx264"),
                    crf=args.get("crf", 22),
                    audio_codec=args.get("audio_codec", "aac"),
                    stream_mapping=args.get("stream_mapping"),
                    extra_args=args.get("extra_args"),
                    progress_callback=progress_callback,
                    on_process_started=on_process_started
                )
                execution_results.append(res)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(res, default=str)
                })

            elif fn_name == "verify_media":
                res = await self.tool_executor.run_verify_media(args.get("filename", ""))
                execution_results.append(res)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(res, default=str)
                })

            elif fn_name == "list_workspace_files":
                res = await self.tool_executor.run_list_workspace_files()
                execution_results.append(res)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(res, default=str)
                })

        # Primary tool result
        primary_result = execution_results[0] if execution_results else {"status": "success", "type": "none"}

        # Follow-up completion turn so the model evaluates tool results and produces assistant response
        # tools MUST be None here so the model provides a final conversational answer instead of an unexpected tool call
        if progress_callback:
            try:
                await progress_callback(100.0, "done", 0, 0.0, step_label="Transcoding complete. Formulating AI summary...")
            except Exception:
                pass

        try:
            sanitized_follow_up = sanitize_message_history(self.history, complete_trailing=False)
            fu_summary, fu_recent = compress_old_history(sanitized_follow_up)
            fu_recent = guard_truncation_boundary(sanitized_follow_up, fu_recent)
            follow_up_messages = [{"role": "system", "content": self._build_system_prompt(user_text)}]
            if fu_summary:
                follow_up_messages.append({"role": "system", "content": f"Previous session context: {fu_summary}"})
            follow_up_messages.extend(fu_recent)
            follow_up_msg = await asyncio.wait_for(
                self.client.chat_completion(
                    messages=follow_up_messages,
                    tools=None,
                    preferred_model=self.preferred_model
                ),
                timeout=30.0
            )
            follow_up_content = (follow_up_msg.get("content") or "").strip()
            if not follow_up_content:
                out_ffprobe = primary_result.get("ffprobe") or {}
                res_str = out_ffprobe.get("resolution", primary_result.get("resolution", ""))
                size_str = out_ffprobe.get("formatted_size", primary_result.get("formatted_size", ""))
                dur_str = out_ffprobe.get("formatted_duration", primary_result.get("duration", ""))
                spec_note = f" ({res_str}, {size_str}, {dur_str})" if (res_str and size_str) else ""
                follow_up_content = (
                    f"I have successfully executed the operation: {primary_result.get('plan_title', 'video processing')}{spec_note}. "
                    "What would you like to do next with this file?"
                )
            self.history.append({"role": "assistant", "content": follow_up_content})
            primary_result["assistant_text"] = follow_up_content
        except Exception as follow_up_err:
            logger.warning(f"Error in follow-up AI completion: {type(follow_up_err).__name__} ({follow_up_err or 'timeout'})")
            if primary_result.get("status") == "error":
                fallback_text = (
                    f"The operation could not be completed: {primary_result.get('error', 'an error occurred during FFmpeg execution')}."
                )
            else:
                out_ffprobe = primary_result.get("ffprobe") or {}
                res_str = out_ffprobe.get("resolution", primary_result.get("resolution", ""))
                size_str = out_ffprobe.get("formatted_size", primary_result.get("formatted_size", ""))
                dur_str = out_ffprobe.get("formatted_duration", primary_result.get("duration", ""))
                spec_note = f" ({res_str}, {size_str}, {dur_str})" if (res_str and size_str) else ""
                fallback_text = (
                    f"I have executed the operation: {primary_result.get('plan_title', 'video processing')}{spec_note}. "
                    "What would you like to do next with this file?"
                )
            self.history.append({"role": "assistant", "content": fallback_text})
            primary_result["assistant_text"] = fallback_text

        ai_debug_logger.log_turn(
            user_id=self.user_id,
            task_id=self.task_id,
            turn_index=turn_idx,
            user_input=user_text,
            model=active_model,
            media_before=media_before,
            assets_before=assets_before,
            system_prompt=system_prompt,
            injected_skills=skill_registry.get_relevant_skills(user_text),
            assistant_raw=assistant_msg,
            tool_calls=tool_calls,
            tool_results=execution_results,
            primary_result=primary_result,
            final_reply=primary_result.get("assistant_text") or primary_result.get("text", ""),
            elapsed_seconds=time.time() - start_time
        )

        try:
            from database.models import save_ai_session
            await save_ai_session(
                self.user_id,
                self.task_id,
                self.history,
                self.assets,
                self.preferred_model or "",
                str(self.input_path) if self.input_path else ""
            )
        except Exception as e:
            logger.warning(f"Error persisting AI session: {e}")

        return primary_result

    # Alias for run_turn (compatible with production hardening tasks)
    run_turn = process_message
