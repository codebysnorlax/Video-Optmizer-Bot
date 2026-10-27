"""
Safe Tool Dispatcher & OpenRouter Function Calling Definitions
Provides vetted, sandboxed video operations strictly confined to storage/Ai_Workplace/
"""

import os
import json
import logging
import re
import shutil
from pathlib import Path
from typing import Dict, Any, List, Optional, Callable, Awaitable, Tuple
from core.probe import probe_media, MediaMetadata, format_bytes, format_duration, clean_display_name
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
            "description": "Provide a technical explanation ONLY when user asks a pure informational question without requesting any change (e.g. 'Why does this video lag on my phone?'). NEVER call this tool to inspect a video before editing; all metadata is already provided. For any editing, optimization, audio removal, or conversion request, call execute_ffmpeg_plan directly.",
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
                    },
                    "stream_mapping": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Stream mapping options (e.g. ['-map', '0:v:0', '-map', '1:a:0'])."
                    },
                    "extra_args": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": "Safe additional flags (e.g. ['-shortest'])."
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
    },
    {
        "type": "function",
        "function": {
            "name": "list_workspace_files",
            "description": "List all intermediate and output files currently in the AI workplace sandbox directory.",
            "parameters": {
                "type": "object",
                "properties": {},
                "required": []
            }
        }
    }
]


# ==============================================================================
# Tool Execution Implementations
# ==============================================================================

def _normalize_string_or_list(val: Any) -> List[str]:
    """
    Safely normalizes arguments that may arrive as a python list, a JSON-stringified list
    (e.g. '["-map", "0:v:0"]'), comma-separated string, or space-separated string into List[str].
    """
    if not val:
        return []
    if isinstance(val, str):
        s = val.strip()
        if s.startswith("[") and s.endswith("]"):
            try:
                parsed = json.loads(s)
                if isinstance(parsed, list):
                    return [str(x).strip().strip("'\"") for x in parsed if str(x).strip()]
            except Exception:
                pass
        # Comma-separated or space-separated fallback
        if "," in s and not s.startswith("-"):
            return [x.strip().strip("'\"") for x in s.split(",") if x.strip()]
        return [x.strip().strip("'\"") for x in s.split() if x.strip()]
    elif isinstance(val, (list, tuple)):
        result = []
        for item in val:
            if isinstance(item, str):
                item_s = item.strip()
                if item_s.startswith("[") and item_s.endswith("]"):
                    try:
                        parsed = json.loads(item_s)
                        if isinstance(parsed, list):
                            result.extend([str(x).strip().strip("'\"") for x in parsed if str(x).strip()])
                            continue
                    except Exception:
                        pass
                cleaned = item_s.strip("'\"")
                if cleaned:
                    result.append(cleaned)
            elif item is not None:
                result.append(str(item))
        return result
    return [str(val).strip().strip("'\"")]


def _parse_scale_filter(ffmpeg_cmd_str: str) -> Tuple[Optional[int], Optional[int]]:
    """
    Extracts target (width, height) integers from -vf scale filter in ffmpeg_cmd_str.
    Supports forms like:
      - scale=-2:1080
      - scale=1080:-2
      - scale=1920:1080
      - scale=w=1280:h=720
      - scale=h=720:w=1280
      - scale=1280:720:force_original_aspect_ratio=decrease
    Returns (target_w, target_h). Non-integer expressions (e.g. iw/2) return None.
    """
    if not ffmpeg_cmd_str:
        return None, None
    match = re.search(r'\bscale=([^,; \t"\']+)', ffmpeg_cmd_str)
    if not match:
        return None, None
    raw = match.group(1).strip()
    parts = raw.split(":")
    w: Optional[int] = None
    h: Optional[int] = None
    pos = 0
    for part in parts:
        part = part.strip()
        if "=" in part:
            k, v = part.split("=", 1)
            k = k.strip().lower()
            v = v.strip()
            try:
                if k == "w":
                    w = int(v)
                elif k == "h":
                    h = int(v)
            except ValueError:
                pass
        else:
            try:
                val = int(part)
                if pos == 0:
                    w = val
                elif pos == 1:
                    h = val
            except ValueError:
                pass
            pos += 1
    return w, h


def _parse_speed_factor(ffmpeg_cmd_str: str) -> Optional[float]:
    """
    Extracts the duration multiplier from setpts or atempo in ffmpeg_cmd_str.
    For setpts=K*PTS or setpts=PTS*K: duration is multiplied by K (e.g. 2.0*PTS -> 2x longer).
    For setpts=PTS/K: duration is divided by K (e.g. PTS/2 -> 0.5x duration).
    For setpts=1/K*PTS: duration is 1/K.
    For atempo=K: audio speed is K, so duration is multiplied by 1/K (e.g. atempo=0.5 -> 2x duration).
    Returns expected duration multiplier (e.g. 2.0 for 2x slow-down, 0.5 for 2x speed-up).
    """
    if not ffmpeg_cmd_str:
        return None

    # Check setpts in video filters first
    # 1. setpts=2.0*PTS or setpts=(2.0)*PTS or setpts=PTS*2.0
    m = re.search(r'setpts=(?:\(?([0-9.]+)\)?\*PTS|PTS\*(?:\(?([0-9.]+)\)?))', ffmpeg_cmd_str, re.IGNORECASE)
    if m:
        try:
            val = float(m.group(1) or m.group(2))
            if val > 0:
                return val
        except ValueError:
            pass

    # 2. setpts=PTS/2.0 or setpts=PTS/(2.0)
    m = re.search(r'setpts=PTS/(?:\(?([0-9.]+)\)?)', ffmpeg_cmd_str, re.IGNORECASE)
    if m:
        try:
            val = float(m.group(1))
            if val > 0:
                return 1.0 / val
        except ValueError:
            pass

    # 3. setpts=1/2*PTS or setpts=(1/2)*PTS
    m = re.search(r'setpts=\(?([0-9.]+)/([0-9.]+)\)?\*PTS', ffmpeg_cmd_str, re.IGNORECASE)
    if m:
        try:
            num = float(m.group(1))
            den = float(m.group(2))
            if den > 0:
                return num / den
        except ValueError:
            pass

    # Check atempo in audio filters
    atempo_matches = re.findall(r'atempo=([0-9.]+)', ffmpeg_cmd_str, re.IGNORECASE)
    if atempo_matches:
        try:
            total_tempo = 1.0
            for a in atempo_matches:
                f_a = float(a)
                if f_a > 0:
                    total_tempo *= f_a
            if total_tempo > 0:
                return 1.0 / total_tempo
        except ValueError:
            pass

    return None


def validate_output(
    input_meta: Optional[MediaMetadata],
    output_meta: Optional[MediaMetadata],
    ffmpeg_cmd_str: str,
    plan_title: str
) -> Tuple[bool, str, List[str]]:
    """
    Validates output quality gates after FFmpeg execution.
    Implements 4 checks from design.md D3:
      1. Resolution check (±2px tolerance against scale filter in ffmpeg_cmd_str)
      2. Duration check (within [0.98, 1.02] ratio, or [0.90, 1.10] against speed-adjusted duration, skipped if -ss/-to/-t/trim present)
      3. Size inflation warning (warns if output > input for optimize/compress/reduce plans)
      4. Stream presence check (fails if input has video+audio but output has only video without -an)

    Returns (is_valid, error_description, warnings_list).
    """
    warnings: List[str] = []

    if output_meta is None:
        return False, "Output media probe failed or file is empty", warnings

    # 1. Resolution check
    scale_w, scale_h = _parse_scale_filter(ffmpeg_cmd_str)
    if scale_w is not None or scale_h is not None:
        if not output_meta.has_video or output_meta.width is None or output_meta.height is None:
            return False, "Resolution check failed: scale was specified but output has no video stream or dimensions", warnings

        out_w = output_meta.width
        out_h = output_meta.height

        # Both width and height are fixed positive targets
        if scale_w is not None and scale_w > 0 and scale_h is not None and scale_h > 0:
            if abs(out_w - scale_w) > 2 or abs(out_h - scale_h) > 2:
                return False, f"Resolution mismatch: expected {scale_w}x{scale_h}, got {out_w}x{out_h}", warnings

        # Height is fixed positive, width is proportional (e.g. scale=-2:1080)
        elif scale_h is not None and scale_h > 0 and (scale_w is None or scale_w < 0):
            if abs(out_h - scale_h) > 2:
                return False, f"Resolution mismatch: expected height {scale_h}, got {out_h} ({out_w}x{out_h})", warnings
            if input_meta and input_meta.width and input_meta.height and input_meta.height > 0:
                expected_w = input_meta.width * scale_h / input_meta.height
                if scale_w == -2:
                    expected_w = round(expected_w / 2.0) * 2
                else:
                    expected_w = round(expected_w)
                if abs(out_w - expected_w) > 2:
                    return False, f"Resolution mismatch: expected width ~{int(expected_w)}, got {out_w} ({out_w}x{out_h})", warnings

        # Width is fixed positive, height is proportional (e.g. scale=1080:-2)
        elif scale_w is not None and scale_w > 0 and (scale_h is None or scale_h < 0):
            if abs(out_w - scale_w) > 2:
                return False, f"Resolution mismatch: expected width {scale_w}, got {out_w} ({out_w}x{out_h})", warnings
            if input_meta and input_meta.width and input_meta.height and input_meta.width > 0:
                expected_h = input_meta.height * scale_w / input_meta.width
                if scale_h == -2:
                    expected_h = round(expected_h / 2.0) * 2
                else:
                    expected_h = round(expected_h)
                if abs(out_h - expected_h) > 2:
                    return False, f"Resolution mismatch: expected height ~{int(expected_h)}, got {out_h} ({out_w}x{out_h})", warnings

    # 2. Duration check
    has_trim = bool(
        re.search(r'(?:^|\s)-(?:ss|to|t)\b', ffmpeg_cmd_str)
        or re.search(r'\b(?:a)?trim\b', ffmpeg_cmd_str)
        or "-shortest" in ffmpeg_cmd_str
    )
    speed_mult = _parse_speed_factor(ffmpeg_cmd_str)
    has_speed_intent = bool(
        speed_mult is not None
        or re.search(r'\b(?:setpts|atempo|minterpolate)\b', ffmpeg_cmd_str)
        or any(kw in (plan_title or "").lower() for kw in ("slow", "speed", "timelapse", "hyperlapse", "fast forward", "tempo"))
    )
    has_loop_intent = bool(
        re.search(r'\b(?:a)?loop\b', ffmpeg_cmd_str)
        or any(kw in (plan_title or "").lower() for kw in ("loop", "repeat"))
    )

    if input_meta and input_meta.duration_seconds and input_meta.duration_seconds > 0:
        out_dur = output_meta.duration_seconds if output_meta.duration_seconds else 0.0
        if speed_mult is not None and speed_mult > 0:
            expected_dur = input_meta.duration_seconds * speed_mult
            ratio = out_dur / expected_dur if expected_dur > 0 else 1.0
            if ratio < 0.90 or ratio > 1.10:
                return False, f"Duration integrity check failed: output duration ({out_dur:.2f}s) differs from expected speed-adjusted duration ({expected_dur:.2f}s) - ratio {ratio:.2f} outside [0.90, 1.10]", warnings
        elif not has_trim and not has_speed_intent and not has_loop_intent:
            ratio = out_dur / input_meta.duration_seconds
            if ratio < 0.98 or ratio > 1.02:
                return False, f"Duration integrity check failed: output duration ({out_dur:.2f}s) differs from input ({input_meta.duration_seconds:.2f}s) - ratio {ratio:.2f} outside [0.98, 1.02]", warnings

    # 3. Size inflation warning
    plan_title_lower = (plan_title or "").lower()
    if any(kw in plan_title_lower for kw in ("optimize", "compress", "reduce")):
        if (
            input_meta
            and input_meta.file_size_bytes
            and output_meta
            and output_meta.file_size_bytes
            and output_meta.file_size_bytes > input_meta.file_size_bytes
        ):
            warnings.append(
                f"Output file size ({format_bytes(output_meta.file_size_bytes)}) is larger than input ({format_bytes(input_meta.file_size_bytes)}) despite optimization plan."
            )

    # 4. Stream presence check
    if (
        input_meta
        and input_meta.has_video
        and input_meta.has_audio
        and output_meta
        and output_meta.has_video
        and not output_meta.has_audio
    ):
        has_an = bool(re.search(r'(?:^|\s)-an\b', ffmpeg_cmd_str))
        if not has_an:
            return False, "Stream loss detected: input contained both video and audio, but output contains only video without -an flag", warnings

    return True, "", warnings


def check_disk_space(sandbox_dir: Path, estimated_output_size: int) -> tuple:
    """Pre-flight check for available disk space before FFmpeg execution."""
    try:
        target = sandbox_dir if sandbox_dir.exists() else sandbox_dir.parent
        free = shutil.disk_usage(target).free
        required = estimated_output_size * 2
        if free >= required:
            return (True, f"Sufficient disk space: {free // (1024*1024)} MB free, {required // (1024*1024)} MB needed.")
        else:
            return (False, f"Not enough disk space. {free // (1024*1024)} MB free, need approximately {required // (1024*1024)} MB. Please delete unused files via /files.")
    except Exception as e:
        logger.warning(f"Disk space check failed: {e}")
        return (True, "Could not verify disk space, proceeding anyway.")


def check_available_ram(min_mb: int = 300) -> tuple:
    """Pre-flight check for available RAM before memory-intensive FFmpeg operations."""
    available_mb = 0
    try:
        with open("/proc/meminfo", "r") as f:
            for line in f:
                if line.startswith("MemAvailable:"):
                    available_kb = int(line.split()[1])
                    available_mb = available_kb // 1024
                    break
    except FileNotFoundError:
        try:
            import psutil
            available_mb = psutil.virtual_memory().available // (1024 * 1024)
        except ImportError:
            return (True, 0)
    except Exception:
        return (True, 0)

    sufficient = available_mb >= min_mb
    return (sufficient, available_mb)


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
        on_process_started: Optional[Callable[[Any], None]] = None
    ) -> Dict[str, Any]:
        """
        Validates arguments, enforces Snapdragon 636 / mobile memory guardrails,
        and executes single or compound FFmpeg operations writing strictly to workplace_dir.
        """
        # Normalize into a list of operations
        op_list: List[Dict[str, Any]] = []
        if operations:
            if isinstance(operations, str):
                try:
                    parsed_ops = json.loads(operations)
                    if isinstance(parsed_ops, list):
                        op_list = parsed_ops
                except Exception:
                    pass
            elif isinstance(operations, list):
                op_list = operations

        if not op_list:
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
        executed_commands: List[str] = []
        total_ops = len(op_list)

        # Pre-flight resource checks
        for op in op_list:
            # Estimate output size based on the actual target input for this op
            op_inputs = _normalize_string_or_list(op.get("inputs")) or ["v1"]
            resolved_op_inputs = [self._resolve_input_path(str(i)) for i in op_inputs]
            target_in_path = resolved_op_inputs[0] if (resolved_op_inputs and resolved_op_inputs[0].exists()) else self.input_path
            input_size = target_in_path.stat().st_size if target_in_path.exists() else 0
            op_vf_check = op.get("vf_filter", "") or ""
            op_af_check = op.get("af_filter", "") or ""
            is_audio_extract = op.get("extract_audio_only", False) or (op.get("video_codec") == "none")

            estimated_output = int(input_size * 0.1) if is_audio_extract else int(input_size * 1.5)
            op_speed_mult = _parse_speed_factor(f"{op_vf_check} {op_af_check}")
            if op_speed_mult and op_speed_mult > 1.0:
                estimated_output = int(estimated_output * op_speed_mult)

            disk_ok, disk_msg = check_disk_space(self.workplace_dir, estimated_output)
            if not disk_ok:
                return {"status": "error", "error": disk_msg}

            # RAM check for memory-intensive operations
            needs_ram_check = (
                "reverse" in op_vf_check or 
                "areverse" in op_af_check or
                (op.get("inputs") and len(_normalize_string_or_list(op.get("inputs"))) > 2)
            )
            if needs_ram_check:
                ram_ok, available_mb = check_available_ram(min_mb=300)
                if not ram_ok:
                    return {
                        "status": "error",
                        "error": f"Insufficient RAM for this operation. Available: {available_mb} MB, minimum required: 300 MB. Please close other apps and try again."
                    }

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
            op_inputs = _normalize_string_or_list(op.get("inputs")) or ["v1"]
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

            # Orientation-Aware Scaling & Quality Floor Protection
            if op_vf and self.meta and self.meta.width and self.meta.height:
                is_portrait = self.meta.width < self.meta.height
                src_w, src_h = self.meta.width, self.meta.height
                explicit_low_res = any(kw in plan_title.lower() for kw in ("480", "360", "sd", "lowest"))

                # 1. Correct inverted portrait scale (e.g. scale=-2:1080 -> scale=1080:-2)
                if is_portrait and "scale=-2:1080" in op_vf:
                    op_vf = op_vf.replace("scale=-2:1080", "scale=1080:-2")

                # 2. Prevent accidental / hallucinated downscaling to 480p or 360p unless explicitly requested
                if not explicit_low_res and ("480" in op_vf or "360" in op_vf):
                    import re
                    # If source exceeds 1080p, cap at 1080p; otherwise keep native (remove downscale)
                    if max(src_w, src_h) > 1080 and min(src_w, src_h) > 1080:
                        safe_scale = "scale=1080:-2" if is_portrait else "scale=-2:1080"
                    else:
                        safe_scale = ""
                    op_vf = re.sub(r'scale=[^,:]+:(?:480|360)', safe_scale, op_vf)
                    op_vf = re.sub(r'scale=(?:480|360):[^,:]+', safe_scale, op_vf)
                    op_vf = re.sub(r',+', ',', op_vf).strip(",").strip()

            # Assemble FFmpeg Command
            cmd = []
            for p in resolved_inputs:
                cmd.extend(["-i", str(p)])

            # Stream Mapping
            raw_sm = op.get("stream_mapping")
            sm_list = _normalize_string_or_list(raw_sm)
            if sm_list:
                i = 0
                while i < len(sm_list):
                    token = sm_list[i].strip().strip("'\"")
                    if token == "-map":
                        if i + 1 < len(sm_list):
                            clean_target = sm_list[i + 1].strip().strip("'\"")
                            if clean_target and clean_target != "-map":
                                cmd.extend(["-map", clean_target])
                            i += 2
                        else:
                            i += 1
                    elif token:
                        cmd.extend(["-map", token])
                        i += 1
            elif len(resolved_inputs) == 2:
                # Auto stream mapping when combining 2 inputs (e.g. video + audio)
                try:
                    p0_meta = await probe_media(resolved_inputs[0])
                    p1_meta = await probe_media(resolved_inputs[1])
                    if p0_meta and p0_meta.has_video and p1_meta and p1_meta.has_audio:
                        cmd.extend(["-map", "0:v:0", "-map", "1:a:0"])
                    elif p1_meta and p1_meta.has_video and p0_meta and p0_meta.has_audio:
                        cmd.extend(["-map", "1:v:0", "-map", "0:a:0"])
                except Exception:
                    pass

            # Video Stream Handling
            if op_extract_audio:
                cmd.append("-vn")
            else:
                vcodec = op.get("video_codec", "libx264")
                if len(resolved_inputs) == 2 and not (op_vf and op_vf.strip()):
                    # When combining new audio into an already-encoded video without extra filters, stream copy video
                    vcodec = "copy"
                elif op_mute_video and vcodec == "libx264" and (not op_vf or not op_vf.strip()):
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
                    clean_af = op_af.strip()
                    # Sanitize hallucinated arepeat filter -> aloop
                    if "arepeat" in clean_af:
                        import re
                        m = re.search(r'arepeat[=:][^,;]*?([0-9-]+)', clean_af)
                        loop_count = m.group(1) if m else "-1"
                        pattern = r'arepeat(?:=[^\',;]*(?:\'[^\']*\')*[^\',;]*|:[^\',;]*(?:\'[^\']*\')*[^\',;]*)?'
                        clean_af = re.sub(pattern, f'aloop=loop={loop_count}:size=2e+09', clean_af)
                    cmd.extend(["-af", clean_af])
                    if acodec == "copy":
                        acodec = "aac"
                if acodec == "copy":
                    cmd.extend(["-c:a", "copy"])
                elif acodec == "aac":
                    cmd.extend(["-c:a", "aac", "-b:a", "128k", "-ac", "2"])
                elif acodec in ("mp3", "libmp3lame"):
                    cmd.extend(["-c:a", "libmp3lame", "-b:a", "192k"])

            # Extra safe arguments (e.g. -shortest, -ss, -to)
            has_shortest = False
            raw_ea = op.get("extra_args")
            ea_list = _normalize_string_or_list(raw_ea)
            for ea in ea_list:
                ea_clean = ea.strip().strip("'\"")
                if ea_clean:
                    cmd.append(ea_clean)
                    if ea_clean == "-shortest":
                        has_shortest = True

            if len(resolved_inputs) >= 2 and not has_shortest and not op_extract_audio:
                cmd.append("-shortest")

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

            # Wrap progress callback with step context
            disp_out_name = clean_display_name(safe_filename)
            async def op_progress(percent: float, speed: str, eta: Optional[int], fps: float, **kwargs):
                if progress_callback:
                    try:
                        await progress_callback(percent, speed, eta, fps, step_label=f"({op_idx}/{total_ops}) {disp_out_name}", **kwargs)
                    except TypeError:
                        try:
                            await progress_callback(percent, speed, eta, fps, **kwargs)
                        except TypeError:
                            await progress_callback(percent, speed, eta, fps)
                    except Exception as pe:
                        logger.warning(f"Error in op_progress callback: {pe}")

            cmd_str = " ".join(cmd)
            executed_commands.append(cmd_str)

            # Adjust estimated duration by speed factor if slowing down or speeding up
            cmd_speed_mult = _parse_speed_factor(cmd_str)
            if cmd_speed_mult and cmd_speed_mult > 0:
                est_duration = est_duration * cmd_speed_mult

            try:
                await run_ffmpeg_with_progress(
                    cmd=cmd,
                    total_duration_seconds=est_duration,
                    progress_callback=op_progress if progress_callback else None,
                    on_process_started=on_process_started
                )
            except Exception as e:
                logger.error(f"Sandbox FFmpeg execution failed for {safe_filename}: {e}")
                return {
                    "status": "error",
                    "error": f"FFmpeg execution failed on {safe_filename}: {str(e)}",
                    "executed_commands": executed_commands,
                    "ffmpeg_command": cmd_str
                }

            if not out_path.exists() or out_path.stat().st_size == 0:
                return {
                    "status": "error",
                    "error": f"Operation finished but output file '{safe_filename}' was not generated or is empty.",
                    "executed_commands": executed_commands,
                    "ffmpeg_command": cmd_str
                }

            out_meta = await probe_media(out_path)
            ffprobe_dict = {
                "filename": safe_filename,
                "has_video": out_meta.has_video if out_meta else False,
                "resolution": out_meta.resolution_label if (out_meta and out_meta.has_video) else ("Audio Track" if (out_meta and out_meta.has_audio) else "Unknown"),
                "width": out_meta.width if out_meta else None,
                "height": out_meta.height if out_meta else None,
                "video_codec": out_meta.video_codec if out_meta else None,
                "video_bitrate_kbps": out_meta.video_bitrate_kbps if out_meta else None,
                "fps": round(out_meta.fps, 2) if (out_meta and out_meta.fps) else None,
                "has_audio": out_meta.has_audio if out_meta else False,
                "audio_codec": out_meta.audio_codec if out_meta else None,
                "audio_bitrate_kbps": out_meta.audio_bitrate_kbps if out_meta else None,
                "overall_bitrate_kbps": out_meta.overall_bitrate_kbps if out_meta else None,
                "duration_seconds": round(out_meta.duration_seconds, 2) if out_meta else 0.0,
                "formatted_duration": format_duration(out_meta.duration_seconds) if out_meta else "Unknown",
                "size_bytes": out_path.stat().st_size,
                "formatted_size": format_bytes(out_path.stat().st_size)
            }

            # Output quality gate validation (validate against actual input for this operation)
            actual_in_meta = None
            if resolved_inputs:
                try:
                    res_p = resolved_inputs[0].resolve()
                    if self.input_path and res_p == self.input_path.resolve():
                        actual_in_meta = self.meta
                    if not actual_in_meta:
                        for k, asset_info in self.assets.items():
                            if isinstance(asset_info, dict) and asset_info.get("filepath"):
                                if Path(asset_info["filepath"]).resolve() == res_p:
                                    actual_in_meta = asset_info.get("meta")
                                    break
                except Exception:
                    pass
                if not actual_in_meta and resolved_inputs[0].exists():
                    try:
                        actual_in_meta = await probe_media(resolved_inputs[0])
                    except Exception:
                        pass
            if not actual_in_meta:
                actual_in_meta = self.meta

            warnings: List[str] = []
            if actual_in_meta and out_meta:
                is_valid, error_msg, warnings = validate_output(
                    input_meta=actual_in_meta,
                    output_meta=out_meta,
                    ffmpeg_cmd_str=cmd_str,
                    plan_title=plan_title
                )
                if not is_valid:
                    # Retry once
                    logger.warning(f"Quality gate failed: {error_msg}. Retrying...")
                    try:
                        if out_path.exists():
                            out_path.unlink()
                        await run_ffmpeg_with_progress(
                            cmd=cmd,
                            total_duration_seconds=est_duration,
                            progress_callback=op_progress if progress_callback else None,
                            on_process_started=on_process_started
                        )
                    except Exception as retry_err:
                        return {
                            "status": "error",
                            "error": f"Quality gate failed: {error_msg}. Retry also failed: {str(retry_err)}",
                            "executed_commands": executed_commands
                        }
                    # Re-verify after retry
                    if not out_path.exists() or out_path.stat().st_size == 0:
                        return {
                            "status": "error",
                            "error": f"Quality gate failed: {error_msg}. Retry produced no output.",
                            "executed_commands": executed_commands
                        }
                    retry_meta = await probe_media(out_path)
                    is_valid2, error_msg2, warnings2 = validate_output(
                        input_meta=actual_in_meta,
                        output_meta=retry_meta,
                        ffmpeg_cmd_str=cmd_str,
                        plan_title=plan_title
                    )
                    if not is_valid2:
                        return {
                            "status": "error",
                            "error": f"Quality gate failed after retry: {error_msg2}",
                            "executed_commands": executed_commands
                        }
                    out_meta = retry_meta
                    warnings = warnings2
                    # Rebuild ffprobe_dict with retry output
                    ffprobe_dict = {
                        "filename": safe_filename,
                        "has_video": out_meta.has_video if out_meta else False,
                        "resolution": out_meta.resolution_label if (out_meta and out_meta.has_video) else ("Audio Track" if (out_meta and out_meta.has_audio) else "Unknown"),
                        "width": out_meta.width if out_meta else None,
                        "height": out_meta.height if out_meta else None,
                        "video_codec": out_meta.video_codec if out_meta else None,
                        "video_bitrate_kbps": out_meta.video_bitrate_kbps if out_meta else None,
                        "fps": round(out_meta.fps, 2) if (out_meta and out_meta.fps) else None,
                        "has_audio": out_meta.has_audio if out_meta else False,
                        "audio_codec": out_meta.audio_codec if out_meta else None,
                        "audio_bitrate_kbps": out_meta.audio_bitrate_kbps if out_meta else None,
                        "overall_bitrate_kbps": out_meta.overall_bitrate_kbps if out_meta else None,
                        "duration_seconds": round(out_meta.duration_seconds, 2) if out_meta else 0.0,
                        "formatted_duration": format_duration(out_meta.duration_seconds) if out_meta else "Unknown",
                        "size_bytes": out_path.stat().st_size,
                        "formatted_size": format_bytes(out_path.stat().st_size)
                    }

            deliverables.append({
                "filename": safe_filename,
                "path": str(out_path),
                "size_bytes": out_path.stat().st_size,
                "formatted_size": format_bytes(out_path.stat().st_size),
                "resolution": ffprobe_dict["resolution"],
                "duration": ffprobe_dict["formatted_duration"],
                "ffmpeg_command": cmd_str,
                "ffprobe": ffprobe_dict,
                "warnings": warnings
            })

        if not deliverables:
            return {
                "status": "error",
                "error": "No deliverables produced by operations.",
                "executed_commands": executed_commands
            }

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
            "duration": first["duration"],
            "ffprobe": first.get("ffprobe", {}),
            "warnings": first.get("warnings", []),
            "executed_commands": executed_commands,
            "ffmpeg_command": executed_commands[0] if executed_commands else ""
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

    async def run_list_workspace_files(self) -> Dict[str, Any]:
        """Lists all files in the workspace sandbox directory."""
        import mimetypes
        files = []
        if self.workplace_dir.exists():
            for f in self.workplace_dir.iterdir():
                if f.is_file():
                    mime_type = mimetypes.guess_type(str(f))[0] or "application/octet-stream"
                    media_type = "video" if mime_type.startswith("video") else ("audio" if mime_type.startswith("audio") else "other")
                    files.append({
                        "filename": f.name,
                        "size_bytes": f.stat().st_size,
                        "media_type": media_type
                    })
        return {"status": "success", "files": files}

