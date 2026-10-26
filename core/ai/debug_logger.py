"""
AI Session Debugger & Diagnostic Logger
Persists complete conversation turns, media before/after ffprobe comparisons,
and FFmpeg execution commands in storage/debug-ai for bug tracking and quality analysis.
"""

import json
import time
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Any, List, Optional
from core.probe import MediaMetadata, format_bytes, format_duration
from config.settings import settings

logger = logging.getLogger(__name__)


def serialize_media_metadata(meta: Optional[MediaMetadata]) -> Optional[Dict[str, Any]]:
    """Converts a MediaMetadata object into a JSON-serializable dictionary."""
    if not meta:
        return None
    return {
        "filename": getattr(meta, "file_name", "unknown"),
        "size_bytes": getattr(meta, "file_size_bytes", 0),
        "formatted_size": format_bytes(getattr(meta, "file_size_bytes", 0)),
        "duration_seconds": round(getattr(meta, "duration_seconds", 0.0), 2),
        "formatted_duration": format_duration(getattr(meta, "duration_seconds", 0.0)),
        "container": getattr(meta, "container_format", "unknown"),
        "has_video": getattr(meta, "has_video", False),
        "resolution": getattr(meta, "resolution_label", "Unknown"),
        "dimensions": f"{meta.width}x{meta.height}" if (getattr(meta, "width", None) and getattr(meta, "height", None)) else None,
        "video_codec": getattr(meta, "video_codec", None),
        "video_bitrate_kbps": getattr(meta, "video_bitrate_kbps", None),
        "fps": round(meta.fps, 2) if getattr(meta, "fps", None) else None,
        "has_audio": getattr(meta, "has_audio", False),
        "audio_codec": getattr(meta, "audio_codec", None),
        "audio_bitrate_kbps": getattr(meta, "audio_bitrate_kbps", None),
        "audio_channels": getattr(meta, "audio_channels", None),
        "audio_sample_rate": getattr(meta, "audio_sample_rate", None),
        "overall_bitrate_kbps": getattr(meta, "overall_bitrate_kbps", None)
    }


def serialize_session_assets(assets: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    """Converts session assets dictionary into JSON-serializable asset metadata."""
    if not assets:
        return {}
    out: Dict[str, Any] = {}
    for aid, a in assets.items():
        out[aid] = {
            "filename": a.get("filename"),
            "media_type": a.get("media_type", "media"),
            "path": str(a.get("path", "")),
            "meta": serialize_media_metadata(a.get("meta"))
        }
    return out


def compute_media_comparison(before_meta: Optional[MediaMetadata], deliverable: Dict[str, Any]) -> Dict[str, Any]:
    """Computes before-vs-after delta metrics between original source and newly generated deliverable."""
    if not before_meta:
        return {}

    before_size = getattr(before_meta, "file_size_bytes", 0)
    after_size = deliverable.get("size_bytes", 0)
    reduction_pct = 0.0
    if before_size > 0 and after_size > 0:
        reduction_pct = round((1 - (after_size / before_size)) * 100, 2)

    after_probe = deliverable.get("ffprobe") or {}
    after_res = after_probe.get("resolution") or deliverable.get("resolution", "Unknown")
    after_vcodec = after_probe.get("video_codec")
    after_acodec = after_probe.get("audio_codec")

    return {
        "size_before": format_bytes(before_size),
        "size_after": format_bytes(after_size),
        "size_reduction_percent": reduction_pct,
        "resolution_before": before_meta.resolution_label,
        "resolution_after": after_res,
        "video_codec_before": before_meta.video_codec,
        "video_codec_after": after_vcodec,
        "audio_codec_before": before_meta.audio_codec,
        "audio_codec_after": after_acodec,
        "duration_before": format_duration(before_meta.duration_seconds),
        "duration_after": after_probe.get("formatted_duration") or deliverable.get("duration", "Unknown")
    }


class AIDebugLogger:
    """Manages recording of AI diagnostic and conversational sessions to disk."""

    def __init__(self, debug_dir: Optional[Path] = None):
        self.debug_dir = debug_dir or getattr(settings, "AI_DEBUG_DIR", settings.BASE_DIR / "storage/debug-ai")
        self._ensure_root_dir()

    def _ensure_root_dir(self) -> None:
        """Ensures the root debug directory exists."""
        try:
            self.debug_dir.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"Could not create debug root dir {self.debug_dir}: {e}")

    def get_session_dir(self, user_id: int, task_id: str) -> Path:
        """Returns the isolated debug directory for a specific session."""
        session_dir = self.debug_dir / f"{user_id}_{task_id}"
        session_dir.mkdir(parents=True, exist_ok=True)
        return session_dir

    def log_turn(
        self,
        user_id: int,
        task_id: str,
        turn_index: int,
        user_input: str,
        model: str,
        media_before: Optional[MediaMetadata],
        assets_before: Optional[Dict[str, Any]],
        system_prompt: str,
        injected_skills: str,
        assistant_raw: Optional[Dict[str, Any]],
        tool_calls: Optional[List[Dict[str, Any]]],
        tool_results: Optional[List[Dict[str, Any]]],
        primary_result: Optional[Dict[str, Any]],
        final_reply: str,
        elapsed_seconds: float
    ) -> Optional[Path]:
        """
        Records a complete turn of AI interaction into JSON and Markdown logs.
        Never raises exceptions to prevent interrupting the main bot workflow.
        """
        try:
            session_dir = self.get_session_dir(user_id, task_id)
            now_iso = datetime.now(timezone.utc).isoformat()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            before_serialized = serialize_media_metadata(media_before)
            assets_serialized = serialize_session_assets(assets_before)

            deliverables = (primary_result.get("deliverables") if primary_result else []) or []
            comparison = {}
            if deliverables and media_before:
                comparison = compute_media_comparison(media_before, deliverables[0])

            # Extract executed commands from deliverables or primary result
            executed_commands = []
            if primary_result:
                if primary_result.get("executed_commands"):
                    executed_commands.extend(primary_result.get("executed_commands"))
                elif primary_result.get("ffmpeg_command"):
                    executed_commands.append(primary_result.get("ffmpeg_command"))

            for d in deliverables:
                if d.get("ffmpeg_command") and d.get("ffmpeg_command") not in executed_commands:
                    executed_commands.append(d.get("ffmpeg_command"))

            turn_data = {
                "turn_index": turn_index,
                "timestamp": now_iso,
                "user_id": user_id,
                "task_id": task_id,
                "user_input": user_input,
                "active_model": model,
                "elapsed_seconds": round(elapsed_seconds, 2),
                "media_before": {
                    "primary": before_serialized,
                    "session_assets": assets_serialized
                },
                "ai_decision": {
                    "injected_skills_snapshot": injected_skills,
                    "raw_assistant_reply": assistant_raw,
                    "tool_calls": tool_calls or []
                },
                "execution": {
                    "status": primary_result.get("status") if primary_result else "none",
                    "plan_title": primary_result.get("plan_title") if primary_result else None,
                    "executed_commands": executed_commands,
                    "error": primary_result.get("error") if primary_result else None
                },
                "media_after": {
                    "deliverables": deliverables,
                    "comparison": comparison
                },
                "final_reply": final_reply
            }

            # 1. Save detailed JSON turn file
            json_file = session_dir / f"turn_{turn_index:03d}.json"
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(turn_data, f, indent=2, default=str)

            # 2. Append/update human-readable session_log.md
            md_file = session_dir / "session_log.md"
            self._append_markdown_log(
                md_file=md_file,
                turn_index=turn_index,
                now_str=now_str,
                user_id=user_id,
                task_id=task_id,
                model=model,
                user_input=user_input,
                before_serialized=before_serialized,
                assets_serialized=assets_serialized,
                tool_calls=tool_calls,
                executed_commands=executed_commands,
                deliverables=deliverables,
                comparison=comparison,
                final_reply=final_reply,
                elapsed_seconds=elapsed_seconds,
                primary_result=primary_result
            )

            # 3. Append to global sessions.jsonl index
            self._append_global_index(
                now_iso=now_iso,
                user_id=user_id,
                task_id=task_id,
                turn_index=turn_index,
                user_input=user_input,
                model=model,
                primary_result=primary_result,
                comparison=comparison,
                elapsed_seconds=elapsed_seconds
            )

            return json_file

        except Exception as e:
            logger.warning(f"AIDebugLogger error during log_turn: {e}", exc_info=True)
            return None

    def _append_markdown_log(
        self,
        md_file: Path,
        turn_index: int,
        now_str: str,
        user_id: int,
        task_id: str,
        model: str,
        user_input: str,
        before_serialized: Optional[Dict[str, Any]],
        assets_serialized: Dict[str, Any],
        tool_calls: Optional[List[Dict[str, Any]]],
        executed_commands: List[str],
        deliverables: List[Dict[str, Any]],
        comparison: Dict[str, Any],
        final_reply: str,
        elapsed_seconds: float,
        primary_result: Optional[Dict[str, Any]]
    ) -> None:
        """Appends a clear, structured markdown summary for human inspection."""
        is_new_file = not md_file.exists() or md_file.stat().st_size == 0
        lines = []

        if is_new_file:
            init_file = before_serialized.get("filename", "Unknown") if before_serialized else "None"
            lines.append(f"# AI Diagnostic Session: {user_id}_{task_id}\n")
            lines.append(f"- **Session Initialized:** {now_str}")
            lines.append(f"- **Initial Source File:** `{init_file}`")
            lines.append(f"- **Initial Model:** `{model}`\n")
            lines.append("---\n")

        lines.append(f"## Turn {turn_index} ({now_str})\n")
        lines.append(f"- **User Input:** \"{user_input}\"")
        lines.append(f"- **Active Model:** `{model}` (Turn duration: {elapsed_seconds:.2f}s)\n")

        # Media Before
        lines.append("### 1. Media Before:")
        if before_serialized:
            lines.append(
                f"- **Target File:** `{before_serialized.get('filename')}` | "
                f"**Resolution:** `{before_serialized.get('resolution')}` | "
                f"**Size:** `{before_serialized.get('formatted_size')}` | "
                f"**Duration:** `{before_serialized.get('formatted_duration')}`"
            )
            lines.append(
                f"- **Codecs:** Video: `{before_serialized.get('video_codec') or 'None'}` (~{before_serialized.get('video_bitrate_kbps') or 'N/A'} kbps) | "
                f"Audio: `{before_serialized.get('audio_codec') or 'None'}` (~{before_serialized.get('audio_bitrate_kbps') or 'N/A'} kbps)"
            )
        else:
            lines.append("- *No primary media metadata available.*")

        if assets_serialized:
            asset_items = [f"`[{aid}]` {a.get('filename')} ({a.get('media_type')})" for aid, a in assets_serialized.items()]
            lines.append(f"- **Session Assets:** {', '.join(asset_items)}")
        lines.append("")

        # AI Decision & Tool Calls
        lines.append("### 2. AI Decision & Tool Execution:")
        if tool_calls:
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "unknown")
                args = fn.get("arguments", "{}")
                lines.append(f"- **Tool Called:** `{name}`")
                lines.append(f"- **Arguments:**\n```json\n{args}\n```")
        else:
            lines.append("- **Tool Called:** None (Direct conversational answer)")

        if executed_commands:
            lines.append("- **FFmpeg Command(s) Executed:**")
            for c in executed_commands:
                lines.append(f"```bash\n{c}\n```")

        status_str = primary_result.get("status", "none") if primary_result else "none"
        lines.append(f"- **Execution Status:** `{status_str}`\n")

        # Media After
        lines.append("### 3. Media After (FFprobe Verified):")
        if deliverables:
            for idx, d in enumerate(deliverables, start=1):
                fprobe = d.get("ffprobe") or {}
                res = fprobe.get("resolution") or d.get("resolution", "Unknown")
                sz = fprobe.get("formatted_size") or d.get("formatted_size", "Unknown")
                dur = fprobe.get("formatted_duration") or d.get("duration", "Unknown")
                vcodec = fprobe.get("video_codec") or "None"
                acodec = fprobe.get("audio_codec") or "None"
                lines.append(f"- **Deliverable {idx}:** `{d.get('filename')}` | `{res}` | `{sz}` | `{dur}`")
                lines.append(f"  - **Codecs:** Video: `{vcodec}` | Audio: `{acodec}`")

            if comparison:
                lines.append(
                    f"- **Delta:** Size `{comparison.get('size_before')}` -> `{comparison.get('size_after')}` "
                    f"({comparison.get('size_reduction_percent', 0)}% change) | "
                    f"Resolution: `{comparison.get('resolution_before')}` -> `{comparison.get('resolution_after')}`"
                )
        else:
            lines.append("- *No output deliverables generated in this turn.*")
        lines.append("")

        # AI Response
        lines.append("### 4. AI Response to User:")
        lines.append(f"> {final_reply.strip()}\n")
        lines.append("---\n")

        with open(md_file, "a", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _append_global_index(
        self,
        now_iso: str,
        user_id: int,
        task_id: str,
        turn_index: int,
        user_input: str,
        model: str,
        primary_result: Optional[Dict[str, Any]],
        comparison: Dict[str, Any],
        elapsed_seconds: float
    ) -> None:
        """Appends an index entry to sessions.jsonl in the root debug directory."""
        index_file = self.debug_dir / "sessions.jsonl"
        summary = {
            "timestamp": now_iso,
            "user_id": user_id,
            "task_id": task_id,
            "turn": turn_index,
            "user_input": user_input[:100],
            "model": model,
            "status": primary_result.get("status") if primary_result else "none",
            "plan_title": primary_result.get("plan_title") if primary_result else None,
            "size_before": comparison.get("size_before"),
            "size_after": comparison.get("size_after"),
            "reduction_percent": comparison.get("size_reduction_percent"),
            "elapsed_seconds": round(elapsed_seconds, 2)
        }
        with open(index_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(summary, default=str) + "\n")

    def log_session_event(self, user_id: int, task_id: str, event_type: str, details: Dict[str, Any]) -> None:
        """Logs arbitrary session lifecycle events (e.g. asset uploaded, session exited)."""
        try:
            session_dir = self.get_session_dir(user_id, task_id)
            event_file = session_dir / "events.jsonl"
            record = {
                "timestamp": datetime.now(timezone.utc).isoformat(),
                "event_type": event_type,
                "user_id": user_id,
                "task_id": task_id,
                **details
            }
            with open(event_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, default=str) + "\n")
        except Exception as e:
            logger.warning(f"Could not record session event {event_type}: {e}")


# Global singleton debug logger instance
ai_debug_logger = AIDebugLogger()
