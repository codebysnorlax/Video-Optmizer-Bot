"""
Unit and Integration Tests for AI Manual Mode
Tests SkillRegistry, OpenRouter Client, SafeToolExecutor, VideoAIAgent, and bot integration.
"""

import os
import json
import asyncio
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock

from config.settings import settings
from core.probe import MediaMetadata
from core.hardware import current_hardware
from core.ai.skills import skill_registry, SkillRegistry
from core.ai.client import OpenRouterClient
from core.ai.tools import SafeToolExecutor, AI_TOOLS_SCHEMA
from core.ai.agent import VideoAIAgent
from bot.keyboards import build_main_menu, build_ai_session_menu, build_ai_fix_menu
from bot.task_manager import TaskManager, TaskContext


@pytest.fixture
def sample_metadata():
    """Generates a dummy 4K bloated video metadata object."""
    return MediaMetadata(
        file_path=Path("dummy_sample.mp4"),
        file_name="sample_4k_bloat.mp4",
        file_size_bytes=480 * 1024 * 1024,  # 480 MB
        duration_seconds=42.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=3840,
        height=2160,
        fps=60.0,
        video_bitrate_kbps=91000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=91128,
        is_high_bitrate=True,
        raw_info={}
    )


def test_skills_registry_loading_and_extraction():
    """Verifies that all skills are indexed and relevant sections are retrieved."""
    skills = skill_registry.list_skills()
    assert "ffmpeg" in skills
    assert "video_diagnosis" in skills
    assert "creative_fx" in skills

    # Query for lag
    lag_excerpt = skill_registry.get_relevant_skills("my video lags on android phone")
    assert "video_diagnosis" in lag_excerpt.lower() or "lag" in lag_excerpt.lower()

    # Query for reverse
    rev_excerpt = skill_registry.get_relevant_skills("reverse this clip backwards")
    assert "reverse" in rev_excerpt.lower()

    # Query for aspect ratio
    ratio_excerpt = skill_registry.get_relevant_skills("change to 9:16 vertical ratio")
    assert "9:16" in ratio_excerpt


def test_memory_safety_guardrails():
    """Verifies RAM protection checks for reverse operations on mobile hosts."""
    # 45-second 1080p video reverse -> Must be blocked to prevent OOM
    safe_long, msg_long = skill_registry.check_memory_safety(45.0, 1920, 1080, is_reverse=True, is_termux=True)
    assert not safe_long
    assert "will crash your device" in msg_long or "trim" in msg_long

    # 18-second 1080p video reverse -> Allowed with downscaling requirement
    safe_mid, msg_mid = skill_registry.check_memory_safety(18.0, 1920, 1080, is_reverse=True, is_termux=True)
    assert safe_mid
    assert "downscaling to 720p" in msg_mid

    # Normal transcode -> Always safe
    safe_norm, _ = skill_registry.check_memory_safety(120.0, 3840, 2160, is_reverse=False, is_termux=True)
    assert safe_norm


@pytest.mark.asyncio
async def test_openrouter_client_fallback_cascade():
    """Verifies client attempts fallback models when primary model is rate-limited (429)."""
    client = OpenRouterClient(api_key="mock_key", default_model="primary/model:free")

    call_count = 0
    async def mock_post(url, headers, json):
        nonlocal call_count
        call_count += 1
        model = json.get("model")
        mock_resp = MagicMock()
        if model == "primary/model:free":
            mock_resp.status_code = 429
            mock_resp.text = "Rate limited"
        else:
            mock_resp.status_code = 200
            mock_resp.json.return_value = {
                "choices": [{"message": {"role": "assistant", "content": "Fallback model response"}}]
            }
        return mock_resp

    with patch("httpx.AsyncClient.post", side_effect=mock_post):
        msg = await client.chat_completion(messages=[{"role": "user", "content": "test"}])
        assert msg["content"] == "Fallback model response"
        assert call_count >= 2


@pytest.mark.asyncio
async def test_ai_agent_diagnostic_turn(sample_metadata, tmp_path):
    """Verifies that diagnostic user query triggers diagnose_video tool and formats diagnosis."""
    dummy_input = tmp_path / "test.mp4"
    dummy_input.write_bytes(b"dummy_content")

    mock_tool_call = {
        "content": "Analyzing video specifications...",
        "tool_calls": [{
            "id": "call_1",
            "type": "function",
            "function": {
                "name": "diagnose_video",
                "arguments": json.dumps({
                    "explanation": "Video is encoded in 4K 60fps HEVC at 91 Mbps, overwhelming phone decoder.",
                    "recommended_action": "Downscale to 1080p H.264 at ~3500 kbps.",
                    "estimated_reduction_percent": 88
                })
            }
        }]
    }

    mock_client = OpenRouterClient(api_key="mock_key")
    mock_client.chat_completion = AsyncMock(return_value=mock_tool_call)

    agent = VideoAIAgent(user_id=42, task_id="task_diag", input_path=dummy_input, meta=sample_metadata, client=mock_client)
    res = await agent.process_message("why is this video lagging on my phone?")

    assert res["status"] == "success"
    assert res["type"] == "diagnosis"
    assert "4K 60fps HEVC" in res["explanation"]
    assert res["estimated_reduction_percent"] == 88


@pytest.mark.asyncio
async def test_safe_tool_executor_sandbox_confinement(sample_metadata, tmp_path):
    """Verifies that execute_ffmpeg_plan is strictly confined inside Ai_Workplace/."""
    dummy_input = tmp_path / "test_orig.mp4"
    dummy_input.write_bytes(b"dummy_bytes")

    executor = SafeToolExecutor(user_id=99, task_id="task_sandbox", input_path=dummy_input, meta=sample_metadata)
    assert executor.workplace_dir.name == "99_task_sandbox"
    assert "Ai_Workplace" in str(executor.workplace_dir)

    # Reversal memory safety rejection
    res_unsafe = await executor.run_execute_ffmpeg(
        plan_title="Reverse Video",
        output_filename="reversed.mp4",
        vf_filter="reverse",
        af_filter="areverse"
    )
    # sample_metadata has 42s duration, should be rejected for reverse
    assert res_unsafe["status"] == "error"
    assert "Memory Safety Constraint" in res_unsafe["error"]

    # Cleanup
    settings.clean_ai_workplace(user_id=99, task_id="task_sandbox")
    assert not executor.workplace_dir.exists()


def test_bot_ai_keyboards():
    """Verifies inline keyboards for AI Manual mode."""
    main_menu = build_main_menu("t1")
    # AI button is in row 2
    ai_btn = main_menu[1][0]
    assert "AI Manual" in ai_btn.text
    assert b"ai_manual:t1" in ai_btn.type.data

    session_menu = build_ai_session_menu("t1")
    assert len(session_menu) >= 1
    assert any(b"main_menu:t1" in btn.type.data for row in session_menu for btn in row)

    fix_menu = build_ai_fix_menu("t1", "recommended")
    assert b"ai_apply:recommended:t1" in fix_menu[0][0].type.data

    from bot.keyboards import build_ai_deliver_menu
    deliv_menu = build_ai_deliver_menu("t1")
    assert len(deliv_menu[0]) == 2
    assert b"ai_manual:t1" in deliv_menu[0][0].type.data
    assert b"main_menu:t1" in deliv_menu[0][1].type.data


def test_task_manager_ai_session_lookup():
    """Verifies that TaskManager tracks and finds active AI sessions by user_id."""
    tm = TaskManager(max_concurrent=1)
    ctx1 = TaskContext(
        task_id="t1",
        user_id=1001,
        input_path=Path("in1.mp4"),
        output_path=None,
        meta=None,
        status_message=None,
        original_message_id=1,
        ai_session_active=False
    )
    ctx2 = TaskContext(
        task_id="t2",
        user_id=1002,
        input_path=Path("in2.mp4"),
        output_path=None,
        meta=None,
        status_message=None,
        original_message_id=2,
        ai_session_active=True
    )
    tm.register_task(ctx1)
    tm.register_task(ctx2)

    assert tm.get_active_ai_task_for_user(1001) is None
    assert tm.get_active_ai_task_for_user(1002) == ctx2
    assert tm.get_active_ai_task_for_user(9999) is None


@pytest.mark.asyncio
async def test_fetch_openrouter_free_models_filtering():
    """Verifies free models fetcher filters out embeddings/rerankers and caches results."""
    from core.ai.client import fetch_openrouter_free_models

    mock_api_payload = {
        "data": [
            {"id": "nvidia/nemotron-3.5-embed-1b:free", "name": "Nemotron Embed 1B (free)", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "nvidia/nemotron-3.5-rerank-8b:free", "name": "Nemotron Rerank 8B (free)", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "openai/moderation:free", "name": "OpenAI Moderation (free)", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "nvidia/nemotron-3.5-lightning:free", "name": "NVIDIA: Nemotron 3.5 Lightning (free)", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "google/gemma-4-31b-it:free", "name": "Google: Gemma 4 31B (free)", "pricing": {"prompt": "0", "completion": "0"}},
            {"id": "anthropic/claude-3-opus", "name": "Claude 3 Opus", "pricing": {"prompt": "15", "completion": "75"}}
        ]
    }

    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = mock_api_payload

    with patch("httpx.AsyncClient.get", AsyncMock(return_value=mock_resp)):
        models = await fetch_openrouter_free_models(force_refresh=True)

        ids = [m["id"] for m in models]
        # openrouter/free router is always at top
        assert ids[0] == "openrouter/free"
        assert "nvidia/nemotron-3.5-lightning:free" in ids
        assert "google/gemma-4-31b-it:free" in ids

        # Non-chat / paid models MUST be excluded
        assert "nvidia/nemotron-3.5-embed-1b:free" not in ids
        assert "nvidia/nemotron-3.5-rerank-8b:free" not in ids
        assert "openai/moderation:free" not in ids
        assert "anthropic/claude-3-opus" not in ids


@pytest.mark.asyncio
async def test_user_ai_model_preference_db(tmp_path: Path, monkeypatch):
    """Verifies reading and persisting user AI model preferences in SQLite."""
    from database.db import init_db
    from database.models import get_user_ai_model, set_user_ai_model

    test_db = tmp_path / "pref_test.db"
    monkeypatch.setattr(settings, "DB_PATH", test_db)
    await init_db()

    user_id = 777123
    # Default model before setting
    default_model = await get_user_ai_model(user_id)
    assert "free" in default_model

    # Set new preferred model
    new_model = "nvidia/nemotron-3.5-lightning:free"
    await set_user_ai_model(user_id, new_model)
    saved_model = await get_user_ai_model(user_id)
    assert saved_model == new_model


def test_build_ai_models_menu_and_no_emojis():
    """Verifies AI models menu formatting, 64-byte callback limit, and strictly zero emojis."""
    from bot.keyboards import build_ai_models_menu

    dummy_models = [
        {"id": f"provider/model-{i}:free", "name": f"Test Model {i}"}
        for i in range(12)
    ]
    current_model = "provider/model-0:free"

    # Page 0 (items 0..4)
    menu_p0 = build_ai_models_menu(dummy_models, current_model, page=0, page_size=5)
    assert len(menu_p0) == 7  # 5 model rows + 1 nav row (Next >) + 1 action row

    # Check Active marker
    assert "[Active]" in menu_p0[0][0].text

    # Check callback data does not exceed Telegram 64-byte limit
    for row in menu_p0:
        for btn in row:
            data = btn.type.data
            assert len(data) <= 64, f"Callback data too long: {data}"

    # Verify strictly NO emojis in any button label
    emoji_chars = ["🤖", "⚡", "🔍", "💡", "⚠️", "✅", "❌", "✨", "🚀", "🔥"]
    for row in menu_p0:
        for btn in row:
            for em in emoji_chars:
                assert em not in btn.text, f"Emoji '{em}' found in button text '{btn.text}'"

    assert any(b"aimod_close" in btn.type.data for row in menu_p0 for btn in row)


def make_dummy_metadata(
    file_path=Path("dummy.mp4"),
    file_name="dummy.mp4",
    file_size_bytes=1000,
    duration_seconds=10.0,
    has_video=True,
    has_audio=True
) -> MediaMetadata:
    """Helper to construct valid MediaMetadata for testing."""
    return MediaMetadata(
        file_path=file_path,
        file_name=file_name,
        file_size_bytes=file_size_bytes,
        duration_seconds=duration_seconds,
        container_format="mp4" if has_video else "mp3",
        has_video=has_video,
        video_codec="h264" if has_video else None,
        width=1280 if has_video else None,
        height=720 if has_video else None,
        fps=30.0 if has_video else None,
        video_bitrate_kbps=1000 if has_video else None,
        has_audio=has_audio,
        audio_codec="aac" if has_audio else None,
        audio_channels=2 if has_audio else None,
        audio_sample_rate=44100 if has_audio else None,
        audio_bitrate_kbps=128 if has_audio else None,
        overall_bitrate_kbps=1128 if has_video else 128,
        is_high_bitrate=False,
        raw_info={}
    )


def test_audio_message_detection():
    """Verifies that audio documents and voice notes are recognized as audio."""
    from bot.media_handler import is_audio_message
    from telethon.tl.types import DocumentAttributeAudio, DocumentAttributeVideo

    msg_text = MagicMock()
    msg_text.audio = None
    msg_text.voice = None
    msg_text.file = None
    msg_text.document = None
    assert not is_audio_message(msg_text)

    # Audio message with DocumentAttributeAudio
    msg_audio = MagicMock()
    msg_audio.audio = None
    msg_audio.voice = None
    msg_audio.file = MagicMock()
    msg_audio.file.name = "audio.mp3"
    attr_audio = MagicMock(spec=DocumentAttributeAudio)
    msg_audio.document = MagicMock(mime_type="audio/mpeg", attributes=[attr_audio])
    assert is_audio_message(msg_audio)

    # Audio message by extension .wav without attributes
    msg_wav = MagicMock()
    msg_wav.audio = None
    msg_wav.voice = None
    msg_wav.file = MagicMock()
    msg_wav.file.name = "soundtrack.wav"
    msg_wav.document = MagicMock(mime_type="application/octet-stream", attributes=[])
    assert is_audio_message(msg_wav)

    # Video message should not be recognized as audio
    msg_video = MagicMock()
    msg_video.audio = None
    msg_video.voice = None
    msg_video.file = MagicMock()
    msg_video.file.name = "movie.mp4"
    attr_vid = MagicMock(spec=DocumentAttributeVideo)
    msg_video.document = MagicMock(mime_type="video/mp4", attributes=[attr_vid])
    assert not is_audio_message(msg_video)


@pytest.mark.asyncio
async def test_safe_tool_executor_extract_audio_and_muting(tmp_path):
    """Verifies FFmpeg command generation for extract_audio_only and mute_video_only."""
    in_video = tmp_path / "source.mp4"
    in_video.write_bytes(b"dummy_video_bytes")

    meta = make_dummy_metadata(file_path=in_video, file_name="source.mp4", file_size_bytes=1024 * 1024)
    executor = SafeToolExecutor(user_id=123, task_id="task_extract_mute", input_path=in_video, meta=meta)

    captured_cmds = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        captured_cmds.append(cmd)
        out_target = Path(cmd[-1])
        out_target.parent.mkdir(parents=True, exist_ok=True)
        out_target.write_bytes(b"processed_bytes")

    out_probe = make_dummy_metadata(
        file_path=Path("out.mp3"),
        file_name="out.mp3",
        file_size_bytes=512,
        has_video=False,
        has_audio=True
    )

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=out_probe)):

        # 1. Test audio extraction (-vn)
        res_audio = await executor.run_execute_ffmpeg(
            plan_title="Extract Audio",
            output_filename="extracted.mp3",
            extract_audio_only=True
        )
        assert res_audio["status"] == "success"
        assert res_audio["output_filename"] == "extracted.mp3"
        last_cmd = captured_cmds[-1]
        assert "-vn" in last_cmd
        assert "-c:a" in last_cmd
        assert "libmp3lame" in last_cmd

        # 2. Test video muting (-an)
        res_mute = await executor.run_execute_ffmpeg(
            plan_title="Mute Video",
            output_filename="muted.mp4",
            mute_video_only=True
        )
        assert res_mute["status"] == "success"
        assert res_mute["output_filename"] == "muted.mp4"
        mute_cmd = captured_cmds[-1]
        assert "-an" in mute_cmd
        assert "-c:v" in mute_cmd
        assert "copy" in mute_cmd

    settings.clean_ai_workplace(user_id=123, task_id="task_extract_mute")


@pytest.mark.asyncio
async def test_safe_tool_executor_compound_split_and_multi_deliverables(tmp_path):
    """Verifies executing compound operations that generate separate video and audio deliverables."""
    in_video = tmp_path / "orig.mp4"
    in_video.write_bytes(b"video_bytes")

    executor = SafeToolExecutor(
        user_id=124,
        task_id="task_compound",
        input_path=in_video,
        assets={"v1": {"filepath": str(in_video), "filename": "orig.mp4"}}
    )

    captured_cmds = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        captured_cmds.append(cmd)
        out_target = Path(cmd[-1])
        out_target.parent.mkdir(parents=True, exist_ok=True)
        out_target.write_bytes(b"deliverable_bytes")

    dummy_meta = make_dummy_metadata(file_path=Path("dummy"), file_name="dummy", file_size_bytes=100)

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=dummy_meta)):

        res = await executor.run_execute_ffmpeg(
            plan_title="Separate Video & Audio",
            operations=[
                {
                    "inputs": ["v1"],
                    "output_filename": "video_mute.mp4",
                    "mute_video_only": True
                },
                {
                    "inputs": ["v1"],
                    "output_filename": "soundtrack.mp3",
                    "extract_audio_only": True
                }
            ]
        )

        assert res["status"] == "success"
        assert len(res["output_paths"]) == 2
        assert len(res["deliverables"]) == 2
        assert len(captured_cmds) == 2
        assert "video_mute.mp4" in res["output_paths"][0]
        assert "soundtrack.mp3" in res["output_paths"][1]

    settings.clean_ai_workplace(user_id=124, task_id="task_compound")


@pytest.mark.asyncio
async def test_safe_tool_executor_audio_replacement_multi_input(tmp_path):
    """Verifies multi-input resolution and stream mapping for soundtrack replacement."""
    in_video = tmp_path / "vid.mp4"
    in_audio = tmp_path / "bg_music.mp3"
    in_video.write_bytes(b"video")
    in_audio.write_bytes(b"audio")

    assets = {
        "v1": {"filepath": str(in_video), "filename": "vid.mp4"},
        "a1": {"filepath": str(in_audio), "filename": "bg_music.mp3"}
    }

    executor = SafeToolExecutor(
        user_id=125,
        task_id="task_remux",
        input_path=in_video,
        assets=assets
    )

    captured_cmds = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        captured_cmds.append(cmd)
        out_target = Path(cmd[-1])
        out_target.parent.mkdir(parents=True, exist_ok=True)
        out_target.write_bytes(b"remuxed")

    dummy_meta = make_dummy_metadata(file_path=Path("dummy"), file_name="dummy", file_size_bytes=200)

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=dummy_meta)):

        res = await executor.run_execute_ffmpeg(
            plan_title="Replace Audio",
            output_filename="remuxed_video.mp4",
            inputs=["v1", "a1"],
            stream_mapping=["0:v:0", "1:a:0"],
            video_codec="copy",
            audio_codec="aac"
        )

        assert res["status"] == "success"
        cmd = captured_cmds[0]
        assert str(in_video) in cmd
        assert str(in_audio) in cmd
        assert "-map" in cmd
        assert "0:v:0" in cmd
        assert "1:a:0" in cmd

    settings.clean_ai_workplace(user_id=125, task_id="task_remux")


@pytest.mark.asyncio
async def test_deliver_ai_results_sequential_multi_upload(tmp_path):
    """Verifies deliver_ai_results sequentially uploads multiple files and completes the task."""
    from bot.ai_handler import deliver_ai_results

    out_file1 = tmp_path / "video_mute.mp4"
    out_file2 = tmp_path / "audio.mp3"
    out_file1.write_bytes(b"video_data_12345")
    out_file2.write_bytes(b"audio_data_67890")

    mock_task_ctx = MagicMock()
    mock_task_ctx.task_id = "test_task_deliv"
    mock_task_ctx.user_id = 8888
    mock_task_ctx.input_path = tmp_path / "orig.mp4"
    mock_task_ctx.meta = make_dummy_metadata(
        file_path=mock_task_ctx.input_path,
        file_name="orig.mp4",
        file_size_bytes=20000,
        duration_seconds=12.0,
        has_video=True
    )
    mock_task_ctx.is_cancelled = False
    mock_task_ctx.processing_elapsed_time = 3.5

    mock_status_msg = AsyncMock()
    mock_client = AsyncMock()

    meta_vid = make_dummy_metadata(file_path=out_file1, file_name="video_mute.mp4", file_size_bytes=16, has_video=True)
    meta_aud = make_dummy_metadata(file_path=out_file2, file_name="audio.mp3", file_size_bytes=16, has_video=False)

    async def mock_probe(path):
        if "mp4" in str(path):
            return meta_vid
        return meta_aud

    with patch("bot.ai_handler.fast_upload", AsyncMock(side_effect=["doc1", "doc2"])), \
         patch("bot.ai_handler.probe_media", side_effect=mock_probe), \
         patch("bot.ai_handler.register_file_cache", AsyncMock()) as mock_cache, \
         patch("bot.ai_handler.record_task_complete", AsyncMock()) as mock_complete:

        await deliver_ai_results(
            task_ctx=mock_task_ctx,
            plan_title="Separate Video & Audio",
            out_paths=[out_file1, out_file2],
            status_msg=mock_status_msg,
            client=mock_client
        )

        assert mock_cache.call_count == 2
        assert mock_client.send_file.call_count == 2

        # Check call arguments: first file was video (supports_streaming=True), second was audio (False)
        first_call = mock_client.send_file.call_args_list[0]
        second_call = mock_client.send_file.call_args_list[1]
        assert first_call.kwargs["supports_streaming"] is True
        assert second_call.kwargs["supports_streaming"] is False

        # Task completion recorded with combined output bytes
        assert mock_complete.call_count == 1
        complete_kwargs = mock_complete.call_args.kwargs
        assert complete_kwargs["output_size"] == len(b"video_data_12345") + len(b"audio_data_67890")
        assert complete_kwargs["elapsed_time"] == 3.5

        # AI session must remain active for follow-up conversation
        assert mock_task_ctx.ai_session_active is True


def test_clean_display_name_and_formatting():
    """Verifies that internal task IDs are stripped and hardware descriptions are clean."""
    from core.probe import clean_display_name
    from core.hardware import detect_hardware

    assert clean_display_name("8cadedd8e0_douyin_7693917933180826587.mp4") == "douyin_7693917933180826587.mp4"
    assert clean_display_name("56f76b2768_video_only.mp4") == "video_only.mp4"
    assert clean_display_name("plain_video.mp4") == "plain_video.mp4"
    assert clean_display_name("") == ""

    # Hardware description shouldn't contain long device paths that wrap awkwardly
    hw = detect_hardware()
    assert "/dev/dri/" not in hw.description


@pytest.mark.asyncio
async def test_handle_incoming_session_asset_auto_execution():
    """Verifies that uploading a session asset automatically triggers execute_ai_query without reprompting."""
    from bot.media_handler import handle_incoming_session_asset
    from bot.task_manager import TaskContext

    mock_event = AsyncMock()
    mock_status_msg = AsyncMock()
    mock_event.reply.return_value = mock_status_msg

    # Mock audio message
    mock_message = MagicMock()
    mock_message.audio = MagicMock()
    mock_message.file = MagicMock()
    mock_message.file.name = "douyin_bgm.mp3"
    mock_message.file.size = 1024
    mock_event.message = mock_message

    mock_task_ctx = TaskContext(
        task_id="test123456",
        user_id=123,
        input_path=Path("/tmp/input.mp4"),
        output_path=None,
        meta=None,
        status_message=mock_status_msg,
        original_message_id=101
    )
    mock_task_ctx.ai_session_active = True
    mock_client = AsyncMock()

    mock_meta = MagicMock()
    mock_meta.has_video = False
    mock_meta.duration_seconds = 30.0
    mock_meta.resolution_label = ""
    mock_meta.file_size_bytes = 1024

    with patch("bot.media_handler.fast_download", AsyncMock()), \
         patch("bot.media_handler.probe_media", AsyncMock(return_value=mock_meta)), \
         patch("bot.ai_handler.execute_ai_query", AsyncMock()) as mock_exec_query:

        await handle_incoming_session_asset(mock_event, mock_task_ctx, mock_client)

        # Asset registered in session
        assets = mock_task_ctx.get_assets()
        assert "a1" in assets
        assert assets["a1"]["filename"] == "test123456_douyin_bgm.mp3"

        # AI query was automatically triggered with asset key
        assert mock_exec_query.call_count == 1
        prompt_arg = mock_exec_query.call_args[0][0]
        assert "[a1]" in prompt_arg
        assert "douyin_bgm.mp3" in prompt_arg
        assert "Please proceed" in prompt_arg


def test_zero_emojis_in_all_ai_strings():
    """Verifies that AI menus, buttons, and prompts strictly contain zero emojis."""
    from bot.keyboards import build_ai_session_menu, build_ai_fix_menu
    emoji_chars = ["🤖", "⚡", "🔍", "💡", "⚠️", "✅", "❌", "✨", "🚀", "🔥", "🎥", "🎬", "🎵", "🔊"]

    session_menu = build_ai_session_menu("test_task")
    for row in session_menu:
        for btn in row:
            for em in emoji_chars:
                assert em not in btn.text, f"Emoji '{em}' found in session button: {btn.text}"

    fix_menu = build_ai_fix_menu("test_task", "recommended")
    for row in fix_menu:
        for btn in row:
            for em in emoji_chars:
                assert em not in btn.text, f"Emoji '{em}' found in fix button: {btn.text}"

    from bot.keyboards import build_ai_deliver_menu
    deliv_menu = build_ai_deliver_menu("test_task")
    for row in deliv_menu:
        for btn in row:
            for em in emoji_chars:
                assert em not in btn.text, f"Emoji '{em}' found in deliver button: {btn.text}"


@pytest.mark.asyncio
async def test_safe_tool_executor_resolution_and_quality_guardrails(tmp_path):
    """Verifies that portrait 4K scale is oriented correctly (1080:-2) and 480p is prevented unless requested."""
    input_file = tmp_path / "portrait_4k.mp4"
    input_file.write_bytes(b"dummy")

    meta_portrait_4k = MediaMetadata(
        file_path=input_file,
        file_name="portrait_4k.mp4",
        file_size_bytes=8000000,
        duration_seconds=10.0,
        container_format="mp4",
        has_video=True,
        video_codec="hevc",
        width=2160,
        height=3840,
        fps=60.0,
        video_bitrate_kbps=5800,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=5928,
        is_high_bitrate=False,
        raw_info={}
    )

    executor = SafeToolExecutor(
        user_id=9999,
        task_id="t_res_guard",
        input_path=input_file,
        meta=meta_portrait_4k
    )

    captured_cmds = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        captured_cmds.append(cmd)
        out_p = Path(cmd[-1])
        out_p.write_bytes(b"dummy_out")

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=meta_portrait_4k)):

        # 1. Inverted portrait filter (scale=-2:1080) MUST be corrected to scale=1080:-2
        await executor.run_execute_ffmpeg(
            plan_title="Optimize 4K vertical",
            output_filename="opt.mp4",
            vf_filter="scale=-2:1080"
        )
        assert len(captured_cmds) == 1
        vf_idx = captured_cmds[0].index("-vf")
        assert captured_cmds[0][vf_idx + 1] == "scale=1080:-2"

        # 2. Unauthorized 480p downscaling MUST be clamped to 1080p
        await executor.run_execute_ffmpeg(
            plan_title="Optimize without losing quality",
            output_filename="opt2.mp4",
            vf_filter="scale=480:854"
        )
        assert len(captured_cmds) == 2
        vf_idx2 = captured_cmds[1].index("-vf")
        assert "480" not in captured_cmds[1][vf_idx2 + 1]
        assert "1080" in captured_cmds[1][vf_idx2 + 1]


def test_sanitize_message_history():
    """Verifies that history sanitization complies with OpenAI / OpenRouter tool call requirements."""
    from core.ai.agent import sanitize_message_history

    # 1. Normal conversation
    h1 = [{"role": "user", "content": "hi"}, {"role": "assistant", "content": "hello"}]
    assert sanitize_message_history(h1) == h1

    # 2. Tool response immediately followed by user message -> must insert assistant message
    h2 = [
        {"role": "user", "content": "optimize this"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status": "success"}'},
        {"role": "user", "content": "now add audio"}
    ]
    s2 = sanitize_message_history(h2)
    assert len(s2) == 5
    assert s2[2]["role"] == "tool"
    assert s2[3]["role"] == "assistant"
    assert s2[4]["role"] == "user"

    # 3. Trailing tool message -> must append assistant message
    h3 = [
        {"role": "user", "content": "optimize this"},
        {"role": "assistant", "content": None, "tool_calls": [{"id": "c1"}]},
        {"role": "tool", "tool_call_id": "c1", "content": '{"status": "success"}'}
    ]
    s3 = sanitize_message_history(h3)
    assert len(s3) == 4
    assert s3[-1]["role"] == "assistant"


@pytest.mark.asyncio
async def test_deliver_ai_results_registers_assets_and_updates_context(tmp_path):
    """Verifies that deliver_ai_results registers output files as session assets and updates task_ctx.meta."""
    from bot.ai_handler import deliver_ai_results
    from bot.task_manager import TaskContext

    in_file = tmp_path / "original_4k.mp4"
    in_file.write_bytes(b"orig_4k_data")
    out_file = tmp_path / "optimized_muted.mp4"
    out_file.write_bytes(b"opt_muted_data")

    orig_meta = make_dummy_metadata(file_path=in_file, file_name="original_4k.mp4", file_size_bytes=1000, duration_seconds=11.0, has_video=True)
    out_meta = make_dummy_metadata(file_path=out_file, file_name="optimized_muted.mp4", file_size_bytes=500, duration_seconds=11.0, has_video=True)

    status_msg = AsyncMock()
    mock_client = AsyncMock()

    task_ctx = TaskContext(
        task_id="chain_task",
        user_id=777,
        input_path=in_file,
        output_path=None,
        meta=orig_meta,
        status_message=status_msg,
        original_message_id=50
    )
    task_ctx.ai_session_active = True

    # Initialize agent
    mock_ai_agent = MagicMock()
    mock_ai_agent.assets = task_ctx.get_assets()
    mock_ai_agent.tool_executor = MagicMock()
    task_ctx.ai_agent = mock_ai_agent

    with patch("bot.ai_handler.fast_upload", AsyncMock(return_value="doc_out")), \
         patch("bot.ai_handler.probe_media", AsyncMock(return_value=out_meta)), \
         patch("bot.ai_handler.register_file_cache", AsyncMock()), \
         patch("bot.ai_handler.record_task_complete", AsyncMock()):

        await deliver_ai_results(
            task_ctx=task_ctx,
            plan_title="Optimize and Mute",
            out_paths=[out_file],
            status_msg=status_msg,
            client=mock_client,
            assistant_text="I have optimized and muted your video. Please upload your new soundtrack!"
        )

        # 1. Output deliverable registered in assets
        assert "v2" in task_ctx.assets
        assert task_ctx.assets["v2"]["filename"] == "optimized_muted.mp4"

        # 2. Context updated to output deliverable
        assert task_ctx.input_path == out_file
        assert task_ctx.meta == out_meta

        # 3. Agent synchronized with output context
        assert task_ctx.ai_agent.input_path == out_file
        assert task_ctx.ai_agent.meta == out_meta

        # 4. Assistant message displayed in summary card
        status_edit_text = status_msg.edit.call_args[0][0]
        assert "Please upload your new soundtrack!" in status_edit_text
        assert "Preserved for chained edits" in status_edit_text


@pytest.mark.asyncio
async def test_safe_tool_executor_dual_input_auto_mapping_and_stream_copy(sample_metadata, tmp_path):
    """Verifies that combining video and audio inputs automatically configures -map and stream copy."""
    vid_file = tmp_path / "video_muted.mp4"
    vid_file.write_bytes(b"vid_bytes")
    aud_file = tmp_path / "new_audio.mp3"
    aud_file.write_bytes(b"aud_bytes")

    vid_meta = make_dummy_metadata(file_path=vid_file, file_name="video_muted.mp4", file_size_bytes=1000, has_video=True)
    aud_meta = make_dummy_metadata(file_path=aud_file, file_name="new_audio.mp3", file_size_bytes=200, has_video=False)

    assets = {
        "v2": {"id": "v2", "filename": "video_muted.mp4", "filepath": vid_file, "meta": vid_meta, "media_type": "video"},
        "a1": {"id": "a1", "filename": "new_audio.mp3", "filepath": aud_file, "meta": aud_meta, "media_type": "audio"}
    }

    executor = SafeToolExecutor(
        user_id=111,
        task_id="dual_in_task",
        input_path=vid_file,
        meta=vid_meta,
        assets=assets
    )

    captured_cmds = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        captured_cmds.append(cmd)
        out_p = Path(cmd[-1])
        out_p.write_bytes(b"merged_out")

    async def mock_probe(p):
        return vid_meta if "video" in str(p) else aud_meta

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", side_effect=mock_probe):

        res = await executor.run_execute_ffmpeg(
            plan_title="Merge Video and New Audio",
            output_filename="final_video.mp4",
            inputs=["v2", "a1"]
        )

        assert res["status"] == "success"
        cmd = captured_cmds[0]
        # Must map video from 0 and audio from 1
        assert "-map" in cmd
        map_indices = [i for i, x in enumerate(cmd) if x == "-map"]
        assert cmd[map_indices[0] + 1] == "0:v:0"
        assert cmd[map_indices[1] + 1] == "1:a:0"
        # Must use video stream copy because video is already encoded
        cv_idx = cmd.index("-c:v")
        assert cmd[cv_idx + 1] == "copy"
        # Must include -shortest
        assert "-shortest" in cmd


@pytest.mark.asyncio
async def test_safe_tool_executor_json_stringified_arguments(tmp_path):
    """Verifies that stringified JSON arrays for stream_mapping and extra_args are cleanly normalized."""
    vid_file = tmp_path / "video_muted.mp4"
    vid_file.write_bytes(b"vid_bytes")
    aud_file = tmp_path / "new_audio.mp3"
    aud_file.write_bytes(b"aud_bytes")

    vid_meta = make_dummy_metadata(file_path=vid_file, file_name="video_muted.mp4", file_size_bytes=1000, has_video=True)
    aud_meta = make_dummy_metadata(file_path=aud_file, file_name="new_audio.mp3", file_size_bytes=200, has_video=False)

    assets = {
        "v2": {"id": "v2", "filename": "video_muted.mp4", "filepath": vid_file, "meta": vid_meta, "media_type": "video"},
        "a1": {"id": "a1", "filename": "new_audio.mp3", "filepath": aud_file, "meta": aud_meta, "media_type": "audio"}
    }

    executor = SafeToolExecutor(
        user_id=111,
        task_id="json_str_task",
        input_path=vid_file,
        meta=vid_meta,
        assets=assets
    )

    captured_cmds = []
    async def mock_run_ffmpeg(cmd, total_duration_seconds, progress_callback=None, on_process_started=None):
        captured_cmds.append(cmd)
        out_p = Path(cmd[-1])
        out_p.write_bytes(b"merged_out")

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=vid_meta)):

        # Pass exact stringified arguments that LLM produced:
        res = await executor.run_execute_ffmpeg(
            plan_title="Merge new audio track with optimized video",
            output_filename="final_with_audio.mp4",
            inputs='["v2", "a1"]',
            video_codec="copy",
            audio_codec="aac",
            stream_mapping='["-map", "0:v:0", "-map", "1:a:0"]',
            extra_args='["-shortest"]'
        )

        assert res["status"] == "success"
        cmd = captured_cmds[0]
        # Verify no trailing quotes or brackets from character splitting
        assert "[" not in cmd
        assert "]" not in cmd
        assert '"' not in cmd
        assert "'" not in cmd
        # Verify valid mapping
        map_indices = [i for i, x in enumerate(cmd) if x == "-map"]
        assert len(map_indices) == 2
        assert cmd[map_indices[0] + 1] == "0:v:0"
        assert cmd[map_indices[1] + 1] == "1:a:0"
        # Verify shortest flag present exactly once
        assert cmd.count("-shortest") == 1


@pytest.mark.asyncio
async def test_safe_tool_executor_output_ffprobe_metadata(tmp_path):
    """Verifies that run_execute_ffmpeg attaches comprehensive ffprobe metadata to deliverables and return dict."""
    from core.ai.tools import SafeToolExecutor
    from core.probe import MediaMetadata
    from unittest.mock import patch, AsyncMock

    in_f = tmp_path / "src.mp4"
    in_f.write_bytes(b"src")
    src_meta = MediaMetadata(
        file_path=in_f,
        file_name="src.mp4",
        file_size_bytes=1000,
        duration_seconds=10.0,
        container_format="mov,mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=2000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=2128,
        is_high_bitrate=False,
        raw_info={}
    )
    executor = SafeToolExecutor(123, "t_probe", in_f, src_meta)

    out_meta = MediaMetadata(
        file_path=tmp_path / "out_bw.mp4",
        file_name="out_bw.mp4",
        file_size_bytes=800,
        duration_seconds=10.0,
        container_format="mov,mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=1500,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=1628,
        is_high_bitrate=False,
        raw_info={}
    )

    async def mock_run_ffmpeg(cmd, **kwargs):
        out_p = Path(cmd[-1])
        out_p.write_bytes(b"out_data_800")

    with patch("core.ai.tools.run_ffmpeg_with_progress", side_effect=mock_run_ffmpeg), \
         patch("core.ai.tools.probe_media", AsyncMock(return_value=out_meta)):

        res = await executor.run_execute_ffmpeg(
            plan_title="Black and White Conversion",
            output_filename="out_bw.mp4",
            inputs=["v1"],
            vf_filter="hue=s=0",
            video_codec="libx264",
            audio_codec="aac"
        )

        assert res["status"] == "success"
        assert "ffprobe" in res
        ffprobe = res["ffprobe"]
        assert ffprobe["has_video"] is True
        assert ffprobe["video_codec"] == "h264"
        assert ffprobe["width"] == 1920
        assert ffprobe["height"] == 1080
        assert ffprobe["resolution"] == "1080p FHD (1920x1080)"
        assert ffprobe["fps"] == 30.0
        assert ffprobe["has_audio"] is True
        assert ffprobe["audio_codec"] == "aac"
        assert ffprobe["duration_seconds"] == 10.0
        assert ffprobe["size_bytes"] == 12  # written 12 bytes
        # Deliverables check
        assert len(res["deliverables"]) == 1
        d0 = res["deliverables"][0]
        assert "ffprobe" in d0
        assert d0["ffprobe"]["video_codec"] == "h264"


def test_ai_agent_unrealistic_demand_and_black_white_guidelines(tmp_path):
    """Verifies that VideoAIAgent system prompt explicitly contains guardrails for unrealistic requests and creative filters."""
    from core.ai.agent import VideoAIAgent
    from core.probe import MediaMetadata

    in_f = tmp_path / "vid_1080p.mp4"
    in_f.write_bytes(b"data")
    meta_1080p = MediaMetadata(
        file_path=in_f,
        file_name="vid_1080p.mp4",
        file_size_bytes=5000000,
        duration_seconds=15.0,
        container_format="mov,mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=2500,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=2628,
        is_high_bitrate=False,
        raw_info={}
    )

    agent = VideoAIAgent(user_id=101, task_id="t_guard", input_path=in_f, meta=meta_1080p)
    prompt = agent._build_system_prompt("can you make my original video in black and white or upscale to 4K?")

    # Verify original video targeting rule
    assert "inputs: ['v1']" in prompt
    assert "[ORIGINAL USER SOURCE VIDEO]" in prompt

    # Verify unrealistic demand guardrails
    assert "Unrealistic & Impossible Demands" in prompt
    assert "Upscaling a 1080p, 720p, 480p, or 360p video to 4K" in prompt
    assert "unrealistic demand" in prompt

    # Verify black and white recipe
    assert "hue=s=0" in prompt
    assert "create single image files" in prompt

    # Verify output ffprobe awareness
    assert "Output FFprobe Awareness" in prompt


@pytest.mark.asyncio
async def test_deliver_ai_results_conversational_chat_reply_and_finally_safety(tmp_path):
    """Verifies that deliver_ai_results sends conversational chat reply and resets is_processing in finally block."""
    from bot.ai_handler import deliver_ai_results
    from bot.task_manager import TaskContext
    from core.probe import MediaMetadata
    from unittest.mock import AsyncMock, patch

    in_f = tmp_path / "orig.mp4"
    in_f.write_bytes(b"orig")
    out_f = tmp_path / "out_bw.mp4"
    out_f.write_bytes(b"out_bw_bytes")

    out_meta = MediaMetadata(
        file_path=out_f,
        file_name="out_bw.mp4",
        file_size_bytes=12,
        duration_seconds=5.0,
        container_format="mov,mp4",
        has_video=True,
        video_codec="h264",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=1000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=1128,
        is_high_bitrate=False,
        raw_info={}
    )

    mock_client = AsyncMock()
    mock_status_msg = AsyncMock()

    task_ctx = TaskContext(
        task_id="t_deliv",
        user_id=999,
        input_path=in_f,
        output_path=None,
        meta=out_meta,
        status_message=mock_status_msg,
        original_message_id=50
    )
    task_ctx.is_processing = True

    with patch("bot.ai_handler.probe_media", AsyncMock(return_value=out_meta)), \
         patch("bot.ai_handler.fast_upload", AsyncMock(return_value="doc_handle")), \
         patch("bot.ai_handler.register_file_cache", AsyncMock()):

        await deliver_ai_results(
            task_ctx=task_ctx,
            plan_title="Black and White",
            out_paths=[out_f],
            status_msg=mock_status_msg,
            client=mock_client,
            assistant_text="I converted your original video to black and white at 1080p FHD."
        )

        # Processing flag must be reset
        assert task_ctx.is_processing is False
        assert task_ctx.ai_session_active is True

        # send_file must be called for the video
        assert mock_client.send_file.called

        # send_message must be called with the assistant text
        assert mock_client.send_message.called
        send_args = mock_client.send_message.call_args[0]
        assert send_args[0] == 999
        assert "I converted your original video to black and white at 1080p FHD." in send_args[1]

        # status_msg.edit must be called with summary card
        assert mock_status_msg.edit.called


def test_ai_debug_logger_turn_logging(tmp_path):
    """Verifies that AIDebugLogger properly records turn JSON, Markdown log, and global index."""
    import json
    from core.ai.debug_logger import AIDebugLogger
    from core.probe import MediaMetadata

    debug_root = tmp_path / "debug-ai"
    logger = AIDebugLogger(debug_root)

    in_f = tmp_path / "src.mp4"
    in_f.write_bytes(b"data")
    before_meta = MediaMetadata(
        file_path=in_f,
        file_name="src.mp4",
        file_size_bytes=10000000,
        duration_seconds=12.0,
        container_format="mov,mp4",
        has_video=True,
        video_codec="hevc",
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate_kbps=6000,
        has_audio=True,
        audio_codec="aac",
        audio_channels=2,
        audio_sample_rate=48000,
        audio_bitrate_kbps=128,
        overall_bitrate_kbps=6128,
        is_high_bitrate=True,
        raw_info={}
    )

    primary_res = {
        "status": "success",
        "plan_title": "Black and White",
        "executed_commands": ["ffmpeg -i src.mp4 -vf hue=s=0 -c:v libx264 -c:a copy out.mp4"],
        "deliverables": [
            {
                "filename": "out_bw.mp4",
                "path": str(tmp_path / "out_bw.mp4"),
                "size_bytes": 5000000,
                "formatted_size": "4.8 MB",
                "resolution": "1080p FHD (1920x1080)",
                "duration": "00:12",
                "ffmpeg_command": "ffmpeg -i src.mp4 -vf hue=s=0 -c:v libx264 -c:a copy out.mp4",
                "ffprobe": {
                    "video_codec": "h264",
                    "audio_codec": "aac",
                    "resolution": "1080p FHD (1920x1080)",
                    "formatted_size": "4.8 MB",
                    "formatted_duration": "00:12"
                }
            }
        ]
    }

    turn_file = logger.log_turn(
        user_id=12345,
        task_id="abcde",
        turn_index=1,
        user_input="make my video black and white",
        model="openrouter/free",
        media_before=before_meta,
        assets_before={"v1": {"filename": "src.mp4", "media_type": "video", "meta": before_meta}},
        system_prompt="System instructions...",
        injected_skills="Recipe: hue=s=0",
        assistant_raw={"role": "assistant", "content": None, "tool_calls": [{"function": {"name": "execute_ffmpeg_plan", "arguments": "{}"}}]},
        tool_calls=[{"function": {"name": "execute_ffmpeg_plan", "arguments": "{}"}}],
        tool_results=[primary_res],
        primary_result=primary_res,
        final_reply="I converted your video to black and white.",
        elapsed_seconds=3.5
    )

    assert turn_file is not None
    assert turn_file.exists()

    # 1. Verify JSON file
    with open(turn_file, "r", encoding="utf-8") as f:
        data = json.load(f)
    assert data["user_id"] == 12345
    assert data["task_id"] == "abcde"
    assert data["turn_index"] == 1
    assert data["user_input"] == "make my video black and white"
    assert data["media_before"]["primary"]["video_codec"] == "hevc"
    assert data["execution"]["status"] == "success"
    assert "ffmpeg -i src.mp4" in data["execution"]["executed_commands"][0]
    assert data["media_after"]["comparison"]["size_reduction_percent"] == 50.0

    # 2. Verify session_log.md
    session_dir = debug_root / "12345_abcde"
    md_file = session_dir / "session_log.md"
    assert md_file.exists()
    md_content = md_file.read_text(encoding="utf-8")
    assert "AI Diagnostic Session: 12345_abcde" in md_content
    assert "make my video black and white" in md_content
    assert "ffmpeg -i src.mp4" in md_content
    assert "50.0% change" in md_content

    # 3. Verify sessions.jsonl
    idx_file = debug_root / "sessions.jsonl"
    assert idx_file.exists()
    idx_content = idx_file.read_text(encoding="utf-8")
    assert "12345" in idx_content
    assert "abcde" in idx_content


def test_ai_debug_logger_event_logging(tmp_path):
    """Verifies that AIDebugLogger properly records session events."""
    from core.ai.debug_logger import AIDebugLogger

    debug_root = tmp_path / "debug-ai"
    logger = AIDebugLogger(debug_root)

    logger.log_session_event(
        user_id=888,
        task_id="task_event",
        event_type="asset_uploaded",
        details={"filename": "sound.mp3", "media_type": "audio"}
    )

    event_file = debug_root / "888_task_event" / "events.jsonl"
    assert event_file.exists()
    content = event_file.read_text(encoding="utf-8")
    assert "asset_uploaded" in content
    assert "sound.mp3" in content




