"""
Video Optimization Engine:
1. Low Device (Max Compatibility, 720p Cap, <=60fps, Moderate Bitrate)
2. Moderate (Mid-Range Device Support, 1080p Cap, <=60fps, Balanced Bitrate)
3. Auto Recommended (Universal Smartphone Fix, Downscales >1080p, <=60fps, Audio/Codec Compatibility)
"""

from pathlib import Path
from typing import List, Tuple, Optional
from core.probe import MediaMetadata
from core.hardware import HardwareProfile, current_hardware


class VideoOptimizer:
    """Generates FFmpeg command parameters for video optimization."""

    @staticmethod
    def get_source_video_bitrate(meta: MediaMetadata) -> int:
        """
        Accurately estimates source video stream bitrate in kbps.
        Falls back to total file bitrate minus audio if video stream bitrate is absent.
        """
        if meta.video_bitrate_kbps and meta.video_bitrate_kbps > 100:
            return meta.video_bitrate_kbps
        if meta.duration_seconds > 0 and meta.file_size_bytes > 0:
            total_kbps = int((meta.file_size_bytes * 8) / (meta.duration_seconds * 1000))
            audio_kbps = meta.audio_bitrate_kbps if meta.has_audio else 0
            return max(300, total_kbps - (audio_kbps or 128))
        return meta.overall_bitrate_kbps or 3500

    @staticmethod
    def get_scale_filter(meta: MediaMetadata, max_dim: int) -> Optional[str]:
        """
        Returns orientation-aware scale filter to bound resolution within max_dim (e.g. 720 or 1080).
        For portrait (width < height): scales width to max_dim if width > max_dim.
        For landscape/square (width >= height): scales height to max_dim if height > max_dim.
        Returns None if downscaling is not needed.
        """
        if not meta.width or not meta.height:
            return None
        if meta.width < meta.height:
            # Vertical / portrait: scale width to max_dim, auto height
            if meta.width > max_dim:
                return f"scale={max_dim}:-2"
        else:
            # Horizontal / landscape / square: scale height to max_dim, auto width
            if meta.height > max_dim:
                return f"scale=-2:{max_dim}"
        return None

    @staticmethod
    def get_fps_filter(meta: MediaMetadata, max_fps: float = 60.0) -> Optional[str]:
        """
        Caps framerate at max_fps if source exceeds it.
        Preserves exact source framerate if source <= max_fps.
        """
        if meta.fps and meta.fps > max_fps:
            return f"fps={int(max_fps)}"
        return None

    @staticmethod
    def build_low_device_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> List[str]:
        """
        Mode 1: Low Device Optimize (Max Compatibility)
        - Resolution: Downscales to 720p if larger than 720p (portrait/landscape aware); keeps native if <= 720p.
        - Framerate: Caps at 60 fps if source > 60 fps; keeps native source FPS if <= 60 fps.
        - Bitrate: Compresses very high bitrates to moderate (~1800k-2000k max), never too low.
        - Codec: H.264 Main profile YUV420p for older & budget devices.
        - Audio: Universal AAC stereo 128k.
        """
        src_br = VideoOptimizer.get_source_video_bitrate(meta)
        max_br = min(2000, max(500, int(src_br * 0.55)))
        buf_size = max_br * 2

        cmd = ["-i", str(meta.file_path)]

        # Video filters (orientation-aware 720p cap + 60fps cap)
        vf_filters: List[str] = []
        scale_f = VideoOptimizer.get_scale_filter(meta, 720)
        if scale_f:
            vf_filters.append(scale_f)
        fps_f = VideoOptimizer.get_fps_filter(meta, 60.0)
        if fps_f:
            vf_filters.append(fps_f)

        # Hardware vs CPU handling
        if hw.hwaccel_type == "vaapi" and hw.hwaccel_device:
            vf_filters.append("format=nv12,hwupload")
            cmd.extend([
                "-init_hw_device", f"vaapi=va:{hw.hwaccel_device}",
                "-filter_hw_device", "va",
                "-vf", ",".join(vf_filters) if vf_filters else "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-qp", "26"
            ])
        else:
            if vf_filters:
                cmd.extend(["-vf", ",".join(vf_filters)])
            cmd.extend([
                "-c:v", "libx264",
                "-profile:v", "main",
                "-level", "4.0",
                "-preset", "veryfast" if hw.is_termux else "fast",
                "-crf", "24",
                "-maxrate", f"{max_br}k",
                "-bufsize", f"{buf_size}k",
                "-pix_fmt", "yuv420p",
                "-threads", str(hw.cpu_cores)
            ])

        # Audio settings: stereo AAC 128k
        cmd.extend([
            "-c:a", "aac",
            "-b:a", "128k",
            "-ac", "2",
            "-movflags", "+faststart",
            str(output_path)
        ])
        return cmd

    @staticmethod
    def build_moderate_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> List[str]:
        """
        Mode 2: Moderate (Mid-Range Device Support)
        - Resolution: Downscales to 1080p if larger than 1080p; keeps native if <= 1080p.
        - Framerate: Caps at 60 fps if source > 60 fps; keeps native source FPS if <= 60 fps.
        - Bitrate: Compresses high bitrate bloat to ~2800k-3200k max so mid-level smartphones handle smoothly.
        - Codec: H.264 High profile YUV420p.
        - Audio: AAC 160k.
        """
        src_br = VideoOptimizer.get_source_video_bitrate(meta)
        max_br = min(3200, max(600, int(src_br * 0.65)))
        buf_size = max_br * 2

        cmd = ["-i", str(meta.file_path)]

        # Video filters (orientation-aware 1080p cap + 60fps cap)
        vf_filters: List[str] = []
        scale_f = VideoOptimizer.get_scale_filter(meta, 1080)
        if scale_f:
            vf_filters.append(scale_f)
        fps_f = VideoOptimizer.get_fps_filter(meta, 60.0)
        if fps_f:
            vf_filters.append(fps_f)

        if hw.hwaccel_type == "vaapi" and hw.hwaccel_device:
            vf_filters.append("format=nv12,hwupload")
            cmd.extend([
                "-init_hw_device", f"vaapi=va:{hw.hwaccel_device}",
                "-filter_hw_device", "va",
                "-vf", ",".join(vf_filters) if vf_filters else "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-qp", "24"
            ])
        else:
            if vf_filters:
                cmd.extend(["-vf", ",".join(vf_filters)])
            cmd.extend([
                "-c:v", "libx264",
                "-profile:v", "high",
                "-level", "4.1",
                "-preset", "veryfast" if hw.is_termux else "fast",
                "-crf", "22",
                "-maxrate", f"{max_br}k",
                "-bufsize", f"{buf_size}k",
                "-pix_fmt", "yuv420p",
                "-threads", str(hw.cpu_cores)
            ])

        cmd.extend([
            "-c:a", "aac",
            "-b:a", "160k",
            "-movflags", "+faststart",
            str(output_path)
        ])
        return cmd

    @staticmethod
    def build_auto_recommended_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> Tuple[List[str], str]:
        """
        Mode 3: Auto Recommended (Smart Problem Detection & Universal Phone Playability)
        - Analyses video resolution: downscales 4K/2K (>1080p) to 1080p for fluid smartphone playback.
        - Framerate: Caps at 60 fps if source > 60 fps; keeps native source FPS if <= 60 fps.
        - Bitrate: Optimizes density (max 3200k), strictly reducing file size without perceptual loss.
        - Audio: Untouched AAC stream copy if compatible; otherwise normalizes to stereo AAC 192k.
        - Enables +faststart for instant Telegram streamability.
        Returns: (command_args, explanation_of_fix)
        """
        src_br = VideoOptimizer.get_source_video_bitrate(meta)
        max_br = min(3200, max(600, int(src_br * 0.65)))
        buf_size = max_br * 2

        cmd = ["-i", str(meta.file_path)]
        explanation: List[str] = []

        vf_filters: List[str] = []
        
        # 1. Resolution Check (Downscale >1080p to 1080p)
        scale_f = VideoOptimizer.get_scale_filter(meta, 1080)
        if scale_f:
            vf_filters.append(scale_f)
            explanation.append("Downscaled high-resolution (4K/2K) to crisp 1080p FHD for fluid mobile playback")
        else:
            explanation.append(f"Preserved native {meta.resolution_label} resolution")

        # 2. Framerate Check (Cap >60fps at 60fps)
        fps_f = VideoOptimizer.get_fps_filter(meta, 60.0)
        if fps_f:
            vf_filters.append(fps_f)
            explanation.append(f"Capped high framerate ({meta.fps:.0f} fps -> 60 fps) for smooth hardware decoding")
        elif meta.fps:
            explanation.append(f"Preserved native framerate ({meta.fps:.0f} fps)")

        # 3. Bitrate Bloat Check
        if meta.overall_bitrate_kbps > 8000:
            explanation.append(f"Eliminated camera bitrate bloat ({meta.overall_bitrate_kbps // 1000} Mbps -> {max_br // 1000} Mbps cap)")
            target_crf = 22
        else:
            explanation.append(f"Optimized stream density (target cap: {max_br} kbps)")
            target_crf = 23

        # 4. Video Encoder Selection
        if hw.hwaccel_type == "vaapi" and hw.hwaccel_device:
            vf_filters.append("format=nv12,hwupload")
            cmd.extend([
                "-init_hw_device", f"vaapi=va:{hw.hwaccel_device}",
                "-filter_hw_device", "va",
                "-vf", ",".join(vf_filters) if vf_filters else "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-qp", "24"
            ])
            explanation.append("Accelerated via Hardware GPU (Intel VAAPI)")
        else:
            if vf_filters:
                cmd.extend(["-vf", ",".join(vf_filters)])
            cmd.extend([
                "-c:v", "libx264",
                "-profile:v", "high",
                "-level", "4.1",
                "-preset", "veryfast" if hw.is_termux else "fast",
                "-crf", str(target_crf),
                "-maxrate", f"{max_br}k",
                "-bufsize", f"{buf_size}k",
                "-pix_fmt", "yuv420p",
                "-threads", str(hw.cpu_cores)
            ])
            explanation.append(f"Processed via Multi-Threaded Engine ({hw.cpu_cores} cores)")

        # 5. Audio Compatibility Check
        if meta.has_audio:
            # If already AAC and stereo/mono, copy stream untouched
            is_aac = meta.audio_codec and meta.audio_codec.lower() == "aac"
            is_stereo_or_mono = meta.audio_channels in (1, 2) if meta.audio_channels else True
            if is_aac and is_stereo_or_mono:
                cmd.extend(["-c:a", "copy"])
                explanation.append("Preserved original untouched AAC audio stream (lossless copy)")
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k", "-ac", "2"])
                explanation.append("Normalized audio to universal smartphone stereo AAC 192k")
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output_path)])
        
        summary_explanation = " • " + "\n • ".join(explanation)
        return cmd, summary_explanation
