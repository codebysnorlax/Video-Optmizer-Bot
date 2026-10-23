"""
Unit Tests for Video Optimizer Command Builders
"""

import pytest
import subprocess
from pathlib import Path
from core.probe import probe_media, MediaMetadata
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


def test_scale_filter_orientation():
    # 1. Vertical / portrait 4K (2160x3840)
    meta_vert_4k = MediaMetadata(
        file_path=Path("/tmp/fake.mp4"),
        file_name="vert_4k.mp4",
        file_size_bytes=1000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=2160,
        height=3840,
        fps=60.0,
        video_bitrate_kbps=5000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=5128,
        is_high_bitrate=False,
        raw_info={}
    )
    # Target 720p: should scale width to 720
    assert VideoOptimizer.get_scale_filter(meta_vert_4k, 720) == "scale=720:-2"
    # Target 1080p: should scale width to 1080
    assert VideoOptimizer.get_scale_filter(meta_vert_4k, 1080) == "scale=1080:-2"

    # 2. Horizontal / landscape 4K (3840x2160)
    meta_horiz_4k = MediaMetadata(
        file_path=Path("/tmp/fake.mp4"),
        file_name="horiz_4k.mp4",
        file_size_bytes=1000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=3840,
        height=2160,
        fps=60.0,
        video_bitrate_kbps=5000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=5128,
        is_high_bitrate=False,
        raw_info={}
    )
    # Target 720p: should scale height to 720
    assert VideoOptimizer.get_scale_filter(meta_horiz_4k, 720) == "scale=-2:720"
    # Target 1080p: should scale height to 1080
    assert VideoOptimizer.get_scale_filter(meta_horiz_4k, 1080) == "scale=-2:1080"

    # 3. Native 1080p vertical: target 1080 should not downscale
    meta_vert_1080 = MediaMetadata(
        file_path=Path("/tmp/fake.mp4"),
        file_name="vert_1080.mp4",
        file_size_bytes=1000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1080,
        height=1920,
        fps=30.0,
        video_bitrate_kbps=3000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=3128,
        is_high_bitrate=False,
        raw_info={}
    )
    assert VideoOptimizer.get_scale_filter(meta_vert_1080, 1080) is None
    assert VideoOptimizer.get_scale_filter(meta_vert_1080, 720) == "scale=720:-2"


def test_fps_filter():
    meta_high_fps = MediaMetadata(
        file_path=Path("/tmp/fake.mp4"),
        file_name="high_fps.mp4",
        file_size_bytes=1000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=120.0,
        video_bitrate_kbps=3000,
        has_audio=False,
        audio_codec=None,
        audio_channels=None,
        audio_sample_rate=None,
        audio_bitrate_kbps=None,
        overall_bitrate_kbps=3000,
        is_high_bitrate=False,
        raw_info={}
    )
    assert VideoOptimizer.get_fps_filter(meta_high_fps, 60.0) == "fps=60"

    meta_normal_fps = MediaMetadata(
        file_path=Path("/tmp/fake.mp4"),
        file_name="normal_fps.mp4",
        file_size_bytes=1000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=60.0,
        video_bitrate_kbps=3000,
        has_audio=False,
        audio_codec=None,
        audio_channels=None,
        audio_sample_rate=None,
        audio_bitrate_kbps=None,
        overall_bitrate_kbps=3000,
        is_high_bitrate=False,
        raw_info={}
    )
    assert VideoOptimizer.get_fps_filter(meta_normal_fps, 60.0) is None


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
