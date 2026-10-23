"""
Integration Test for FFmpeg Execution & Progress Callback
"""

import pytest
import subprocess
from pathlib import Path
from core.engine import run_ffmpeg_with_progress


@pytest.fixture(scope="module")
def input_video(tmp_path_factory) -> Path:
    tmp_dir = tmp_path_factory.mktemp("engine_test")
    src = tmp_dir / "src.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=2:size=320x240:rate=25",
        "-c:v", "libx264", "-pix_fmt", "yuv420p",
        str(src)
    ]
    res = subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    assert res.returncode == 0
    return src


@pytest.mark.asyncio
async def test_run_ffmpeg_with_progress(input_video: Path, tmp_path: Path):
    out_video = tmp_path / "out.mp4"
    progress_records = []

    async def on_progress(percent: float, speed: str, eta: int, fps: float):
        progress_records.append(percent)

    cmd = [
        "-i", str(input_video),
        "-c:v", "libx264",
        "-preset", "ultrafast",
        str(out_video)
    ]

    await run_ffmpeg_with_progress(
        cmd=cmd,
        total_duration_seconds=2.0,
        progress_callback=on_progress,
        progress_interval=0.1
    )

    assert out_video.exists()
    assert out_video.stat().st_size > 0
    # Must have recorded progress
    assert len(progress_records) > 0
    assert progress_records[-1] == 100.0
