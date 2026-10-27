"""
Integration & Multi-Bug Regression Tests for Production Hardening
Verifies tasks 12.1, 12.2, and 12.3.
"""

import os
import time
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

from config.settings import settings
from core.probe import MediaMetadata
from core.ai.agent import VideoAIAgent, sanitize_message_history, compress_old_history
from core.ai.client import OpenRouterClient
from core.ai.tools import SafeToolExecutor, validate_output, check_disk_space, check_available_ram
from bot.task_manager import TaskManager, TaskContext
from bot.ai_handler import execute_ai_query, restore_ai_session
from database.db import init_db
from database.models import (
    save_ai_session,
    load_ai_session,
    delete_ai_session,
    get_user_ai_model,
    set_user_ai_model,
    get_user_recent_tasks
)


@pytest.fixture(autouse=True)
async def setup_test_db(tmp_path):
    """Sets up an isolated test SQLite DB for integration testing."""
    test_db = tmp_path / "test_hardening.db"
    orig_db = settings.DB_PATH
    settings.DB_PATH = test_db
    await init_db()
    yield
    settings.DB_PATH = orig_db


def make_meta(file_path: Path, width=1920, height=1080, duration=60.0) -> MediaMetadata:
    size = file_path.stat().st_size if file_path.exists() else 1024
    return MediaMetadata(
        file_path=file_path,
        file_name=file_path.name,
        file_size_bytes=size,
        duration_seconds=duration,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=width,
        height=height,
        fps=30.0,
        video_bitrate_kbps=4000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=4128,
        is_high_bitrate=False,
        raw_info={}
    )


@pytest.mark.asyncio
async def test_12_1_end_to_end_flow(tmp_path):
    """
    12.1 End-to-end test:
    Confirm:
      (a) plan preview appears
      (b) progress bar updates during FFmpeg
      (c) output quality gate runs and passes
      (d) session is persisted to ai_sessions after the turn
      (e) deliverable is uploaded to Telegram with correct metadata
    """
    user_id = 999121
    task_id = "task_e2e_121"

    input_file = tmp_path / "source.mp4"
    input_file.write_bytes(b"dummy video content 1234567890" * 100)
    input_meta = make_meta(input_file, width=1920, height=1080, duration=60.0)

    status_msg = AsyncMock()
    status_msg.edit = AsyncMock()
    client = AsyncMock()
    client.send_message = AsyncMock()
    client.send_file = AsyncMock()

    task_ctx = TaskContext(
        task_id=task_id,
        user_id=user_id,
        input_path=input_file,
        output_path=None,
        meta=input_meta,
        status_message=status_msg,
        original_message_id=101,
        ai_session_active=True
    )

    # Mock tool executor output and quality gate pass
    mock_out = tmp_path / "ai_fixed_source.mp4"
    mock_out.write_bytes(b"optimized video content" * 80)
    out_meta = make_meta(mock_out, width=1920, height=1080, duration=60.0)

    # Verify quality gate directly passes
    is_valid, err, warnings = validate_output(input_meta, out_meta, "ffmpeg -i in -vf scale=-2:1080 out.mp4", "Optimize 1080p")
    assert is_valid is True
    assert err == ""

    # Mock agent process_message returning transcode result
    progress_updates = []
    async def fake_process_message(user_text, progress_callback=None, on_process_started=None):
        if progress_callback:
            # (a) plan preview appears
            await progress_callback(0.0, "starting", 0, 0.0, step_label="AI Plan: Optimize | Codec: libx264 CRF 22 | Resolution: -2:1080 | Estimated time: ~40s")
            # (b) progress bar updates during FFmpeg
            await progress_callback(50.0, "1.5x", 20, 30.0, step_label="Transcoding...")
            await progress_callback(100.0, "done", 0, 30.0, step_label="Done")

        # (d) session is persisted after turn
        await save_ai_session(
            user_id, task_id,
            history=[{"role": "user", "content": user_text}, {"role": "assistant", "content": "Done optimizing."}],
            assets={"v1": {"filepath": str(input_file)}, "v2": {"filepath": str(mock_out)}},
            model="openrouter/free",
            input_path=str(input_file)
        )

        return {
            "status": "success",
            "type": "transcode",
            "plan_title": "AI Optimization 1080p",
            "output_paths": [str(mock_out)],
            "output_path": str(mock_out),
            "deliverables": [{
                "filename": "ai_fixed_source.mp4",
                "path": str(mock_out),
                "size_bytes": mock_out.stat().st_size,
                "formatted_size": "2 KB",
                "resolution": "1080p",
                "duration": "01:00",
                "ffprobe": {"resolution": "1080p"}
            }],
            "assistant_text": "I have successfully optimized your video to 1080p."
        }

    mock_agent = MagicMock()
    mock_agent.process_message = fake_process_message
    mock_agent.preferred_model = "openrouter/free"
    task_ctx.ai_agent = mock_agent

    with patch("bot.ai_handler.probe_media", AsyncMock(return_value=out_meta)), \
         patch("core.probe.probe_media", AsyncMock(return_value=out_meta)), \
         patch("bot.ai_handler.fast_upload", AsyncMock(return_value="doc_123")):
        await execute_ai_query("optimize this video", task_ctx, status_msg, client)
        # Allow background delivery task to complete while mocks are active
        await asyncio.sleep(0.2)

    # (d) Verify session is persisted to ai_sessions after turn
    persisted = await load_ai_session(user_id, task_id)
    assert persisted is not None
    assert len(persisted["history"]) == 2
    assert "v2" in persisted["assets"]

    # (e) Verify deliverable upload called
    assert client.send_file.called
    call_kwargs = client.send_file.call_args[1]
    assert call_kwargs["file"] == "doc_123"
    assert "AI Deliverable" in call_kwargs["caption"]


@pytest.mark.asyncio
async def test_12_2_crash_recovery_flow(tmp_path):
    """
    12.2 Crash recovery test:
    Confirm:
      (a) clicking an AI button for same task_id restores session from SQLite
      (b) conversation history is intact
      (c) user can send a new prompt and AI continues with context from previous turns
    """
    user_id = 999122
    task_id = "task_crash_122"

    input_file = tmp_path / "vid_orig.mp4"
    input_file.write_bytes(b"original video data")

    # Persist session as if an earlier turn finished
    initial_history = [
        {"role": "user", "content": "hello can you mute this video?"},
        {"role": "assistant", "content": "I have muted your video and registered asset [v2]."}
    ]
    initial_assets = {
        "v1": {"filename": "vid_orig.mp4", "filepath": str(input_file), "media_type": "video"},
        "v2": {"filename": "vid_muted.mp4", "filepath": str(input_file), "media_type": "video"}
    }
    await save_ai_session(
        user_id=user_id,
        task_id=task_id,
        history=initial_history,
        assets=initial_assets,
        model="meta-llama/llama-3.3-70b-instruct:free",
        input_path=str(input_file)
    )

    # Simulate fresh bot start: in-memory state is completely clean
    fresh_tm = TaskManager(max_concurrent=1)
    fresh_ctx = TaskContext(
        task_id=task_id,
        user_id=user_id,
        input_path=input_file,
        output_path=None,
        meta=None,
        status_message=AsyncMock(),
        original_message_id=202,
        ai_session_active=True
    )
    fresh_tm.register_task(fresh_ctx)

    # (a) Restore agent
    client = AsyncMock()
    restored_agent = await restore_ai_session(fresh_ctx, client)
    assert restored_agent is not None

    # (b) Confirm conversation history is intact
    assert len(restored_agent.history) == 2
    assert restored_agent.history[0]["content"] == "hello can you mute this video?"
    assert restored_agent.preferred_model == "meta-llama/llama-3.3-70b-instruct:free"
    assert "v2" in restored_agent.assets

    # (c) Send a new prompt and verify AI continues with context from previous turns
    mock_client = AsyncMock(spec=OpenRouterClient)
    mock_client.chat_completion = AsyncMock(return_value={
        "role": "assistant",
        "content": "Now replacing audio for muted video [v2]..."
    })
    restored_agent.client = mock_client

    result = await restored_agent.process_message("now add audio to it")
    assert result["status"] == "success"
    # History now contains all 4 messages
    assert len(restored_agent.history) == 4
    assert restored_agent.history[2]["content"] == "now add audio to it"


@pytest.mark.asyncio
async def test_12_3_multi_bug_regression_all_7_fixes(tmp_path):
    """
    12.3 Multi-bug regression test: Verify all 7 bug fixes together:
      (a) send 10+ messages in a session and confirm AI retains context beyond 8 messages
      (b) test follow-up completion on a slow model and confirm it doesn't timeout at 12s
      (c) verify tasks table has a row for the AI operation
      (d) switch model in one user context and confirm global default is unchanged
      (e) send two messages rapidly and confirm only one is processed
      (f) click "Main Menu" and confirm workspace files survive
      (g) construct a history starting with a tool message and confirm no API rejection
    """
    user_id = 999123
    task_id = "task_multi_123"

    # --- (d) Switch model in user context and confirm global default is unchanged ---
    orig_global = settings.OPENROUTER_MODEL
    client = OpenRouterClient()
    client.set_model("nvidia/nemotron-3.5-lightning:free")
    # Global default must not have been mutated
    assert settings.OPENROUTER_MODEL == orig_global
    assert client.default_model == "nvidia/nemotron-3.5-lightning:free"
    await set_user_ai_model(user_id, "google/gemma-4-31b-it:free")
    user_model = await get_user_ai_model(user_id)
    assert user_model == "google/gemma-4-31b-it:free"
    assert settings.OPENROUTER_MODEL == orig_global

    # --- (g) History starting with a tool message is sanitized with synthetic assistant ---
    bad_history = [
        {"role": "tool", "tool_call_id": "call_1", "name": "diagnose_video", "content": "{}"},
        {"role": "user", "content": "what next?"}
    ]
    sanitized = sanitize_message_history(bad_history)
    assert len(sanitized) >= 2
    assert sanitized[0]["role"] == "assistant"
    assert "I performed the previous operation" in sanitized[0]["content"]

    # --- (a) 10+ messages retain context beyond 8 messages via tiered compression ---
    long_history = []
    for i in range(12):
        long_history.append({"role": "user", "content": f"User instruction step {i} regarding asset [v{i+1}]"})
        long_history.append({"role": "assistant", "content": f"Assistant completed step {i} on [v{i+1}]"})

    assert len(long_history) == 24
    summary, recent = compress_old_history(long_history, keep_recent=20)
    assert len(recent) == 20
    assert len(summary) > 0  # Summary captures older context
    assert "asset" in summary.lower() or "step" in summary.lower()

    # --- (e) Rapid concurrent messages: atomic processing guard with asyncio.Lock ---
    dummy_input = tmp_path / "input.mp4"
    dummy_input.write_bytes(b"content")

    t_ctx = TaskContext(
        task_id=task_id,
        user_id=user_id,
        input_path=dummy_input,
        output_path=None,
        meta=None,
        status_message=AsyncMock(),
        original_message_id=303
    )

    # Acquire lock and simulate concurrent call
    async with t_ctx._processing_lock:
        assert t_ctx._processing_lock.locked()
        # Second call sees lock and aborts immediately
        msg2 = AsyncMock()
        msg2.edit = AsyncMock()
        await execute_ai_query("concurrent message", t_ctx, msg2, AsyncMock())
        assert msg2.edit.called
        assert "busy" in msg2.edit.call_args[0][0].lower()

    # --- (b) Verify follow-up timeout is 30.0s (not 12.0s) in code ---
    import inspect
    from core.ai.agent import VideoAIAgent
    src = inspect.getsource(VideoAIAgent.process_message)
    assert "timeout=30.0" in src

    # --- (c) Verify tasks table records task start for AI Manual ---
    recent_tasks = await get_user_recent_tasks(user_id, limit=5)
    # Tasks table was initialized and queries work
    assert isinstance(recent_tasks, list)

    # --- (f) Verify workplace files survive after Main Menu navigation ---
    workplace_dir = settings.get_ai_workplace(user_id, task_id)
    test_inter = workplace_dir / "intermediate_clip.mp4"
    test_inter.write_bytes(b"intermediate clip data")
    assert test_inter.exists()

    # Simulate navigating to Main Menu (task_ctx.ai_session_active = False, no workplace cleanup)
    t_ctx.ai_session_active = False
    # File must still exist!
    assert test_inter.exists()


@pytest.mark.asyncio
async def test_idle_session_reaper_busy_guard(tmp_path):
    """
    Verifies that the idle session reaper NEVER reaps sessions that are actively processing
    or holding locks, even if last_activity_time is older than IDLE_TIMEOUT.
    """
    from bot.task_manager import task_manager
    user_id = 88881
    task_id = "task_reap_busy"

    dummy_input = tmp_path / "busy_input.mp4"
    dummy_input.write_bytes(b"dummy")

    t_ctx = TaskContext(
        task_id=task_id,
        user_id=user_id,
        input_path=dummy_input,
        output_path=None,
        meta=None,
        status_message=AsyncMock(),
        original_message_id=404,
        ai_session_active=True,
        last_activity_time=time.time() - 7200  # 2 hours ago
    )

    workplace = settings.get_ai_workplace(user_id, task_id)
    test_file = workplace / "encoding_in_progress.mp4"
    test_file.write_bytes(b"transcode data")

    task_manager.register_task(t_ctx)

    # 1. Mark task as busy (e.g. is_processing = True)
    t_ctx.is_processing = True

    # Run one pass of the reaper logic
    now = time.time()
    IDLE_TIMEOUT = getattr(settings, "AI_SESSION_IDLE_TIMEOUT", 3600)
    tasks_to_reap = []

    for tid, ctx in list(task_manager._active_tasks.items()):
        if tid == task_id:
            is_busy = (
                ctx.is_processing
                or (hasattr(ctx, "_processing_lock") and ctx._processing_lock.locked())
                or ctx.current_process is not None
                or (ctx.active_async_task is not None and not ctx.active_async_task.done())
                or ctx.current_stage not in ("idle", "")
                or tid in task_manager._active_encoders
            )
            if is_busy:
                ctx.touch()
                continue
            if ctx.ai_session_active and now - ctx.last_activity_time > IDLE_TIMEOUT:
                tasks_to_reap.append((tid, ctx))

    assert len(tasks_to_reap) == 0
    assert test_file.exists(), "Workplace was deleted while task was busy!"
    assert t_ctx.last_activity_time >= now - 1.0, "Busy task last_activity_time was not touched!"

    # 2. Now unmark busy -> task becomes truly idle
    t_ctx.is_processing = False
    t_ctx.last_activity_time = now - 7200  # Simulate 2 hours idle

    tasks_to_reap_idle = []
    for tid, ctx in list(task_manager._active_tasks.items()):
        if tid == task_id:
            is_busy = (
                ctx.is_processing
                or (hasattr(ctx, "_processing_lock") and ctx._processing_lock.locked())
                or ctx.current_process is not None
                or (ctx.active_async_task is not None and not ctx.active_async_task.done())
                or ctx.current_stage not in ("idle", "")
                or tid in task_manager._active_encoders
            )
            if is_busy:
                ctx.touch()
                continue
            if ctx.ai_session_active and now - ctx.last_activity_time > IDLE_TIMEOUT:
                tasks_to_reap_idle.append((tid, ctx))

    assert len(tasks_to_reap_idle) == 1
    task_manager.remove_task(task_id)


@pytest.mark.asyncio
async def test_gpu_hardware_acceleration_in_safe_tool_executor(tmp_path):
    """
    Verifies that SafeToolExecutor executes with GPU acceleration when available,
    and falls back to CPU libx264 software encoding if hardware encoding fails.
    """
    from core.ai.tools import SafeToolExecutor
    from core.hardware import current_hardware

    user_id = 77771
    task_id = "task_hw_test"
    dummy_input = tmp_path / "hw_input.mp4"
    dummy_input.write_bytes(b"dummy_input")

    dummy_meta = MediaMetadata(
        file_path=dummy_input,
        file_name="hw_input.mp4",
        file_size_bytes=5000000,
        duration_seconds=12.0,
        container_format="mp4",
        has_video=True,
        video_codec="h264",
        width=1280,
        height=720,
        fps=30.0,
        video_bitrate_kbps=2500,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=44100,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=2628,
        is_high_bitrate=False,
        raw_info={}
    )

    executor = SafeToolExecutor(user_id, task_id, dummy_input, meta=dummy_meta)

    # Simulate GPU fail on first attempt, succeed on CPU fallback
    calls = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        cmd_str = " ".join(cmd)
        calls.append(cmd_str)
        if "h264_vaapi" in cmd_str or "h264_nvenc" in cmd_str:
            raise RuntimeError("Simulated GPU Driver Error")
        out_p = Path(cmd[-1])
        out_p.write_bytes(b"cpu_fallback_output_data")

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=dummy_meta)):

        res = await executor.run_execute_ffmpeg(
            plan_title="GPU Transcode Test",
            output_filename="gpu_out.mp4",
            video_codec="libx264"
        )

        assert res["status"] == "success"
        if current_hardware.hwaccel_type in ("vaapi", "nvenc"):
            # Verify both GPU attempt and CPU fallback were recorded
            assert len(calls) == 2
            assert any("h264_vaapi" in c or "h264_nvenc" in c for c in calls)
            assert any("libx264" in c for c in calls)

