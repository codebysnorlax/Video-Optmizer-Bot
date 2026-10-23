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
    low_cmd, low_exp = VideoOptimizer.build_low_device_args(meta, out_file, current_hardware)
    assert "-i" in low_cmd
    assert str(out_file) in low_cmd
    assert len(low_exp) > 0

    # Test 2: Moderate
    mod_cmd, mod_exp = VideoOptimizer.build_moderate_args(meta, out_file, current_hardware)
    assert "-i" in mod_cmd
    assert str(out_file) in mod_cmd
    assert len(mod_exp) > 0

    # Test 3: Auto Recommended
    auto_cmd, auto_exp = VideoOptimizer.build_auto_recommended_args(meta, out_file, current_hardware)
    assert "-i" in auto_cmd
    assert len(auto_exp) > 0


def test_never_upscale_low_resolution_and_fps():
    """Verify that a 420p 24fps video is never upscaled to 720p/1080p and fps is never upsampled."""
    meta_420p = MediaMetadata(
        file_path=Path("/tmp/fake_420p.mp4"),
        file_name="fake_420p.mp4",
        file_size_bytes=2000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=420,
        height=746,
        fps=24.0,
        video_bitrate_kbps=400,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=64,
        overall_bitrate_kbps=464,
        is_high_bitrate=False,
        raw_info={}
    )

    # 1. Scale filter must be None for 720p and 1080p
    assert VideoOptimizer.get_scale_filter(meta_420p, 720) is None
    assert VideoOptimizer.get_scale_filter(meta_420p, 1080) is None

    # 2. FPS filter must be None
    assert VideoOptimizer.get_fps_filter(meta_420p, 60.0) is None

    out_file = Path("/tmp/out.mp4")
    from core.hardware import HardwareProfile
    cpu_hw = HardwareProfile(
        os_name="linux",
        is_termux=False,
        arch="x86_64",
        cpu_cores=4,
        total_ram_mb=8000,
        available_ram_mb=4000,
        cpu_model="test_cpu",
        video_encoder="libx264",
        hevc_encoder="libx265",
        hwaccel_type="cpu",
        hwaccel_device=None,
        description="CPU Engine"
    )

    # Low device mode on 420p
    cmd_low, exp_low = VideoOptimizer.build_low_device_args(meta_420p, out_file, cpu_hw)
    assert "scale=" not in str(cmd_low)
    assert "fps=" not in str(cmd_low)
    assert "never upscaled" in exp_low
    assert "never upsampled" in exp_low

    # Moderate mode on 420p
    cmd_mod, exp_mod = VideoOptimizer.build_moderate_args(meta_420p, out_file, cpu_hw)
    assert "scale=" not in str(cmd_mod)
    assert "fps=" not in str(cmd_mod)
    assert "never upscaled" in exp_mod
    assert "never upsampled" in exp_mod

    # Auto mode on 420p
    cmd_auto, exp_auto = VideoOptimizer.build_auto_recommended_args(meta_420p, out_file, cpu_hw)
    assert "scale=" not in str(cmd_auto)
    assert "fps=" not in str(cmd_auto)
    assert "never upscaled" in exp_auto
    assert "never upsampled" in exp_auto


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


def test_4k_vertical_downscaling():
    """Verify that a 4K 60fps vertical video (2160x3840) downscales correctly to 720p and 1080p with strict VBV rate control."""
    meta_4k = MediaMetadata(
        file_path=Path("/tmp/fake_4k.mp4"),
        file_name="douyin_4k.mp4",
        file_size_bytes=7800000,
        duration_seconds=11.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=2160,
        height=3840,
        fps=60.0,
        video_bitrate_kbps=5857,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=5857,
        is_high_bitrate=False,
        raw_info={}
    )
    out_file = Path("/tmp/out.mp4")

    # 1. Low Device Mode (must downscale to 720p cap with scale=720:-2)
    cmd_low, exp_low = VideoOptimizer.build_low_device_args(meta_4k, out_file)
    assert "-vf" in cmd_low
    vf_idx = cmd_low.index("-vf")
    assert "scale=720:-2" in cmd_low[vf_idx + 1]
    if current_hardware.hwaccel_type == "vaapi":
        assert "h264_vaapi" in cmd_low
        assert "-qp" in cmd_low
    else:
        assert "-maxrate" in cmd_low
        assert cmd_low[cmd_low.index("-preset") + 1] == "veryfast"
    assert "720p HD cap" in exp_low

    # 2. Moderate Mode (must downscale to 1080p cap with scale=1080:-2)
    cmd_mod, exp_mod = VideoOptimizer.build_moderate_args(meta_4k, out_file)
    assert "-vf" in cmd_mod
    vf_idx = cmd_mod.index("-vf")
    assert "scale=1080:-2" in cmd_mod[vf_idx + 1]
    if current_hardware.hwaccel_type == "vaapi":
        assert "h264_vaapi" in cmd_mod
        assert "-qp" in cmd_mod
    else:
        assert cmd_mod[cmd_mod.index("-preset") + 1] == "veryfast"
    assert "1080p FHD cap" in exp_mod

    # 3. Auto Recommended Mode (Downscales heavy 4K to 1080p FHD cap for lag-free mobile playback)
    cmd_auto, exp_auto = VideoOptimizer.build_auto_recommended_args(meta_4k, out_file)
    assert "-vf" in cmd_auto
    vf_idx = cmd_auto.index("-vf")
    assert "scale=1080:-2" in cmd_auto[vf_idx + 1]
    assert "1080p FHD cap" in exp_auto
    assert "-movflags" in cmd_auto
    assert "+faststart" in cmd_auto

