"""
Video Optimization Engine:
1. Low Device (Max Compatibility, 720p Cap, <=60fps, Moderate Bitrate)
2. Moderate (Mid-Range Device Support, 1080p Cap, <=60fps, Balanced Bitrate)
3. Auto Recommended (Universal Smartphone Fix, Downscales >1080p, <=60fps, Audio/Codec Compatibility)

Strict High-to-Low Invariant:
- Resolution: NEVER upscales (e.g. 420p or 360p is strictly preserved as native 420p/360p). Only downscales if larger than mode cap.
- Framerate: NEVER upsamples (e.g. 24fps or 30fps is strictly preserved). Only downsamples if > 60 fps.
- Bitrate: NEVER increases (target cap is strictly below source bitrate).
- Audio: Untouched copy if already AAC; never bloats audio bitrate.
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
            return max(150, total_kbps - (audio_kbps or 128))
        return meta.overall_bitrate_kbps or 2500

    @staticmethod
    def get_scale_filter(meta: MediaMetadata, max_dim: int) -> Optional[str]:
        """
        Returns orientation-aware scale filter to bound resolution within max_dim (e.g. 720 or 1080).
        CRITICAL RULE: NEVER UPSCALE.
        Only downscales if the source dimension exceeds max_dim.
        For portrait (width < height): scales width to max_dim ONLY IF width > max_dim.
        For landscape/square (width >= height): scales height to max_dim ONLY IF height > max_dim.
        Returns None if resolution is already <= max_dim (e.g. 420p, 480p, 720p).
        """
        if not meta.width or not meta.height:
            return None
        if meta.width < meta.height:
            # Vertical / portrait: only scale if width > max_dim
            if meta.width > max_dim:
                return f"scale={max_dim}:-2"
        else:
            # Horizontal / landscape / square: only scale if height > max_dim
            if meta.height > max_dim:
                return f"scale=-2:{max_dim}"
        return None

    @staticmethod
    def get_fps_filter(meta: MediaMetadata, max_fps: float = 60.0) -> Optional[str]:
        """
        CRITICAL RULE: NEVER UPSAMPLE FRAMERATE.
        Only caps framerate if source strictly exceeds max_fps (e.g. 120fps -> 60fps).
        Preserves exact source framerate if source <= max_fps (e.g. 24fps, 30fps).
        """
        if meta.fps and meta.fps > max_fps:
            return f"fps={int(max_fps)}"
        return None

    @staticmethod
    def build_low_device_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> Tuple[List[str], str]:
        """
        Mode 1: Low Device Optimize (Max Compatibility)
        - Resolution: Downscales to 720p ONLY if larger than 720p; NEVER upscales (e.g. 420p stays 420p).
        - Framerate: Caps at 60 fps ONLY if source > 60 fps; NEVER upsamples (e.g. 30fps stays 30fps).
        - Bitrate: Compresses very high bitrates to moderate (max 2000k), strictly lower than source.
        - Codec: H.264 Main profile YUV420p for older & budget devices.
        - Audio: Untouched copy if AAC; otherwise normalizes to AAC stereo.
        """
        src_br = VideoOptimizer.get_source_video_bitrate(meta)
        target_br = int(src_br * 0.55)
        # Ensure max_br never exceeds 85% of source bitrate (never increases bitrate)
        max_br = min(2000, min(int(src_br * 0.85), max(150, target_br)))
        buf_size = max_br * 2

        cmd = ["-i", str(meta.file_path)]
        explanation: List[str] = []

        # Video filters (orientation-aware 720p cap + 60fps cap, never upscale)
        vf_filters: List[str] = []
        scale_f = VideoOptimizer.get_scale_filter(meta, 720)
        if scale_f:
            vf_filters.append(scale_f)
            explanation.append(f"Downscaled resolution ({meta.resolution_label} -> 720p HD cap)")
        else:
            explanation.append(f"Preserved exact native {meta.resolution_label} resolution (never upscaled)")

        fps_f = VideoOptimizer.get_fps_filter(meta, 60.0)
        if fps_f:
            vf_filters.append(fps_f)
            explanation.append(f"Capped framerate ({meta.fps:.0f} fps -> 60 fps)")
        elif meta.fps:
            explanation.append(f"Preserved native framerate ({meta.fps:.0f} fps, never upsampled)")

        explanation.append(f"Reduced stream density ({src_br} kbps -> {max_br} kbps cap, strictly reduced)")

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
            explanation.append("Accelerated via Hardware GPU (Intel VAAPI)")
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
            explanation.append(f"Processed via Multi-Threaded Engine ({hw.cpu_cores} cores)")

        # Audio settings: preserve untouched if already AAC, otherwise convert without inflating
        if meta.has_audio:
            is_aac = meta.audio_codec and meta.audio_codec.lower() == "aac"
            is_stereo_or_mono = meta.audio_channels in (1, 2) if meta.audio_channels else True
            if is_aac and is_stereo_or_mono:
                cmd.extend(["-c:a", "copy"])
                explanation.append("Preserved original untouched AAC audio (zero re-encoding)")
            else:
                audio_br = min(128, meta.audio_bitrate_kbps or 128)
                cmd.extend(["-c:a", "aac", "-b:a", f"{audio_br}k", "-ac", "2"])
                explanation.append(f"Normalized audio to universal AAC stereo ({audio_br}k)")
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output_path)])
        summary_explanation = " • " + "\n • ".join(explanation)
        return cmd, summary_explanation

    @staticmethod
    def build_moderate_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> Tuple[List[str], str]:
        """
        Mode 2: Moderate (Mid-Range Device Support)
        - Resolution: Downscales to 1080p ONLY if larger than 1080p; NEVER upscales (e.g. 720p or 420p stays native).
        - Framerate: Caps at 60 fps ONLY if source > 60 fps; NEVER upsamples.
        - Bitrate: Compresses high bitrate bloat to ~2800k-3200k max, strictly lower than source.
        - Codec: H.264 High profile YUV420p.
        - Audio: Untouched copy if AAC; otherwise normalizes to AAC stereo 160k.
        """
        src_br = VideoOptimizer.get_source_video_bitrate(meta)
        target_br = int(src_br * 0.65)
        # Ensure max_br never exceeds 85% of source bitrate (never increases bitrate)
        max_br = min(3200, min(int(src_br * 0.85), max(200, target_br)))
        buf_size = max_br * 2

        cmd = ["-i", str(meta.file_path)]
        explanation: List[str] = []

        # Video filters (orientation-aware 1080p cap + 60fps cap, never upscale)
        vf_filters: List[str] = []
        scale_f = VideoOptimizer.get_scale_filter(meta, 1080)
        if scale_f:
            vf_filters.append(scale_f)
            explanation.append(f"Downscaled resolution ({meta.resolution_label} -> 1080p FHD cap)")
        else:
            explanation.append(f"Preserved exact native {meta.resolution_label} resolution (never upscaled)")

        fps_f = VideoOptimizer.get_fps_filter(meta, 60.0)
        if fps_f:
            vf_filters.append(fps_f)
            explanation.append(f"Capped framerate ({meta.fps:.0f} fps -> 60 fps)")
        elif meta.fps:
            explanation.append(f"Preserved native framerate ({meta.fps:.0f} fps, never upsampled)")

        explanation.append(f"Optimized stream density for mid-range devices ({src_br} kbps -> {max_br} kbps cap)")

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
                "-crf", "22",
                "-maxrate", f"{max_br}k",
                "-bufsize", f"{buf_size}k",
                "-pix_fmt", "yuv420p",
                "-threads", str(hw.cpu_cores)
            ])
            explanation.append(f"Processed via Multi-Threaded Engine ({hw.cpu_cores} cores)")

        # Audio settings: preserve untouched if already AAC, otherwise convert without inflating
        if meta.has_audio:
            is_aac = meta.audio_codec and meta.audio_codec.lower() == "aac"
            is_stereo_or_mono = meta.audio_channels in (1, 2) if meta.audio_channels else True
            if is_aac and is_stereo_or_mono:
                cmd.extend(["-c:a", "copy"])
                explanation.append("Preserved original untouched AAC audio (zero re-encoding)")
            else:
                audio_br = min(160, meta.audio_bitrate_kbps or 160)
                cmd.extend(["-c:a", "aac", "-b:a", f"{audio_br}k", "-ac", "2"])
                explanation.append(f"Normalized audio to universal AAC stereo ({audio_br}k)")
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output_path)])
        summary_explanation = " • " + "\n • ".join(explanation)
        return cmd, summary_explanation

    @staticmethod
    def build_auto_recommended_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> Tuple[List[str], str]:
        """
        Mode 3: Auto Recommended (Smart Problem Detection & Universal Phone Playability)
        - Analyses video resolution: downscales 4K/2K (>1080p) to 1080p for fluid smartphone playback; NEVER upscales.
        - Framerate: Caps at 60 fps ONLY if source > 60 fps; NEVER upsamples.
        - Bitrate: Optimizes density (max 3200k), strictly lower than source without perceptual loss.
        - Audio: Untouched AAC stream copy if compatible; otherwise normalizes to stereo AAC 192k.
        - Enables +faststart for instant Telegram streamability.
        Returns: (command_args, explanation_of_fix)
        """
        src_br = VideoOptimizer.get_source_video_bitrate(meta)
        target_br = int(src_br * 0.65)
        # Ensure max_br never exceeds 85% of source bitrate (never increases bitrate)
        max_br = min(3200, min(int(src_br * 0.85), max(200, target_br)))
        buf_size = max_br * 2

        cmd = ["-i", str(meta.file_path)]
        explanation: List[str] = []

        vf_filters: List[str] = []
        
        # 1. Resolution Check (Downscale >1080p to 1080p, never upscale)
        scale_f = VideoOptimizer.get_scale_filter(meta, 1080)
        if scale_f:
            vf_filters.append(scale_f)
            explanation.append(f"Downscaled high-resolution ({meta.resolution_label}) to crisp 1080p FHD for fluid mobile playback")
        else:
            explanation.append(f"Preserved exact native {meta.resolution_label} resolution (never upscaled)")

        # 2. Framerate Check (Cap >60fps at 60fps, never upsample)
        fps_f = VideoOptimizer.get_fps_filter(meta, 60.0)
        if fps_f:
            vf_filters.append(fps_f)
            explanation.append(f"Capped high framerate ({meta.fps:.0f} fps -> 60 fps) for smooth hardware decoding")
        elif meta.fps:
            explanation.append(f"Preserved native framerate ({meta.fps:.0f} fps, never upsampled)")

        # 3. Bitrate Bloat Check
        if meta.overall_bitrate_kbps > 8000:
            explanation.append(f"Eliminated camera bitrate bloat ({meta.overall_bitrate_kbps // 1000} Mbps -> {max_br // 1000} Mbps cap)")
            target_crf = 22
        else:
            explanation.append(f"Optimized stream density ({src_br} kbps -> {max_br} kbps cap, strictly reduced)")
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
                audio_br = min(192, meta.audio_bitrate_kbps or 192)
                cmd.extend(["-c:a", "aac", "-b:a", f"{audio_br}k", "-ac", "2"])
                explanation.append(f"Normalized audio to universal smartphone stereo AAC ({audio_br}k)")
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output_path)])
        
        summary_explanation = " • " + "\n • ".join(explanation)
        return cmd, summary_explanation
