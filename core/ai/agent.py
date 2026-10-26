"""
AI Video Engineer Agent (ReAct Loop)
Manages multi-turn conversation, dynamically injects skills, executes sandboxed tools,
and provides technical diagnosis or video optimization.
"""

import json
import logging
from pathlib import Path
from typing import List, Dict, Any, Optional, Callable, Awaitable
from core.probe import MediaMetadata, format_bytes, format_duration
from core.hardware import current_hardware
from core.engine import ProgressCallback
from core.ai.client import ai_client, OpenRouterClient
from core.ai.skills import skill_registry
from core.ai.tools import AI_TOOLS_SCHEMA, SafeToolExecutor

logger = logging.getLogger(__name__)


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
                lines.append(f"- [{aid}] {a['filename']} ({a.get('media_type', 'media').upper()}: {res}{dur}{size})")
            assets_section = "### AVAILABLE SESSION ASSETS:\n" + "\n".join(lines) + "\n\n"
        elif self.meta:
            assets_section = f"### AVAILABLE SESSION ASSETS:\n- [v1] {self.meta.file_name} (VIDEO: {self.meta.resolution_label}, {int(self.meta.duration_seconds)}s)\n\n"

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
            "   - If the user asks you to DO or CHANGE something (e.g. 'optimize it', 'remove audio', 'mute', 'add audio', 'replace audio', 'fix lag', 'make it smooth', 'convert', 'downscale'):\n"
            "     NEVER call `diagnose_video`! `diagnose_video` is STRICTLY reserved for pure questions like 'Why does this video lag on my phone?' when the user asks for an explanation without any editing command.\n"
            "     For any action request, you must call `execute_ffmpeg_plan` or ask the user for missing files directly.\n"
            "2. **Resolution & Quality Guardrails (DO NOT DROP QUALITY):**\n"
            "   - Users prioritize visual clarity. It is completely acceptable if preserving high quality requires extra file size.\n"
            "   - When optimizing, fixing lag, or compressing without losing quality:\n"
            "     * If source is > 1080p (e.g. 4K UHD or 1440p): Downscale ONLY to 1080p FHD (for landscape: scale=-2:1080, for portrait/vertical: scale=1080:-2). NEVER downscale to 720p, 480p, or 360p!\n"
            "     * If source is <= 1080p (e.g. 1080p or 720p): KEEP NATIVE RESOLUTION (do NOT add any scale filter).\n"
            "     * NEVER drop resolution down to 480p or 360p unless the user explicitly types the words '480p', '360p', or 'lowest resolution'.\n"
            "     * Use CRF 20-22 with libx264 for crisp, high-fidelity visual output.\n"
            "3. **Audio Removal & Missing Audio Requests:**\n"
            "   - If the user asks to remove/mute audio AND add a new audio track, but the new audio track is not yet in the session assets ([a1], [a2], etc.):\n"
            "     * If the user ALSO asked to optimize or mute right now: Call `execute_ffmpeg_plan` with mute_video_only=True (audio_codec='none') and 1080p scale to deliver the optimized muted video immediately, AND in your conversational response tell the user: 'I have optimized your video to 1080p without losing quality and removed the existing audio. Please upload or forward the new audio file you want to add, and I will merge it with this video!'\n"
            "     * OR reply in plain English: 'I can optimize the video to 1080p and replace the audio. Please drop or forward the audio file you want to add into this chat now, and I will remove the original audio and merge your new soundtrack!'\n"
            "   - Always clearly prompt the user to drop the file they want to add.\n"
            "4. **Multi-Asset Referencing:** Reference session assets by their keys (e.g. 'v1', 'a1') in the inputs list.\n"
            "5. **Memory Safety on Snapdragon / Low RAM:** NEVER buffer unbounded frames into RAM. If user asks to reverse a video "
            "> 30s or > 720p, inform them or downscale to 720p.\n"
            "6. **No Emojis:** Do NOT include any emojis or emoji symbols in your responses. Output only plain, professional text.\n"
            "7. **New Asset Uploaded:** If the user provides a new asset (e.g. [a1]) following a previous discussion or request to merge/replace/add audio or video, immediately call `execute_ffmpeg_plan` to perform the operation."
        )

    async def process_message(
        self,
        user_text: str,
        progress_callback: Optional[ProgressCallback] = None,
        on_process_started: Optional[Callable[[any], None]] = None
    ) -> Dict[str, Any]:
        """
        Executes one turn of the ReAct reasoning loop.
        Returns a structured dictionary with agent response, tool execution results, and status.
        """
        # Append user message
        self.history.append({"role": "user", "content": user_text})

        # Build payload with dynamic system prompt
        system_prompt = self._build_system_prompt(user_text)
        messages = [{"role": "system", "content": system_prompt}] + self.history[-8:]  # keep recent 8 turns

        try:
            assistant_msg = await self.client.chat_completion(
                messages=messages,
                tools=AI_TOOLS_SCHEMA,
                preferred_model=self.preferred_model
            )
        except Exception as e:
            logger.error(f"OpenRouter API error: {e}")
            return {
                "status": "error",
                "error": f"AI service error: {str(e)}",
                "text": "Sorry, I encountered an issue connecting to the AI service. Please verify your OpenRouter API key or try again."
            }

        tool_calls = assistant_msg.get("tool_calls")
        content = assistant_msg.get("content") or ""

        # If no tool calls, pure conversational reply
        if not tool_calls:
            self.history.append({"role": "assistant", "content": content})
            return {
                "status": "success",
                "type": "text",
                "text": content
            }

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

        # Primary tool result
        primary_result = execution_results[0] if execution_results else {"status": "success", "type": "none"}
        primary_result["assistant_text"] = content
        return primary_result
