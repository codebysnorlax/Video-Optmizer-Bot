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
            "description": "Execute video/audio transformation, optimization, extraction, or multi-file editing operations using FFmpeg inside the isolated Ai_Workplace sandbox.",
            "parameters": {
                "type": "object",
                "properties": {
                    "plan_title": {
                        "type": "string",
                        "description": "Descriptive headline for the operation (e.g. 'Separate Video and Audio Tracks' or 'Replace Audio Track with New Sound')."
                    },
                    "operations": {
                        "type": "array",
                        "description": "List of FFmpeg operations to execute. Use this for compound tasks (e.g. extracting audio + muting video) or multi-input remuxing.",
                        "items": {
                            "type": "object",
                            "properties": {
                                "inputs": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Input asset keys (e.g. ['v1'] or ['v1', 'a1']) or filenames."
                                },
                                "output_filename": {
                                    "type": "string",
                                    "description": "Target filename for this output (e.g. 'video_mute.mp4' or 'extracted_audio.mp3')."
                                },
                                "extract_audio_only": {
                                    "type": "boolean",
                                    "description": "If true, extracts audio without video stream (-vn)."
                                },
                                "mute_video_only": {
                                    "type": "boolean",
                                    "description": "If true, strips audio stream producing muted video (-an)."
                                },
                                "video_codec": {
                                    "type": "string",
                                    "enum": ["libx264", "libx265", "copy", "none"],
                                    "description": "Video encoder. Use 'copy' for lossless stream copy, 'none' for audio-only."
                                },
                                "audio_codec": {
                                    "type": "string",
                                    "enum": ["aac", "mp3", "copy", "none"],
                                    "description": "Audio encoder. Use 'none' for video-only / muted output."
                                },
                                "vf_filter": {
                                    "type": "string",
                                    "description": "Video filter chain for FFmpeg (e.g. 'scale=-2:1080')."
                                },
                                "af_filter": {
                                    "type": "string",
                                    "description": "Audio filter chain for FFmpeg."
                                },
                                "crf": {
                                    "type": "integer",
                                    "description": "CRF quality factor (18-28). Default 22."
                                },
                                "stream_mapping": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Stream mapping options (e.g. ['-map', '0:v:0', '-map', '1:a:0'])."
                                },
                                "extra_args": {
                                    "type": "array",
                                    "items": {"type": "string"},
                                    "description": "Safe additional flags (e.g. ['-shortest'] or ['-ss', '00:00:05'])."
                                }
                            },
                            "required": ["output_filename"]
                        }
                    },
                    "inputs": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Input asset keys (for single-step plan, e.g. ['v1'])."
                    },
                    "output_filename": {
                        "type": "string",
                        "description": "Output filename (for single-step plan, e.g. 'optimized.mp4')."
                    },
                    "extract_audio_only": {
                        "type": "boolean",
                        "description": "If true, extract audio only (-vn)."
                    },
                    "mute_video_only": {
                        "type": "boolean",
                        "description": "If true, mute video (-an)."
                    },
                    "vf_filter": {
                        "type": "string",
                        "description": "Video filter chain."
                    },
                    "af_filter": {
                        "type": "string",
                        "description": "Audio filter chain."
                    },
                    "video_codec": {
                        "type": "string",
                        "enum": ["libx264", "libx265", "copy", "none"],
                        "description": "Video codec."
                    },
                    "audio_codec": {
                        "type": "string",
                        "enum": ["aac", "copy", "mp3", "none"],
                        "description": "Audio codec."
                    },
                    "crf": {
                        "type": "integer",
                        "description": "CRF (18-28)."
                    }
                },
                "required": ["plan_title"]
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

    def __init__(
        self,
        user_id: int,
        task_id: str,
        input_path: Path,
        meta: Optional[MediaMetadata] = None,
        assets: Optional[Dict[str, Any]] = None
    ):
        self.user_id = user_id
        self.task_id = task_id
        self.input_path = input_path
        self.meta = meta
        self.assets = assets or {}
        self.workplace_dir = settings.get_ai_workplace(user_id, task_id)

    def _resolve_input_path(self, identifier: str) -> Path:
        """Resolves an asset token (e.g. 'v1', 'a1'), filename, or defaults to primary input path."""
        ident = str(identifier).strip()
        # 1. Match in session assets
        if self.assets and ident in self.assets:
            return Path(self.assets[ident]["filepath"])
        # 2. Match by filename in session assets
        if self.assets:
            for a in self.assets.values():
                if a.get("filename") == ident or Path(a.get("filepath", "")).name == ident:
                    return Path(a["filepath"])
        # 3. Match in workplace directory (intermediate chaining)
        workplace_p = self.workplace_dir / Path(ident).name
        if workplace_p.exists():
            return workplace_p
        # 4. Match in downloads directory
        download_p = settings.DOWNLOAD_DIR / Path(ident).name
        if download_p.exists():
            return download_p
        # 5. Fallback to primary input
        return self.input_path

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
        output_filename: Optional[str] = None,
        operations: Optional[List[Dict[str, Any]]] = None,
        inputs: Optional[List[str]] = None,
        extract_audio_only: bool = False,
        mute_video_only: bool = False,
        vf_filter: Optional[str] = None,
        af_filter: Optional[str] = None,
        video_codec: str = "libx264",
        crf: int = 22,
        audio_codec: str = "aac",
        stream_mapping: Optional[List[str]] = None,
        extra_args: Optional[List[str]] = None,
        progress_callback: Optional[ProgressCallback] = None,
        on_process_started: Optional[Callable[[any], None]] = None
    ) -> Dict[str, Any]:
        """
        Validates arguments, enforces Snapdragon 636 / mobile memory guardrails,
        and executes single or compound FFmpeg operations writing strictly to workplace_dir.
        """
        # Normalize into a list of operations
        op_list: List[Dict[str, Any]] = []
        if operations and len(operations) > 0:
            op_list = operations
        else:
            default_out = output_filename or f"ai_fixed_{self.input_path.stem}.mp4"
            op_list = [{
                "inputs": inputs or ["v1"],
                "output_filename": default_out,
                "extract_audio_only": extract_audio_only,
                "mute_video_only": mute_video_only,
                "vf_filter": vf_filter,
                "af_filter": af_filter,
                "video_codec": video_codec,
                "crf": crf,
                "audio_codec": audio_codec,
                "stream_mapping": stream_mapping,
                "extra_args": extra_args
            }]

        deliverables: List[Dict[str, Any]] = []

        for op_idx, op in enumerate(op_list, start=1):
            raw_out_name = op.get("output_filename") or f"output_{op_idx}.mp4"
            safe_filename = Path(raw_out_name).name
            op_extract_audio = op.get("extract_audio_only", False) or (op.get("video_codec") == "none")
            op_mute_video = op.get("mute_video_only", False) or (op.get("audio_codec") == "none")

            # Determine appropriate extension if omitted
            if not safe_filename.endswith((".mp4", ".mkv", ".webm", ".avi", ".mov", ".mp3", ".m4a", ".wav", ".flac")):
                safe_filename += ".mp3" if op_extract_audio else ".mp4"

            out_path = self.workplace_dir / safe_filename

            # Resolve input paths
            op_inputs = op.get("inputs") or ["v1"]
            resolved_inputs = [self._resolve_input_path(str(i)) for i in op_inputs]
            for p in resolved_inputs:
                if not p.exists():
                    return {"status": "error", "error": f"Input asset '{p.name}' does not exist on server."}

            op_vf = op.get("vf_filter")
            op_af = op.get("af_filter")

            # Check Memory Safety for Reverse operations
            if (op_vf and "reverse" in op_vf) or (op_af and "areverse" in op_af):
                dur = self.meta.duration_seconds if self.meta else 0.0
                w = self.meta.width if self.meta else 1920
                h = self.meta.height if self.meta else 1080
                safe, warn = skill_registry.check_memory_safety(dur, w, h, is_reverse=True, is_termux=current_hardware.is_termux)
                if not safe:
                    return {"status": "error", "error": f"Memory Safety Constraint: {warn}"}
                if "downscaling to 720p" in warn and "scale" not in (op_vf or ""):
                    op_vf = f"scale=-2:720,{op_vf}" if op_vf else "scale=-2:720,reverse"

            # Assemble FFmpeg Command
            cmd = []
            for p in resolved_inputs:
                cmd.extend(["-i", str(p)])

            # Stream Mapping
            if op.get("stream_mapping"):
                for m in op["stream_mapping"]:
                    if isinstance(m, str):
                        parts = m.strip().split()
                        if len(parts) == 1 and not parts[0].startswith("-"):
                            cmd.extend(["-map", parts[0]])
                        else:
                            cmd.extend(parts)
                    elif isinstance(m, list):
                        cmd.extend(m)

            # Video Stream Handling
            if op_extract_audio:
                cmd.append("-vn")
            else:
                vcodec = op.get("video_codec", "libx264")
                if op_mute_video and vcodec == "libx264" and (not op_vf or not op_vf.strip()):
                    vcodec = "copy"
                if op_vf and op_vf.strip():
                    cmd.extend(["-vf", op_vf.strip()])
                if vcodec == "copy":
                    cmd.extend(["-c:v", "copy"])
                elif vcodec != "none":
                    cmd.extend(["-c:v", vcodec])
                    if current_hardware.is_termux:
                        cmd.extend(["-preset", "veryfast", "-tune", "zerolatency"])
                    else:
                        cmd.extend(["-preset", "fast"])
                    op_crf = op.get("crf", 22)
                    cmd.extend(["-crf", str(op_crf), "-pix_fmt", "yuv420p", "-threads", str(current_hardware.cpu_cores)])

            # Audio Stream Handling
            if op_mute_video:
                cmd.append("-an")
            else:
                acodec = op.get("audio_codec", "aac")
                if safe_filename.lower().endswith(".mp3") and acodec == "aac":
                    acodec = "mp3"
                if op_af and op_af.strip():
                    cmd.extend(["-af", op_af.strip()])
                if acodec == "copy":
                    cmd.extend(["-c:a", "copy"])
                elif acodec == "aac":
                    cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
                elif acodec in ("mp3", "libmp3lame"):
                    cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])

            # Extra safe arguments (e.g. -shortest, -ss, -to)
            if op.get("extra_args"):
                for ea in op["extra_args"]:
                    if isinstance(ea, str):
                        cmd.extend(ea.split())

            # Faststart for web streaming containers
            if safe_filename.endswith((".mp4", ".mov", ".m4v", ".m4a")):
                cmd.extend(["-movflags", "+faststart"])

            cmd.append(str(out_path))

            # Duration estimation for progress bar
            est_duration = 0.0
            for p in resolved_inputs:
                try:
                    p_meta = await probe_media(p)
                    if p_meta and p_meta.duration_seconds > est_duration:
                        est_duration = p_meta.duration_seconds
                except Exception:
                    pass
            if est_duration <= 0.0 and self.meta:
                est_duration = self.meta.duration_seconds

            try:
                await run_ffmpeg_with_progress(
                    cmd=cmd,
                    total_duration_seconds=est_duration,
                    progress_callback=progress_callback,
                    on_process_started=on_process_started
                )
            except Exception as e:
                logger.error(f"Sandbox FFmpeg execution failed for {safe_filename}: {e}")
                return {
                    "status": "error",
                    "error": f"FFmpeg execution failed on {safe_filename}: {str(e)}"
                }

            if not out_path.exists() or out_path.stat().st_size == 0:
                return {
                    "status": "error",
                    "error": f"Operation finished but output file '{safe_filename}' was not generated or is empty."
                }

            out_meta = await probe_media(out_path)
            deliverables.append({
                "filename": safe_filename,
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size,
                "formatted_size": format_bytes(out_path.stat().st_size),
                "resolution": out_meta.resolution_label if (out_meta and out_meta.has_video) else "Audio Track",
                "duration": format_duration(out_meta.duration_seconds) if out_meta else "Unknown",
                "meta": out_meta
            })

        if not deliverables:
            return {"status": "error", "error": "No deliverables produced by operations."}

        first = deliverables[0]
        return {
            "status": "success",
            "type": "transcode",
            "plan_title": plan_title,
            "output_paths": [d["path"] for d in deliverables],
            "output_path": first["path"],
            "deliverables": deliverables,
            "output_filename": first["filename"],
            "output_size_bytes": first["size_bytes"],
            "formatted_size": first["formatted_size"],
            "resolution": first["resolution"],
            "duration": first["duration"]
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
