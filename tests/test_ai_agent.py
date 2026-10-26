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
    assert len(session_menu) >= 3
    assert any(b"ai_quick:lag:t1" in btn.type.data for row in session_menu for btn in row)
    assert any(b"main_menu:t1" in btn.type.data for row in session_menu for btn in row)

    fix_menu = build_ai_fix_menu("t1", "recommended")
    assert b"ai_apply:recommended:t1" in fix_menu[0][0].type.data


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
