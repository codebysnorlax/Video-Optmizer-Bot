"""
Video & Audio Metadata Inspector (FFprobe Async Parser)
"""

import json
import asyncio
import os
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, Dict, Any


def format_bytes(size_bytes: int) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 ** 2:
        return f"{size_bytes / 1024:.1f} KB"
    elif size_bytes < 1024 ** 3:
        return f"{size_bytes / (1024 ** 2):.1f} MB"
    else:
        return f"{size_bytes / (1024 ** 3):.2f} GB"


def format_duration(seconds: float) -> str:
    """Format seconds into HH:MM:SS or MM:SS."""
    total_sec = int(seconds)
    hours = total_sec // 3600
    minutes = (total_sec % 3600) // 60
    secs = total_sec % 60
    if hours > 0:
        return f"{hours:02d}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


@dataclass
class MediaMetadata:
    file_path: Path
    file_name: str
    file_size_bytes: int
    duration_seconds: float
    container_format: str
    
    # Video details
    has_video: bool
    video_codec: Optional[str]
    width: Optional[int]
    height: Optional[int]
    fps: Optional[float]
    video_bitrate_kbps: Optional[int]
    
    # Audio details
    has_audio: bool
    audio_codec: Optional[str]
    audio_channels: Optional[int]
    audio_sample_rate: Optional[int]
    audio_bitrate_kbps: Optional[int]
    
    # Calculated metrics
    overall_bitrate_kbps: int
    is_high_bitrate: bool
    raw_info: Dict[str, Any]

    @property
    def resolution_label(self) -> str:
        if not self.width or not self.height:
            return "Audio Only" if self.has_audio else "Unknown"
        h = self.height
        w = self.width
        max_dim = max(w, h)
        if max_dim >= 3840 or min(w, h) >= 2160:
            return f"4K UHD ({w}x{h})"
        elif min(w, h) >= 1080:
            return f"1080p FHD ({w}x{h})"
        elif min(w, h) >= 720:
            return f"720p HD ({w}x{h})"
        elif min(w, h) >= 480:
            return f"480p SD ({w}x{h})"
        return f"{w}x{h}"

    def get_summary_card(self, hardware_summary: str = "") -> str:
        """Generates a structured, clean Telegram markdown prompt."""
        size_str = format_bytes(self.file_size_bytes)
        dur_str = format_duration(self.duration_seconds)
        
        # High bitrate alert
        bitrate_note = ""
        if self.is_high_bitrate and self.duration_seconds > 0:
            bitrate_note = f"\n[NOTE] **High Bitrate Bloat:** ~{self.overall_bitrate_kbps // 1000} Mbps (Can be drastically optimized)"

        card = [
            "**Video Analysis Report**\n",
            f"• **Name:** `{self.file_name}`",
            f"• **Size:** `{size_str}` {bitrate_note}",
            f"• **Duration:** `{dur_str}` ({int(self.duration_seconds)}s)",
        ]

        if self.has_video:
            fps_str = f" @ {int(self.fps)}fps" if self.fps else ""
            codec_display = self.video_codec.upper() if self.video_codec else "N/A"
            if self.video_codec:
                c_low = self.video_codec.lower()
                if c_low in ("h264", "avc1"):
                    codec_display = "H.264 / AVC"
                elif c_low in ("hevc", "h265"):
                    codec_display = "H.265 / HEVC"
                elif c_low == "vp9":
                    codec_display = "VP9"
                elif c_low == "av1":
                    codec_display = "AV1"

            card.append(f"• **Resolution:** `{self.resolution_label}`{fps_str}")
            card.append(f"• **Video Codec:** `{codec_display}`")
        
        if self.has_audio:
            ch_str = f"{self.audio_channels} ch" if self.audio_channels else "audio"
            sr_str = f"{self.audio_sample_rate}Hz" if self.audio_sample_rate else ""
            br_str = f" | {self.audio_bitrate_kbps} kbps" if self.audio_bitrate_kbps else ""
            card.append(f"• **Audio:** `{self.audio_codec.upper() if self.audio_codec else 'N/A'}` ({ch_str}, {sr_str}{br_str})")
        else:
            card.append("• **Audio:** `None`")

        if hardware_summary:
            card.append(f"• **Engine:** `{hardware_summary}`")

        card.append("\n**Select an action below to proceed:**")
        return "\n".join(card)


async def probe_media(file_path: Path) -> MediaMetadata:
    """
    Asynchronously probes media file using ffprobe and returns parsed metadata.
    """
    if not file_path.exists():
        raise FileNotFoundError(f"File not found: {file_path}")

    file_size = os.path.getsize(file_path)
    file_name = file_path.name

    cmd = [
        "ffprobe",
        "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        "-show_streams",
        str(file_path)
    ]

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await proc.communicate()

    if proc.returncode != 0:
        raise RuntimeError(f"FFprobe failed ({proc.returncode}): {stderr.decode(errors='ignore')}")

    data = json.loads(stdout.decode(errors="ignore"))
    format_info = data.get("format", {})
    streams = data.get("streams", [])

    duration = float(format_info.get("duration", 0.0))
    container = format_info.get("format_name", "unknown")

    # Find video and audio streams
    video_stream = None
    audio_stream = None
    for s in streams:
        if s.get("codec_type") == "video" and not video_stream:
            # Skip image attachments/covers if any
            if s.get("codec_name") not in ["mjpeg", "png"] or len(streams) == 1:
                video_stream = s
        elif s.get("codec_type") == "audio" and not audio_stream:
            audio_stream = s

    has_video = video_stream is not None
    has_audio = audio_stream is not None

    video_codec = video_stream.get("codec_name") if video_stream else None
    width = int(video_stream.get("width")) if video_stream and video_stream.get("width") else None
    height = int(video_stream.get("height")) if video_stream and video_stream.get("height") else None

    # Calculate FPS
    fps = None
    if video_stream:
        r_fps = video_stream.get("r_frame_rate", "0/1")
        try:
            num, den = map(int, r_fps.split("/"))
            if den > 0:
                fps = round(num / den, 2)
        except Exception:
            fps = None

    # Video bitrate
    video_bitrate = None
    if video_stream and video_stream.get("bit_rate"):
        try:
            video_bitrate = int(int(video_stream.get("bit_rate")) / 1000)
        except Exception:
            pass

    # Audio stream info
    audio_codec = audio_stream.get("codec_name") if audio_stream else None
    audio_channels = int(audio_stream.get("channels")) if audio_stream and audio_stream.get("channels") else None
    audio_sample_rate = int(audio_stream.get("sample_rate")) if audio_stream and audio_stream.get("sample_rate") else None
    audio_bitrate = None
    if audio_stream and audio_stream.get("bit_rate"):
        try:
            audio_bitrate = int(int(audio_stream.get("bit_rate")) / 1000)
        except Exception:
            pass

    # Overall bitrate calculation
    if duration > 0:
        overall_bitrate_kbps = int((file_size * 8) / (duration * 1000))
    else:
        overall_bitrate_kbps = 0

    # Detect high bitrate: e.g. 1080p > 8 Mbps or 720p > 4 Mbps or > 15 Mbps in general
    is_high_bitrate = False
    if overall_bitrate_kbps > 12000:
        is_high_bitrate = True
    elif height and height <= 1080 and overall_bitrate_kbps > 8000:
        is_high_bitrate = True
    elif height and height <= 720 and overall_bitrate_kbps > 4000:
        is_high_bitrate = True

    return MediaMetadata(
        file_path=file_path,
        file_name=file_name,
        file_size_bytes=file_size,
        duration_seconds=duration,
        container_format=container,
        has_video=has_video,
        video_codec=video_codec,
        width=width,
        height=height,
        fps=fps,
        video_bitrate_kbps=video_bitrate,
        has_audio=has_audio,
        audio_codec=audio_codec,
        audio_channels=audio_channels,
        audio_sample_rate=audio_sample_rate,
        audio_bitrate_kbps=audio_bitrate,
        overall_bitrate_kbps=overall_bitrate_kbps,
        is_high_bitrate=is_high_bitrate,
        raw_info=data
    )


def format_before_after_analysis(before: MediaMetadata, after: MediaMetadata) -> str:
    """Formats a side-by-side comparative analysis of before and after media."""
    if after.file_size_bytes < before.file_size_bytes:
        saved = before.file_size_bytes - after.file_size_bytes
        ratio = (saved / before.file_size_bytes) * 100
        size_diff = f"{format_bytes(before.file_size_bytes)} -> {format_bytes(after.file_size_bytes)} ({ratio:.1f}% Saved)"
    elif after.file_size_bytes == before.file_size_bytes:
        size_diff = f"{format_bytes(before.file_size_bytes)} -> {format_bytes(after.file_size_bytes)} (Identical)"
    else:
        inc = after.file_size_bytes - before.file_size_bytes
        inc_pct = (inc / before.file_size_bytes) * 100
        size_diff = f"{format_bytes(before.file_size_bytes)} -> {format_bytes(after.file_size_bytes)} (+{inc_pct:.1f}% for standard compatibility)"

    lines = ["**Before & After Media Analysis:**"]
    
    # Video resolution & codec
    if before.has_video and after.has_video:
        lines.append(f"• **Resolution:** `{before.resolution_label}` -> `{after.resolution_label}`")
        v_before = before.video_codec.upper() if before.video_codec else "Unknown"
        v_after = after.video_codec.upper() if after.video_codec else "Unknown"
        lines.append(f"• **Video Codec:** `{v_before}` -> `{v_after}`")
    elif before.has_video and not after.has_video:
        lines.append(f"• **Video Stream:** `{before.resolution_label}` -> `Stripped (Audio Only)`")

    # Audio codec & channels
    a_before = f"{before.audio_codec.upper()} ({before.audio_channels}ch)" if before.has_audio and before.audio_codec else "None"
    a_after = f"{after.audio_codec.upper()} ({after.audio_channels}ch)" if after.has_audio and after.audio_codec else "None"
    lines.append(f"• **Audio Codec:** `{a_before}` -> `{a_after}`")

    # Overall bitrate
    lines.append(f"• **Overall Bitrate:** `{before.overall_bitrate_kbps} kbps` -> `{after.overall_bitrate_kbps} kbps`")
    lines.append(f"• **File Size:** `{size_diff}`")

    return "\n".join(lines)
