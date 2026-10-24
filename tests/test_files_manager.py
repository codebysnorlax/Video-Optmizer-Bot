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


def test_task_cancellation_keyboards():
    from bot.keyboards import (
        build_task_progress_menu,
        build_confirm_stop_task_menu,
        build_stopped_task_menu
    )
    # 1. Progress stop button
    prog_menu = build_task_progress_menu("task_abc")
    assert len(prog_menu) == 1
    assert prog_menu[0][0].text == "Stop / Cancel"
    assert prog_menu[0][0].type.data == b"stop_task:task_abc"

    # 2. 2-Step Confirmation buttons
    confirm_menu = build_confirm_stop_task_menu("task_abc")
    assert len(confirm_menu) == 1
    assert len(confirm_menu[0]) == 2
    assert confirm_menu[0][0].text == "Yes, 100% Sure - Stop Process"
    assert confirm_menu[0][0].type.data == b"yes_stop_task:task_abc"
    assert confirm_menu[0][1].text == "No, Continue Processing"
    assert confirm_menu[0][1].type.data == b"no_stop_task:task_abc"

    # 3. Post-stop recovery menu
    stopped_menu = build_stopped_task_menu("task_abc")
    assert len(stopped_menu) == 3
    assert stopped_menu[0][0].text == "1. Try Another Optimization / Method"
    assert stopped_menu[0][0].type.data == b"retry_opt:task_abc"
    assert stopped_menu[1][0].text == "2. Delete File from Server"
    assert stopped_menu[1][0].type.data == b"del_orig:task_abc"
    assert stopped_menu[2][0].text == "< Main Menu"
    assert stopped_menu[2][0].type.data == b"main_menu:task_abc"


@pytest.mark.asyncio
async def test_task_stop_callbacks(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock, MagicMock
    from bot.callbacks import handle_callback_query
    from bot.task_manager import task_manager, TaskContext
    from core.probe import MediaMetadata
    from config.settings import settings

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "outputs")
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    dummy_input = tmp_path / "task_stop_test.mp4"
    dummy_input.write_bytes(b"dummy_video_bytes")
    dummy_output = settings.OUTPUT_DIR / "task_stop_opt.mp4"
    dummy_output.write_bytes(b"partial_output_bytes")

    meta = MediaMetadata(
        file_path=dummy_input,
        file_name="task_stop_test.mp4",
        file_size_bytes=1048576,
        duration_seconds=30.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=5000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=192,
        overall_bitrate_kbps=5192,
        is_high_bitrate=False,
        raw_info={}
    )

    mock_proc = MagicMock()
    mock_async_task = MagicMock()
    mock_async_task.done.return_value = False

    task_ctx = TaskContext(
        task_id="task_stop_test",
        user_id=12345,
        input_path=dummy_input,
        output_path=dummy_output,
        meta=meta,
        status_message=MagicMock(),
        original_message_id=1,
        t_received=100.0,
        current_stage="encoding",
        active_stage_label="Optimizing (Auto Recommended)",
        current_process=mock_proc,
        active_async_task=mock_async_task
    )
    task_manager.register_task(task_ctx)

    # 1. User clicks stop_task
    mock_event = MagicMock()
    mock_event.data = b"stop_task:task_stop_test"
    mock_event.edit = AsyncMock()
    mock_event.answer = AsyncMock()

    await handle_callback_query(mock_event, client=MagicMock())
    assert task_ctx.is_confirming_stop is True
    # Verify 100% sure prompt is displayed
    edit_call_args = mock_event.edit.call_args[0][0]
    assert "Confirm Stop Active Process" in edit_call_args
    assert "100% sure" in edit_call_args

    # 2. User clicks no_stop_task (continue)
    mock_event.data = b"no_stop_task:task_stop_test"
    await handle_callback_query(mock_event, client=MagicMock())
    assert task_ctx.is_confirming_stop is False
    edit_resume = mock_event.edit.call_args[0][0]
    assert "Resuming processing" in edit_resume

    # 3. User clicks yes_stop_task (confirmed 100% stop)
    mock_event.data = b"yes_stop_task:task_stop_test"
    await handle_callback_query(mock_event, client=MagicMock())
    # Subprocess was killed
    mock_proc.kill.assert_called_once()
    # Async task was cancelled
    mock_async_task.cancel.assert_called_once()
    # Partial output was deleted
    assert not dummy_output.exists()
    # Original input is kept
    assert dummy_input.exists()

    # Message showed full original file details
    edit_stop_text = mock_event.edit.call_args[0][0]
    assert "Process Stopped by User" in edit_stop_text
    assert "Original Source File Details:" in edit_stop_text
    assert "task_stop_test.mp4" in edit_stop_text
    assert "1920x1080" in edit_stop_text
    assert "30.00 fps" in edit_stop_text
    assert "H264" in edit_stop_text
    assert "AAC" in edit_stop_text
    assert "What would you like to do next?" in edit_stop_text

