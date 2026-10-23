"""
Unit Tests for Video Optimizer Command Builders
"""

import pytest
import subprocess
from pathlib import Path
from core.probe import probe_media
from core.optimizer import VideoOptimizer
from core.converter import VideoConverter
from core.hardware import current_hardware


@pytest.fixture(scope="module")
def sample_media(tmp_path_factory) -> Path:
    tmp_dir = tmp_path_factory.mktemp("opt_media")
    video_path = tmp_dir / "opt_test.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=1920x1080:rate=30",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(video_path)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode == 0
    return video_path


@pytest.mark.asyncio
async def test_optimizer_modes(sample_media: Path, tmp_path: Path):
    meta = await probe_media(sample_media)
    out_file = tmp_path / "out.mp4"

    # Test 1: Low Device
    low_cmd = VideoOptimizer.build_low_device_args(meta, out_file, current_hardware)
    assert "-i" in low_cmd
    assert str(out_file) in low_cmd
    assert "-c:a" in low_cmd

    # Test 2: Moderate
    mod_cmd = VideoOptimizer.build_moderate_args(meta, out_file, current_hardware)
    assert "-i" in mod_cmd
    assert str(out_file) in mod_cmd

    # Test 3: Auto Recommended
    auto_cmd, explanation = VideoOptimizer.build_auto_recommended_args(meta, out_file, current_hardware)
    assert "-i" in auto_cmd
    assert len(explanation) > 0

    # Test 4: Auto + Best Quality + Fix Size
    best_cmd, best_exp = VideoOptimizer.build_best_quality_args(meta, out_file, current_hardware)
    assert "-i" in best_cmd
    assert str(out_file) in best_cmd
    assert "Preserved 100% native" in best_exp
    assert "-movflags" in best_cmd
    assert "+faststart" in best_cmd


@pytest.mark.asyncio
async def test_converter_modes(sample_media: Path, tmp_path: Path):
    meta = await probe_media(sample_media)

    # Convert to MKV
    mkv_out = tmp_path / "out.mkv"
    cmd, is_audio = VideoConverter.build_convert_args(meta, "mkv", mkv_out, current_hardware)
    assert is_audio is False
    assert str(mkv_out) in cmd

    # Convert to MP3
    mp3_out = tmp_path / "out.mp3"
    cmd, is_audio = VideoConverter.build_convert_args(meta, "mp3", mp3_out, current_hardware)
    assert is_audio is True
    assert "-vn" in cmd
    assert "libmp3lame" in cmd

    # Convert with audio_fix (Smartphone Audio Fix)
    audio_fix_out = tmp_path / "out_audiofix.mp4"
    cmd, is_audio = VideoConverter.build_convert_args(meta, "audio_fix", audio_fix_out, current_hardware)
    assert is_audio is False
    assert "-c:v" in cmd
    assert "copy" in cmd
    assert "-c:a" in cmd
    assert "aac" in cmd
    assert "192k" in cmd
