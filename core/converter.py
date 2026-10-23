"""
Format & Container Conversion Engine
Supports:
- Video: MP4, MKV, AVI, WEBM, MOV, MPG
- Audio Extraction: MP3, AAC, OGG (Opus), WAV
"""

from pathlib import Path
from typing import List, Tuple
from core.probe import MediaMetadata
from core.hardware import HardwareProfile, current_hardware


class VideoConverter:
    """Generates FFmpeg command parameters for video conversion and audio extraction."""

    SUPPORTED_VIDEO_FORMATS = ["audio_fix", "mp4", "mkv", "webm", "avi", "mov", "mpg"]
    SUPPORTED_AUDIO_FORMATS = ["mp3", "aac", "ogg", "wav"]

    @staticmethod
    def build_convert_args(
        meta: MediaMetadata,
        target_format: str,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> Tuple[List[str], bool]:
        """
        Builds FFmpeg parameters for target format conversion.
        Returns: (cmd_args, is_audio_only)
        """
        fmt = target_format.lower().strip()
        cmd = ["-i", str(meta.file_path)]
        is_audio = False

        if fmt in ("audio_fix", "fix_audio", "fixaudio"):
            # Smartphone Audio Fix: Copy video untouched (-c:v copy), convert audio to standard AAC stereo 192k
            cmd.extend([
                "-c:v", "copy",
                "-c:a", "aac", "-b:a", "192k", "-ac", "2",
                "-movflags", "+faststart"
            ])

        elif fmt == "mp4":
            # If already H.264 + AAC, fast stream copy!
            if meta.video_codec == "h264" and meta.audio_codec in ("aac", None):
                cmd.extend(["-c", "copy", "-movflags", "+faststart"])
            else:
                cmd.extend([
                    "-c:v", "libx264", "-preset", "veryfast" if hw.is_termux else "fast",
                    "-crf", "23", "-pix_fmt", "yuv420p",
                    "-c:a", "aac", "-b:a", "160k",
                    "-movflags", "+faststart"
                ])

        elif fmt == "mkv":
            # Matroska accepts virtually all codecs - fast copy if possible!
            cmd.extend(["-c", "copy"])

        elif fmt == "webm":
            cmd.extend([
                "-c:v", "libvpx-vp9", "-crf", "30", "-b:v", "0",
                "-c:a", "libopus", "-b:a", "128k",
                "-threads", str(hw.cpu_cores)
            ])

        elif fmt == "avi":
            cmd.extend([
                "-c:v", "mpeg4", "-q:v", "5",
                "-c:a", "libmp3lame", "-b:a", "160k"
            ])

        elif fmt == "mov":
            cmd.extend([
                "-c:v", "libx264", "-preset", "fast",
                "-crf", "22", "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k"
            ])

        elif fmt == "mpg":
            cmd.extend([
                "-c:v", "mpeg2video", "-q:v", "4",
                "-c:a", "mp2", "-b:a", "192k"
            ])

        # Audio Extractions
        elif fmt == "mp3":
            is_audio = True
            cmd.extend(["-vn", "-c:a", "libmp3lame", "-q:a", "2"])

        elif fmt == "aac":
            is_audio = True
            if meta.audio_codec == "aac":
                cmd.extend(["-vn", "-c:a", "copy"])
            else:
                cmd.extend(["-vn", "-c:a", "aac", "-b:a", "192k"])

        elif fmt == "ogg":
            is_audio = True
            cmd.extend(["-vn", "-c:a", "libopus", "-b:a", "128k"])

        elif fmt == "wav":
            is_audio = True
            cmd.extend(["-vn", "-c:a", "pcm_s16le"])

        else:
            raise ValueError(f"Unsupported format: {fmt}")

        cmd.append(str(output_path))
        return cmd, is_audio
