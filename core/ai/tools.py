"""
Safe Tool Dispatcher & OpenRouter Function Calling Definitions
Provides vetted, sandboxed video operations strictly confined to storage/Ai_Workplace/
"""

import os
import json
import logging
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable
from core.probe import probe_media, MediaMetadata, format_bytes, format_duration
from core.hardware import current_hardware
from core.engine import run_ffmpeg_with_progress, ProgressCallback
from core.ai.skills import skill_registry
from config.settings import settings

logger = logging.getLogger(__name__)


# ==============================================================================
# OpenRouter Tool Schemas (OpenAI-compatible Function Calling)
# ==============================================================================

AI_TOOLS_SCHEMA: List[Dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "diagnose_video",
            "description": "Provide a technical yet easy-to-understand explanation of why the video lags, stutters, has huge file size bloat, or fails on mobile devices, and recommend a concrete fix.",
            "parameters": {
                "type": "object",
                "properties": {
                    "explanation": {
                        "type": "string",
                        "description": "Clear explanation of the bottleneck or bloat based on metadata (resolution, codec, bitrate, fps)."
                    },
                    "recommended_action": {
                        "type": "string",
                        "description": "Short, actionable solution (e.g. 'Downscale to 1080p H.264 at ~3500 kbps to eliminate mobile decoding lag')."
                    },
                    "estimated_reduction_percent": {
                        "type": "integer",
                        "description": "Estimated percentage reduction in file size (e.g. 80 for 80% reduction), or 0 if pure diagnostic."
                    }
                },
                "required": ["explanation", "recommended_action"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "execute_ffmpeg_plan",
            "description": "Execute a video transformation, optimization, or creative effect plan using FFmpeg inside the isolated Ai_Workplace sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_title": {
                        "type": "string",
                        "description": "Short headline for the transcode operation (e.g. 'Downscale 4K to 1080p FHD' or 'Convert Aspect Ratio to 9:16 Vertical')."
                    },
                    "vf_filter": {
                        "type": "string",
                        "description": "Video filter chain for FFmpeg (e.g. 'scale=-2:1080' or 'crop=ih*9/16:ih' or 'scale=-2:720,reverse'). Leave empty if untouched."
                    },
                    "af_filter": {
                        "type": "string",
                        "description": "Audio filter chain for FFmpeg (e.g. 'areverse' or 'atempo=2.0'). Leave empty if untouched."
                    },
                    "video_codec": {
                        "type": "string",
                        "enum": ["libx264", "libx265", "copy"],
                        "description": "Target video encoder. Default is 'libx264' for universal compatibility."
                    },
                    "crf": {
                        "type": "integer",
                        "description": "Constant Rate Factor (18-28). Lower is higher quality. Default 22."
                    },
                    "audio_codec": {
                        "type": "string",
                        "enum": ["aac", "copy", "mp3"],
                        "description": "Target audio codec. Use 'aac' for universal smartphone compatibility."
                    },
                    "output_filename": {
                        "type": "string",
                        "description": "Name of output file to create inside Ai_Workplace (e.g. 'video_1080p.mp4')."
                    }
                },
                "required": ["plan_title", "output_filename"]
            }
        }
    },
    {
        "type": "function",
        "function": {
            "name": "verify_media",
            "description": "Probe and inspect an intermediate or final media file generated inside the sandbox to verify stream integrity and quality before delivery.",
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {
                        "type": "string",
                        "description": "Filename inside the sandbox workplace to verify."
                    }
                },
                "required": ["filename"]
            }
        }
    }
]


# ==============================================================================
# Tool Execution Implementations
# ==============================================================================

class SafeToolExecutor:
    """Executes validated tool requests within a sandboxed workplace."""

    def __init__(self, user_id: int, task_id: str, input_path: Path, meta: Optional[MediaMetadata] = None):
        self.user_id = user_id
        self.task_id = task_id
        self.input_path = input_path
        self.meta = meta
        self.workplace_dir = settings.get_ai_workplace(user_id, task_id)

    async def run_diagnose(self, explanation: str, recommended_action: str, estimated_reduction_percent: int = 0) -> Dict[str, Any]:
        """Handles diagnostic analysis."""
        return {
            "status": "success",
            "type": "diagnosis",
            "explanation": explanation,
            "recommended_action": recommended_action,
            "estimated_reduction_percent": estimated_reduction_percent
        }

    async def run_execute_ffmpeg(
        self,
        plan_title: str,
        output_filename: str,
        vf_filter: Optional[str] = None,
        af_filter: Optional[str] = None,
        video_codec: str = "libx264",
        crf: int = 22,
        audio_codec: str = "aac",
        progress_callback: Optional[ProgressCallback] = None,
        on_process_started: Optional[Callable[[any], None]] = None
    ) -> Dict[str, Any]:
        """
        Validates arguments, enforces Snapdragon 636 / mobile memory guardrails,
        and executes FFmpeg writing strictly to workplace_dir.
        """
        # 1. Sanitize output filename and confine to sandbox
        safe_filename = Path(output_filename).name
        if not safe_filename.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov", ".mp3", ".m4a")):
            safe_filename += ".mp4"
        output_path = self.workplace_dir / safe_filename

        # 2. Check Memory Safety for Reverse operations
        is_reverse = False
        if (vf_filter and "reverse" in vf_filter) or (af_filter and "areverse" in af_filter):
            is_reverse = True
            dur = self.meta.duration_seconds if self.meta else 0.0
            w = self.meta.width if self.meta else 1920
            h = self.meta.height if self.meta else 1080
            safe, warn = skill_registry.check_memory_safety(dur, w, h, is_reverse=True, is_termux=current_hardware.is_termux)
            if not safe:
                return {
                    "status": "error",
                    "error": f"Memory Safety Constraint: {warn}"
                }
            # If warning suggests downscaling and filter doesn't already scale down:
            if "downscaling to 720p" in warn and "scale" not in (vf_filter or ""):
                vf_filter = f"scale=-2:720,{vf_filter}" if vf_filter else "scale=-2:720,reverse"

        # 3. Assemble FFmpeg Command
        cmd = ["-i", str(self.input_path)]

        # Video Filter
        if vf_filter and vf_filter.strip():
            cmd.extend(["-vf", vf_filter.strip()])

        # Video Codec
        if video_codec == "copy":
            cmd.extend(["-c:v", "copy"])
        else:
            cmd.extend(["-c:v", video_codec])
            # Hardware / mobile presets
            if current_hardware.is_termux:
                cmd.extend(["-preset", "veryfast", "-tune", "zerolatency"])
            else:
                cmd.extend(["-preset", "fast"])
            cmd.extend(["-crf", str(crf), "-pix_fmt", "yuv420p", "-threads", str(current_hardware.cpu_cores)])

        # Audio Filter & Codec
        if af_filter and af_filter.strip():
            cmd.extend(["-af", af_filter.strip()])

        if audio_codec == "copy":
            cmd.extend(["-c:a", "copy"])
        elif audio_codec == "aac":
            cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
        elif audio_codec == "mp3":
            cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])

        # Streamability flag
        cmd.extend(["-movflags", "+faststart"])
        cmd.append(str(output_path))

        # 4. Execute with async live progress
        duration = self.meta.duration_seconds if self.meta else 0.0
        try:
            await run_ffmpeg_with_progress(
                cmd=cmd,
                total_duration_seconds=duration,
                progress_callback=progress_callback,
                on_process_started=on_process_started
            )
        except Exception as e:
            logger.error(f"Sandbox FFmpeg execution failed: {e}")
            return {
                "status": "error",
                "error": f"FFmpeg execution failed: {str(e)}"
            }

        # 5. Verify Output
        if not output_path.exists() or output_path.stat().st_size == 0:
            return {
                "status": "error",
                "error": "Transcoding finished but output file was not generated or is empty."
            }

        out_meta = await probe_media(output_path)
        return {
            "status": "success",
            "type": "transcode",
            "plan_title": plan_title,
            "output_path": str(output_path),
            "output_filename": safe_filename,
            "output_size_bytes": output_path.stat().st_size,
            "formatted_size": format_bytes(output_path.stat().st_size),
            "resolution": out_meta.resolution_label if out_meta else "Unknown",
            "duration": format_duration(out_meta.duration_seconds) if out_meta else "Unknown"
        }

    async def run_verify_media(self, filename: str) -> Dict[str, Any]:
        """Probes a file inside the workplace."""
        file_path = self.workplace_dir / Path(filename).name
        if not file_path.exists():
            return {"status": "error", "error": f"File '{filename}' does not exist in workplace."}

        meta = await probe_media(file_path)
        if not meta:
            return {"status": "error", "error": "Failed to probe media."}

        return {
            "status": "success",
            "filename": meta.file_name,
            "size": format_bytes(meta.file_size_bytes),
            "duration": format_duration(meta.duration_seconds),
            "resolution": meta.resolution_label,
            "video_codec": meta.video_codec,
            "audio_codec": meta.audio_codec,
            "bitrate": f"{meta.overall_bitrate_kbps} kbps"
        }
