"""
Unit Tests for Hardware & Platform Detection
"""

import pytest
from core.hardware import detect_hardware, HardwareProfile


def test_detect_hardware():
    profile = detect_hardware()
    assert isinstance(profile, HardwareProfile)
    assert profile.cpu_cores > 0
    assert profile.total_ram_mb > 0
    assert profile.video_encoder in ["h264_vaapi", "h264_nvenc", "libx264"]
    assert profile.hwaccel_type in ["vaapi", "nvenc", "cpu"]


def test_hardware_ffmpeg_args_generation():
    profile = detect_hardware()
    args = profile.get_ffmpeg_video_args(target_codec="h264", crf=22, maxrate_kbps=4000)
    assert isinstance(args, list)
    assert len(args) > 0
    # Must have video codec flag
    assert "-c:v" in args
