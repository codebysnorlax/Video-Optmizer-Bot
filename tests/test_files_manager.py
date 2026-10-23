"""
Unit Tests for Files Manager, Recall System, and Keyboards
"""

import pytest
from pathlib import Path
from bot.keyboards import (
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
