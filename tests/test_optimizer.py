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

    # Test 5: 4K video handling ensures libx264 with maxrate under source bitrate
    from core.probe import MediaMetadata
    meta_4k = MediaMetadata(
        file_path=sample_media,
        file_name="4k_sample.mp4",
        file_size_bytes=8000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=2160,
        height=3840,
        fps=60.0,
        video_bitrate_kbps=5800,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=5900,
        is_high_bitrate=False,
        raw_info={}
    )
    cmd_4k, exp_4k = VideoOptimizer.build_best_quality_args(meta_4k, out_file, current_hardware)
    assert "libx264" in cmd_4k
    assert "-maxrate" in cmd_4k
    idx = cmd_4k.index("-maxrate")
    rate_val = int(cmd_4k[idx + 1].replace("k", ""))
    # Guarantee maxrate is strictly less than source bitrate (5800)
    assert rate_val < 5800


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
