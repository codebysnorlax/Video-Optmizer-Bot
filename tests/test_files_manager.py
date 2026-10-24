"""
Unit Tests for Files Manager, Recall System, and Keyboards
"""

import pytest
from pathlib import Path
from bot.keyboards import (
    build_main_menu,
    build_optimize_menu,
    build_files_list_menu,
    build_file_action_menu,
    build_recall_progress_menu,
    build_confirm_stop_menu
)
from bot.task_manager import task_manager, RecallContext


def test_files_keyboards():
    dummy_files = [
        {"task_id": "task1", "file_type": "original", "name": "sample_video.mp4", "size": 10485760},
        {"task_id": "task1", "file_type": "processed", "name": "opt_sample_video.mp4", "size": 2097152},
        {"task_id": "task2", "file_type": "original", "name": "camera_clip.mov", "size": 52428800},
    ]

    menu = build_files_list_menu(dummy_files, page=0, page_size=2)
    assert len(menu) == 4  # 2 file buttons + 1 nav row (Next) + 1 refresh button
    assert b"fview:original:task1" in menu[0][0].type.data
    assert b"fview:processed:task1" in menu[1][0].type.data
    assert b"flist:1" in menu[2][0].type.data  # Next button

    action_menu = build_file_action_menu("task1", "processed")
    assert any(b"recall:processed:task1" in btn.type.data for row in action_menu for btn in row)
    assert any(b"del_f:processed:task1" in btn.type.data for row in action_menu for btn in row)

    rec_menu = build_recall_progress_menu("rec123")
    assert any(b"stop_rec:rec123" in btn.type.data for row in rec_menu for btn in row)

    stop_menu = build_confirm_stop_menu("rec123")
    assert any(b"yes_stop:rec123" in btn.type.data for row in stop_menu for btn in row)
    assert any(b"no_stop:rec123" in btn.type.data for row in stop_menu for btn in row)


def test_optimize_menu():
    menu = build_optimize_menu("task_abc")
    assert len(menu) == 4  # 3 options + back button
    assert b"opt_sub:720:task_abc" in menu[0][0].type.data
    assert b"opt_sub:1080:task_abc" in menu[1][0].type.data
    assert b"opt_run:auto:task_abc" in menu[2][0].type.data
    assert b"main_menu:task_abc" in menu[3][0].type.data

    from bot.keyboards import build_bitrate_choice_menu, build_auto_recommended_menu
    # 720p choice menu
    menu_720 = build_bitrate_choice_menu("720", "task_abc")
    assert len(menu_720) == 3
    assert b"opt_run:720_same:task_abc" in menu_720[0][0].type.data
    assert b"opt_run:720_less:task_abc" in menu_720[1][0].type.data
    assert b"opt_menu:task_abc" in menu_720[2][0].type.data

    # 1080p choice menu
    menu_1080 = build_bitrate_choice_menu("1080", "task_abc")
    assert len(menu_1080) == 3
    assert b"opt_run:1080_same:task_abc" in menu_1080[0][0].type.data
    assert b"opt_run:1080_less:task_abc" in menu_1080[1][0].type.data
    assert b"opt_menu:task_abc" in menu_1080[2][0].type.data

    # Auto recommended menu
    auto_menu = build_auto_recommended_menu("task_abc")
    assert len(auto_menu) == 3
    assert b"opt_run:auto_same:task_abc" in auto_menu[0][0].type.data
    assert b"opt_run:auto_less:task_abc" in auto_menu[1][0].type.data
    assert b"opt_menu:task_abc" in auto_menu[2][0].type.data

    from bot.keyboards import build_cleanup_original_menu, build_cleanup_output_menu
    clean_orig = build_cleanup_original_menu("task_abc")
    assert any(b"retry_opt:task_abc" in btn.type.data for row in clean_orig for btn in row)

    clean_out = build_cleanup_output_menu("task_abc")
    assert any(b"retry_opt:task_abc" in btn.type.data for row in clean_out for btn in row)


def test_recall_task_manager():
    ctx = RecallContext(
        recall_id="rec_abc",
        user_id=12345,
        task_id="task_999",
        file_type="processed",
        file_path=Path("/tmp/fake.mp4"),
        file_name="fake.mp4",
        file_size=5000,
        status_msg=None
    )
    task_manager.register_recall(ctx)
    assert task_manager.get_recall("rec_abc") is ctx
    assert task_manager.get_recall("nonexistent") is None

    # Test confirmation flag
    ctx.is_confirming_stop = True
    assert task_manager.get_recall("rec_abc").is_confirming_stop is True

    # Test removal
    removed = task_manager.remove_recall("rec_abc")
    assert removed is ctx
    assert task_manager.get_recall("rec_abc") is None


def test_find_existing_server_file(tmp_path, monkeypatch):
    from bot.media_handler import find_existing_server_file
    from config.settings import settings

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path)
    sample_file = tmp_path / "task123_video_sample.mp4"
    sample_file.write_bytes(b"A" * 1024)

    # 1. Matching filename suffix and size
    res = find_existing_server_file("video_sample.mp4", 1024)
    assert res == sample_file

    # 2. Mismatched size
    res_wrong_size = find_existing_server_file("video_sample.mp4", 2048)
    assert res_wrong_size is None

    # 3. Nonexistent file
    res_nonexistent = find_existing_server_file("other_video.mp4", 1024)
    assert res_nonexistent is None


@pytest.mark.asyncio
async def test_get_or_restore_task_ctx(tmp_path, monkeypatch):
    from bot.callbacks import get_or_restore_task_ctx
    from database.models import register_file_cache
    from unittest.mock import AsyncMock, MagicMock
    from config.settings import settings

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "outputs")
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    orig_video = tmp_path / "task_xyz_original.mp4"
    import subprocess
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
        "-c:v", "libx264", "-c:a", "aac",
        str(orig_video)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    opt_video = settings.OUTPUT_DIR / "task_xyz_opt_low.mp4"
    opt_video.write_bytes(b"dummy_output")

    # Register in DB cache
    await register_file_cache(
        task_id="task_xyz",
        user_id=12345,
        input_path=str(orig_video),
        output_path=str(opt_video)
    )

    # Ensure memory task is clear
    task_manager.remove_task("task_xyz")

    # Mock callback event
    mock_event = MagicMock()
    mock_event.sender_id = 12345
    mock_event.get_message = AsyncMock(return_value=MagicMock())

    # Call get_or_restore_task_ctx
    restored_ctx = await get_or_restore_task_ctx("task_xyz", mock_event)
    assert restored_ctx is not None
    assert restored_ctx.task_id == "task_xyz"
    # CRITICAL: input_path must strictly be the original video, NEVER the output
    assert restored_ctx.input_path == orig_video
    assert restored_ctx.output_path is None
    assert restored_ctx.meta.width == 640
    assert restored_ctx.meta.height == 360


@pytest.mark.asyncio
async def test_start_dashboard():
    from bot.handlers import build_start_dashboard
    text, buttons = await build_start_dashboard(user_id=99999, user_name="Alex")
    assert "Video Optimizer & Converter Dashboard" in text
    assert "Alex" in text
    assert "1. Convert to 720p" in text
    assert "2. Convert to 1080p" in text
    assert "3. Auto Recommended" in text
    assert len(buttons) == 2
    assert b"flist:0" in buttons[0][0].type.data
    assert b"dash_stats" in buttons[0][1].type.data
    assert b"dash_hw" in buttons[1][0].type.data
    assert b"dash_help" in buttons[1][1].type.data

