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


def test_auto_recommended_bitrate_policy():
    """Verify Auto Recommended mode caps bloated camera bitrates at 20 Mbps and preserves <= 20 Mbps."""
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

    # 1. 50 Mbps raw camera video -> Capped at 20 Mbps
    meta_50mbps = MediaMetadata(
        file_path=Path("/tmp/fake_50mbps.mp4"),
        file_name="cam_4k.mp4",
        file_size_bytes=50000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=3840,
        height=2160,
        fps=60.0,
        video_bitrate_kbps=50000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=256,
        overall_bitrate_kbps=50000,
        is_high_bitrate=True,
        raw_info={}
    )
    cmd_50, exp_50 = VideoOptimizer.build_auto_recommended_args(meta_50mbps, out_file, cpu_hw)
    assert "-maxrate" in cmd_50
    assert "20000k" in cmd_50
    assert "-crf" in cmd_50
    assert cmd_50[cmd_50.index("-crf") + 1] == "17"
    assert "20 Mbps master quality" in exp_50

    # 2. 4K 6 Mbps video -> Downscaled to 1080p with bitrate preserved at ~95%
    meta_6mbps = MediaMetadata(
        file_path=Path("/tmp/fake_6mbps.mp4"),
        file_name="web_4k.mp4",
        file_size_bytes=6000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=3840,
        height=2160,
        fps=30.0,
        video_bitrate_kbps=6000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=6000,
        is_high_bitrate=False,
        raw_info={}
    )
    cmd_6, exp_6 = VideoOptimizer.build_auto_recommended_args(meta_6mbps, out_file, cpu_hw)
    assert "-maxrate" in cmd_6
    assert "5700k" in cmd_6
    assert "Preserved high bitrate stream density" in exp_6

    # 3. Mode 'less': Compresses bitrate for noticeably smaller file size
    cmd_less, exp_less = VideoOptimizer.build_auto_recommended_args(meta_6mbps, out_file, cpu_hw, bitrate_mode="less")
    assert "-maxrate" in cmd_less
    # 6000 * 0.55 = 3300
    assert "3300k" in cmd_less
    assert "-crf" in cmd_less
    assert cmd_less[cmd_less.index("-crf") + 1] in ("24", "25")
    assert "Compressed stream bitrate" in exp_less

    # 4. Mode 'less' on already mobile-fit 1080p: must still compress rather than copy
    meta_1080p_fit = MediaMetadata(
        file_path=Path("/tmp/fake_1080p.mp4"),
        file_name="web_1080p.mp4",
        file_size_bytes=6000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=6000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=6000,
        is_high_bitrate=False,
        raw_info={}
    )
    cmd_fit_same, _ = VideoOptimizer.build_auto_recommended_args(meta_1080p_fit, out_file, cpu_hw, bitrate_mode="same")
    assert "copy" in cmd_fit_same  # 'same' does fast lossless copy

    cmd_fit_less, exp_fit_less = VideoOptimizer.build_auto_recommended_args(meta_1080p_fit, out_file, cpu_hw, bitrate_mode="less")
    assert "copy" not in cmd_fit_less  # 'less' re-encodes to reduce file size
    assert "-maxrate" in cmd_fit_less
    assert "3300k" in cmd_fit_less


def test_720p_and_1080p_bitrate_modes():
    """Verify 720p and 1080p aliases and bitrate_mode behavior."""
    out_file = Path("/tmp/out.mp4")
    meta_720p = MediaMetadata(
        file_path=Path("/tmp/fake_720p.mp4"),
        file_name="clip_720p.mp4",
        file_size_bytes=4000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1280,
        height=720,
        fps=30.0,
        video_bitrate_kbps=3000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=3000,
        is_high_bitrate=False,
        raw_info={}
    )

    # 720p same on 720p compliant video: stream copy
    cmd_720_same, _ = VideoOptimizer.build_720p_args(meta_720p, out_file, bitrate_mode="same")
    assert "copy" in cmd_720_same

    # 720p less on 720p video: re-encode to compress bitrate
    cmd_720_less, exp_720_less = VideoOptimizer.build_720p_args(meta_720p, out_file, bitrate_mode="less")
    assert "copy" not in cmd_720_less
    assert "Compressed stream bitrate" in exp_720_less

    # 1080p same on 720p video: stream copy (already <= 1080p)
    cmd_1080_same, _ = VideoOptimizer.build_1080p_args(meta_720p, out_file, bitrate_mode="same")
    assert "copy" in cmd_1080_same


