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

    from bot.keyboards import build_cleanup_original_menu, build_cleanup_output_menu, build_cleanup_finished_menu
    clean_orig = build_cleanup_original_menu("task_abc")
    assert any(b"retry_opt:task_abc" in btn.type.data for row in clean_orig for btn in row)
    assert any(b"done_cleanup:task_abc" in btn.type.data for row in clean_orig for btn in row)

    clean_out = build_cleanup_output_menu("task_abc")
    assert any(b"retry_opt:task_abc" in btn.type.data for row in clean_out for btn in row)
    assert any(b"done_cleanup:task_abc" in btn.type.data for row in clean_out for btn in row)

    clean_fin = build_cleanup_finished_menu("task_abc")
    assert any(b"retry_opt:task_abc" in btn.type.data for row in clean_fin for btn in row)
    assert any(b"done_cleanup:task_abc" in btn.type.data for row in clean_fin for btn in row)

    from bot.callbacks import extract_report_header
    dummy_report = (
        "**Auto Recommended Complete!**\n"
        "• Quality: 1080p FHD\n"
        "• Duration: 00:30\n\n"
        "Step 1 of 2: Original File Cleanup\n"
        "• Original Source File: test.mp4"
    )
    extracted = extract_report_header(dummy_report)
    assert "**Auto Recommended Complete!**" in extracted
    assert "• Quality: 1080p FHD" in extracted
    assert "Step 1 of 2" not in extracted


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
    # Verify 100% sure prompt is displayed with size and quality details
    edit_call_args = mock_event.edit.call_args[0][0]
    assert "Confirm Stop Active Process" in edit_call_args
    assert "100% sure" in edit_call_args
    assert "File & Quality Details:" in edit_call_args
    assert "1080p FHD" in edit_call_args
    assert "Source Size:" in edit_call_args
    assert "Codecs:" in edit_call_args

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


@pytest.mark.asyncio
async def test_recall_stop_callbacks(tmp_path):
    from unittest.mock import AsyncMock, MagicMock
    from bot.callbacks import handle_callback_query
    from bot.task_manager import task_manager, RecallContext
    import subprocess

    dummy_video = tmp_path / "recall_test.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=1280x720:rate=30",
        "-c:v", "libx264", str(dummy_video)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)

    mock_upload_task = MagicMock()
    mock_upload_task.done.return_value = False

    recall_ctx = RecallContext(
        recall_id="rec_test123",
        user_id=12345,
        task_id="task_rec",
        file_type="processed",
        file_path=dummy_video,
        file_name="recall_test.mp4",
        file_size=dummy_video.stat().st_size,
        status_msg=MagicMock(),
        upload_task=mock_upload_task
    )
    task_manager.register_recall(recall_ctx)

    # 1. User taps stop_rec
    mock_event = MagicMock()
    mock_event.data = b"stop_rec:rec_test123"
    mock_event.edit = AsyncMock()
    mock_event.answer = AsyncMock()

    await handle_callback_query(mock_event, client=MagicMock())
    assert recall_ctx.is_confirming_stop is True
    confirm_text = mock_event.edit.call_args[0][0]
    assert "Confirm Stop Sending" in confirm_text
    assert "File & Quality Details:" in confirm_text
    assert "Quality / Resolution:" in confirm_text
    assert "720p HD" in confirm_text
    assert "File Size:" in confirm_text

    # 2. User taps yes_stop
    mock_event.data = b"yes_stop:rec_test123"
    await handle_callback_query(mock_event, client=MagicMock())
    mock_upload_task.cancel.assert_called_once()
    assert recall_ctx.is_cancelled is True


@pytest.mark.asyncio
async def test_format_queue_status():
    from bot.progress import format_queue_status
    text = format_queue_status(
        position=2,
        action_label="Auto Recommended",
        file_name="holiday_clip.mp4",
        active_jobs=1,
        total_queued=3
    )
    assert "**Server is Busy — Task Queued**" in text
    assert "• **Position in Queue:** `#2`" in text
    assert "• **Active Processing Jobs:** `1`" in text
    assert "• **Total Waiting in Queue:** `3`" in text
    assert "• **Requested Action:** `Auto Recommended`" in text
    assert "• **File Name:** `holiday_clip.mp4`" in text


@pytest.mark.asyncio
async def test_encoding_queue_concurrency_and_position_advancement():
    import asyncio
    from bot.task_manager import TaskManager, TaskContext
    from unittest.mock import AsyncMock, MagicMock
    from bot.progress import ThrottledMessageUpdater

    tm = TaskManager(max_concurrent=1)

    t1_status = MagicMock()
    t1_status.edit = AsyncMock()
    t1_updater = ThrottledMessageUpdater(t1_status, interval=0.01)
    t1_ctx = TaskContext(
        task_id="t1",
        user_id=1,
        input_path=Path("/tmp/t1.mp4"),
        output_path=None,
        meta=None,
        status_message=t1_status,
        original_message_id=101
    )
    tm.register_task(t1_ctx)

    t2_status = MagicMock()
    t2_status.edit = AsyncMock()
    t2_updater = ThrottledMessageUpdater(t2_status, interval=0.01)
    t2_ctx = TaskContext(
        task_id="t2",
        user_id=2,
        input_path=Path("/tmp/t2.mp4"),
        output_path=None,
        meta=None,
        status_message=t2_status,
        original_message_id=102
    )
    tm.register_task(t2_ctx)

    t3_status = MagicMock()
    t3_status.edit = AsyncMock()
    t3_updater = ThrottledMessageUpdater(t3_status, interval=0.01)
    t3_ctx = TaskContext(
        task_id="t3",
        user_id=3,
        input_path=Path("/tmp/t3.mp4"),
        output_path=None,
        meta=None,
        status_message=t3_status,
        original_message_id=103
    )
    tm.register_task(t3_ctx)

    t1_acquired = asyncio.Event()
    t1_release = asyncio.Event()
    t2_acquired = asyncio.Event()
    t2_release = asyncio.Event()
    t3_acquired = asyncio.Event()
    t3_release = asyncio.Event()

    async def run_t1():
        async with tm.acquire_encoding_slot("t1", updater=t1_updater, mode_label="Auto Recommended", file_name="t1.mp4"):
            t1_acquired.set()
            await t1_release.wait()

    async def run_t2():
        async with tm.acquire_encoding_slot("t2", updater=t2_updater, mode_label="720p HD", file_name="t2.mp4"):
            t2_acquired.set()
            await t2_release.wait()

    async def run_t3():
        async with tm.acquire_encoding_slot("t3", updater=t3_updater, mode_label="1080p FHD", file_name="t3.mp4"):
            t3_acquired.set()
            await t3_release.wait()

    # Step 1: Task 1 acquires slot
    task1 = asyncio.create_task(run_t1())
    await t1_acquired.wait()
    assert tm.get_queue_position("t1") == 0  # Active
    assert tm.get_active_encoder_count() == 1
    assert tm.get_queue_length() == 0

    # Step 2: Task 2 attempts to acquire slot while Task 1 is transcoding
    task2 = asyncio.create_task(run_t2())
    await asyncio.sleep(0.05)
    assert tm.get_queue_position("t2") == 1  # In queue at Position #1
    assert tm.get_queue_length() == 1
    assert t2_status.edit.called
    last_t2_msg = t2_status.edit.call_args[0][0]
    assert "Position in Queue:** `#1`" in last_t2_msg

    # Step 3: Task 3 attempts to acquire slot while Task 1 is still transcoding
    task3 = asyncio.create_task(run_t3())
    await asyncio.sleep(0.05)
    assert tm.get_queue_position("t3") == 2  # In queue at Position #2
    assert tm.get_queue_length() == 2
    assert t3_status.edit.called
    last_t3_msg = t3_status.edit.call_args[0][0]
    assert "Position in Queue:** `#2`" in last_t3_msg

    # Step 4: Task 1 finishes transcoding and releases slot
    t1_release.set()
    await task1
    await t2_acquired.wait()  # Task 2 immediately acquires the slot!
    await asyncio.sleep(0.05)

    assert tm.get_queue_position("t2") == 0  # Task 2 is now active
    assert tm.get_queue_position("t3") == 1  # Task 3 advanced from #2 to #1!
    assert tm.get_queue_length() == 1

    # Task 3's message should have been automatically updated to Position #1
    last_t3_msg = t3_status.edit.call_args[0][0]
    assert "Position in Queue:** `#1`" in last_t3_msg

    # Step 5: Task 2 finishes transcoding
    t2_release.set()
    await task2
    await t3_acquired.wait()  # Task 3 acquires slot!
    await asyncio.sleep(0.05)
    assert tm.get_queue_position("t3") == 0
    assert tm.get_queue_length() == 0

    # Step 6: Task 3 finishes
    t3_release.set()
    await task3
    assert tm.get_active_encoder_count() == 0
    assert tm.get_queue_length() == 0


@pytest.mark.asyncio
async def test_encoding_queue_cancellation():
    import asyncio
    from bot.task_manager import TaskManager, TaskContext
    from unittest.mock import MagicMock

    tm = TaskManager(max_concurrent=1)

    t1_ctx = TaskContext("t1", 1, Path("/tmp/t1.mp4"), None, None, MagicMock(), 101)
    t2_ctx = TaskContext("t2", 2, Path("/tmp/t2.mp4"), None, None, MagicMock(), 102)
    t3_ctx = TaskContext("t3", 3, Path("/tmp/t3.mp4"), None, None, MagicMock(), 103)
    tm.register_task(t1_ctx)
    tm.register_task(t2_ctx)
    tm.register_task(t3_ctx)

    t1_acquired = asyncio.Event()
    t1_release = asyncio.Event()

    async def run_t1():
        async with tm.acquire_encoding_slot("t1"):
            t1_acquired.set()
            await t1_release.wait()

    async def run_t2():
        async with tm.acquire_encoding_slot("t2"):
            pass

    async def run_t3():
        async with tm.acquire_encoding_slot("t3"):
            pass

    task1 = asyncio.create_task(run_t1())
    await t1_acquired.wait()

    task2 = asyncio.create_task(run_t2())
    task3 = asyncio.create_task(run_t3())
    await asyncio.sleep(0.05)

    assert tm.get_queue_position("t2") == 1
    assert tm.get_queue_position("t3") == 2

    # User cancels Task 2 while waiting in queue
    task2.cancel()
    await asyncio.sleep(0.05)

    assert tm.get_queue_position("t2") is None
    assert tm.get_queue_position("t3") == 1  # Task 3 automatically promoted to #1

    t1_release.set()
    await task1
    await task3
    assert tm.get_queue_length() == 0
    assert tm.get_active_encoder_count() == 0


@pytest.mark.asyncio
async def test_upload_runs_outside_encoding_slot(tmp_path, monkeypatch):
    import asyncio
    from bot.callbacks import process_optimization
    from bot.task_manager import task_manager, TaskContext
    from core.probe import MediaMetadata
    from config.settings import settings
    from unittest.mock import AsyncMock, MagicMock

    monkeypatch.setattr(settings, "DOWNLOAD_DIR", tmp_path)
    monkeypatch.setattr(settings, "OUTPUT_DIR", tmp_path / "outputs")
    settings.OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    import subprocess
    from core.probe import probe_media
    dummy_video = tmp_path / "test.mp4"
    cmd = [
        "ffmpeg", "-y",
        "-f", "lavfi", "-i", "testsrc=duration=1:size=640x360:rate=24",
        "-f", "lavfi", "-i", "sine=frequency=1000:duration=1",
        "-c:v", "libx264", "-c:a", "aac",
        str(dummy_video)
    ]
    subprocess.run(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True)
    meta = await probe_media(dummy_video)

    upload_in_progress = asyncio.Event()
    release_upload = asyncio.Event()

    async def mock_send_file(*args, **kwargs):
        upload_in_progress.set()
        await release_upload.wait()
        msg = MagicMock()
        msg.id = 999
        return msg

    client = MagicMock()
    client.send_file = mock_send_file
    client.send_message = AsyncMock()

    status1 = MagicMock()
    status1.edit = AsyncMock()
    status1.delete = AsyncMock()

    ctx1 = TaskContext(
        task_id="task_upload1",
        user_id=101,
        input_path=dummy_video,
        output_path=None,
        meta=meta,
        status_message=status1,
        original_message_id=1
    )
    task_manager.register_task(ctx1)

    # Mock FFmpeg to create dummy output immediately
    async def mock_run_ffmpeg(*args, **kwargs):
        out_file = settings.OUTPUT_DIR / f"{ctx1.task_id}_opt_auto_test.mp4"
        out_file.write_bytes(b"dummy_optimized")
        if kwargs.get("on_process_started"):
            kwargs["on_process_started"](None)

    monkeypatch.setattr("bot.callbacks.run_ffmpeg_with_progress", mock_run_ffmpeg)
    monkeypatch.setattr("bot.callbacks.probe_media", AsyncMock(return_value=meta))

    t1 = asyncio.create_task(process_optimization(ctx1, "auto", client))

    # Wait until Task 1 reaches upload stage
    await upload_in_progress.wait()
    assert ctx1.current_stage == "uploading"

    # Verify that while Task 1 is uploading, the encoding slot is FREE!
    assert task_manager.get_active_encoder_count() == 0

    # Task 2 should be able to acquire encoding slot immediately!
    slot2_acquired = False
    async with task_manager.acquire_encoding_slot("task_upload2"):
        slot2_acquired = True

    assert slot2_acquired is True

    # Now release upload and let Task 1 complete cleanly
    release_upload.set()
    await t1
    task_manager.remove_task("task_upload1")



