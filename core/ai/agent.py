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
        client: Optional[OpenRouterClient] = None
    ):
        self.user_id = user_id
        self.task_id = task_id
        self.input_path = input_path
        self.meta = meta
        self.client = client or ai_client
        self.history: List[Dict[str, Any]] = []
        self.tool_executor = SafeToolExecutor(user_id, task_id, input_path, meta)

    def _build_system_prompt(self, user_prompt: str) -> str:
        """Constructs a comprehensive, token-efficient system prompt with video metadata and dynamic skills."""
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
            "You are the **AI Video Engineer & Optimization Specialist** for a high-performance Telegram bot.\n"
            "You assist users by diagnosing why videos lag, stutter, or have huge file size, and executing custom "
            "optimizations, format conversions, aspect ratio changes, or creative modifications (reverse, speed, etc.).\n\n"
            "### CURRENT TARGET VIDEO METADATA:\n"
            f"{video_info}\n\n"
            "### HOST ENVIRONMENT CONSTRAINTS:\n"
            f"{host_info}\n\n"
            "### RELEVANT KNOWLEDGE BASE & RECIPES:\n"
            f"{skills_context}\n\n"
            "### OPERATIONAL GUIDELINES:\n"
            "1. **Diagnostic Questions:** If user asks why the video lags, why size is huge, or about device compatibility, "
            "call the `diagnose_video` tool or explain clearly based on the metadata above, explaining the root cause.\n"
            "2. **Editing / Conversion Requests:** If user asks to change resolution, downscale, reverse, change ratio (e.g. 9:16), "
            "or optimize, call the `execute_ffmpeg_plan` tool with appropriate filter chains.\n"
            "3. **Memory Safety on Snapdragon / Low RAM:** NEVER buffer unbounded frames into RAM. If user asks to reverse a video "
            "> 30s or > 720p, inform them or downscale to 720p.\n"
            "4. **Tone & Style:** Be concise, professional, technically accurate, and user-friendly."
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
                tools=AI_TOOLS_SCHEMA
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
                    "content": json.dumps(res)
                })

            elif fn_name == "execute_ffmpeg_plan":
                res = await self.tool_executor.run_execute_ffmpeg(
                    plan_title=args.get("plan_title", "Custom AI Optimization"),
                    output_filename=args.get("output_filename", "ai_optimized.mp4"),
                    vf_filter=args.get("vf_filter"),
                    af_filter=args.get("af_filter"),
                    video_codec=args.get("video_codec", "libx264"),
                    crf=args.get("crf", 22),
                    audio_codec=args.get("audio_codec", "aac"),
                    progress_callback=progress_callback,
                    on_process_started=on_process_started
                )
                execution_results.append(res)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(res)
                })

            elif fn_name == "verify_media":
                res = await self.tool_executor.run_verify_media(args.get("filename", ""))
                execution_results.append(res)
                self.history.append({
                    "role": "tool",
                    "tool_call_id": call_id,
                    "name": fn_name,
                    "content": json.dumps(res)
                })

        # Primary tool result
        primary_result = execution_results[0] if execution_results else {"status": "success", "type": "none"}
        primary_result["assistant_text"] = content
        return primary_result
