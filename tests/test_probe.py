"""
Unit Tests for Video Probing & Analysis
"""

import pytest
import subprocess
from pathlib import Path
from core.probe import probe_media, MediaMetadata, format_bytes, format_duration


@pytest.fixture(scope="module")
def sample_video(tmp_path_factory) -> Path:
    """Generates a synthetic 1-second video with audio for testing."""
    tmp_dir = tmp_path_factory.mktemp("media")
    video_path = tmp_dir / "sample_test.mp4"
    
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=25",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-c:a", "aac",
        str(video_path)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode == 0
    return video_path


@pytest.mark.asyncio
async def test_probe_media(sample_video: Path):
    meta = await probe_media(sample_video)
    assert isinstance(meta, MediaMetadata)
    assert meta.has_video is True
    assert meta.width == 640
    assert meta.height == 360
    assert meta.video_codec == "h264"
    assert meta.has_audio is True
    assert meta.duration_seconds >= 0.9
    assert meta.resolution_label == "640x360"

    card = meta.get_summary_card(hardware_summary="Test Engine")
    assert "Video Analysis Report" in card
    assert "sample_test.mp4" in card
    assert "H.264" in card


def test_format_helpers():
    assert format_bytes(500) == "500 B"
    assert format_bytes(1024 * 500) == "500.0 KB"
    assert format_bytes(1024 * 1024 * 25) == "25.0 MB"
    assert format_bytes(1024 * 1024 * 1024 * 2) == "2.00 GB"

    assert format_duration(45) == "00:45"
    assert format_duration(125) == "02:05"
    assert format_duration(3665) == "01:01:05"


def test_format_time_breakdown():
    from bot.progress import format_time_breakdown
    res = format_time_breakdown(
        download_time=4.2,
        probe_time=0.3,
        transcode_time=11.5,
        upload_time=5.1,
        total_pipeline_time=21.1,
        total_overall_time=32.6
    )
    assert "*Time Details:*" in res
    assert "Receive & Download: `4.2s`" in res
    assert "Media Analysis: `0.3s`" in res
    assert "Transcode / Processing: `11.5s`" in res
    assert "Upload to Telegram: `5.1s`" in res
    assert "Total Active Pipeline: `21.1s`" in res
    assert "Total Overall Time: `32.6s`" in res


def test_format_before_after_analysis(sample_video: Path):
    from core.probe import format_before_after_analysis
    meta1 = MediaMetadata(
        file_path=sample_video,
        file_name="before.mp4",
        file_size_bytes=10000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=3840,
        height=2160,
        fps=30.0,
        video_bitrate_kbps=7500,
        has_audio=True,
        audio_codec="ac3",
        audio_channels=6,
        audio_sample_rate=48000,
        audio_bitrate_kbps=384,
        overall_bitrate_kbps=8000,
        is_high_bitrate=True,
        raw_info={}
    )
    meta2 = MediaMetadata(
        file_path=sample_video,
        file_name="after.mp4",
        file_size_bytes=4000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=3000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=192,
        overall_bitrate_kbps=3200,
        is_high_bitrate=False,
        raw_info={}
    )
    res = format_before_after_analysis(meta1, meta2)
    assert "*Before & After Media Analysis:*" in res
    assert "HEVC` -> `H264`" in res
    assert "AC3 (6ch)` -> `AAC (2ch)`" in res
    assert "60.0% Saved" in res


