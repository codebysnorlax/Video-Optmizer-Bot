"""
Video Optimization Algorithms:
1. Low Device (Ultra Compatibility & Low Bitrate)
2. Moderate (Mid-Range Device Support)
3. Auto Recommended (Smart Problem Detection, Bitrate De-bloat, Perceptually Lossless)
"""

from pathlib import Path
from typing import List, Tuple
from core.probe import MediaMetadata
from core.hardware import HardwareProfile, current_hardware


class VideoOptimizer:
    """Generates FFmpeg command parameters for video optimization."""

    @staticmethod
    def build_low_device_args(
        meta: MediaMetadata,
        output_path: Path,
        hw: HardwareProfile = current_hardware
    ) -> List[str]:
        """
        Mode 1: Low Device Optimize
        - H.264 Baseline/Main profile
        - YUV420p
        - Capped resolution (max 720p)
        - Target bitrate: 1200k, Audio: AAC 128k
        - High compatibility with older phones, TVs, and budget devices.
        """
        cmd = ["-i", str(meta.file_path)]

        # Video scale filter
        vf_filters = []
        if meta.height and meta.height > 720:
            vf_filters.append("scale=-2:720")
        
        # Hardware vs CPU handling
        if hw.hwaccel_type == "vaapi" and hw.hwaccel_device:
            vf_filters.append("format=nv12,hwupload")
            cmd.extend([
                "-init_hw_device", f"vaapi=va:{hw.hwaccel_device}",
                "-filter_hw_device", "va",
                "-vf", ",".join(vf_filters) if vf_filters else "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-b:v", "1200k",
                "-maxrate", "1500k",
                "-bufsize", "2500k"
            ])
        else:
            if vf_filters:
                cmd.extend(["-vf", ",".join(vf_filters)])
            cmd.extend([
                "-c:v", "libx264",
                "-profile:v", "main",
                "-level", "4.0",
                "-preset", "veryfast" if hw.is_termux else "fast",
                "-crf", "26",
                "-maxrate", "1400k",
                "-bufsize", "2800k",
                "-pix_fmt", "yuv420p",
                "-threads", str(hw.cpu_cores)
            ])

        # Audio settings
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
        - H.264 High profile
        - Target CRF 23, maxrate 3500k
        - Audio AAC 160k
        - Great balance of crisp clarity and reduced file size.
        """
        cmd = ["-i", str(meta.file_path)]

        vf_filters = []
        # Downscale 4K to 1080p for mid-range playback stability
        if meta.height and meta.height > 1080:
            vf_filters.append("scale=-2:1080")

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
                "-crf", "23",
                "-maxrate", "3500k",
                "-bufsize", "7000k",
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
        Mode 3: Auto Recommended (Smart Problem Detection & Fix)
        Analyses video characteristics:
        - If high bitrate bloat (e.g. 40s 500MB = ~100 Mbps!), strips raw bit waste.
        - Calculates target CRF (21-22) for perceptually lossless results.
        - Caps VBV buffer at optimal streaming rates.
        - Preserves native 1080p/720p resolution without blurring.
        - Enables +faststart for instant Telegram streamability.
        Returns: (command_args, explanation_of_fix)
        """
        cmd = ["-i", str(meta.file_path)]
        explanation = []

        vf_filters = []
        
        # Check 1: Extreme resolution
        if meta.height and meta.height > 1080:
            vf_filters.append("scale=-2:1080")
            explanation.append("Downscaled 4K UHD to crisp 1080p FHD for fluid playback")
        else:
            explanation.append(f"Preserved native {meta.resolution_label} resolution")

        # Check 2: High Bitrate Bloat
        if meta.overall_bitrate_kbps > 10000:
            explanation.append(f"Eliminated raw camera bitrate bloat ({meta.overall_bitrate_kbps // 1000} Mbps -> Perceptual VBR)")
            target_crf = 22
            max_br = 4000
        else:
            target_crf = 23
            max_br = 3000

        # Video encoder selection
        if hw.hwaccel_type == "vaapi" and hw.hwaccel_device:
            vf_filters.append("format=nv12,hwupload")
            cmd.extend([
                "-init_hw_device", f"vaapi=va:{hw.hwaccel_device}",
                "-filter_hw_device", "va",
                "-vf", ",".join(vf_filters) if vf_filters else "format=nv12,hwupload",
                "-c:v", "h264_vaapi",
                "-qp", str(target_crf)
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
                "-bufsize", f"{max_br * 2}k",
                "-pix_fmt", "yuv420p",
                "-threads", str(hw.cpu_cores)
            ])
            explanation.append(f"Processed via Multi-Threaded Engine ({hw.cpu_cores} cores)")

        # Audio processing: If already AAC, copy stream for zero quality loss!
        if meta.has_audio:
            if meta.audio_codec and meta.audio_codec.lower() == "aac":
                cmd.extend(["-c:a", "copy"])
                explanation.append("Preserved original untouched AAC audio stream")
            else:
                cmd.extend(["-c:a", "aac", "-b:a", "192k"])
                explanation.append("Normalized audio to studio-quality AAC 192k")
        else:
            cmd.extend(["-an"])

        cmd.extend(["-movflags", "+faststart", str(output_path)])
        
        summary_explanation = " • " + "\n • ".join(explanation)
        return cmd, summary_explanation
