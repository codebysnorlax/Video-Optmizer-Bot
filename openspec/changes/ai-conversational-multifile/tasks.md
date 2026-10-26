# Tasks

## 1. Runtime Bug Fixes

- [x] 1.1 Fix `record_task_complete` missing `elapsed_time` parameter in `bot/ai_handler.py` and verify via test.
- [x] 1.2 Fix CallbackQuery `event.message` attribute errors in `bot/callbacks.py` by using `await event.get_message()` and verify via test.

## 2. Multi-Asset Representation & Session Tracking

- [x] 2.1 Extend `TaskContext` in `bot/task_manager.py` with an `assets` dictionary mapping asset tokens (`v1`, `a1`) to file paths and probed metadata.
- [x] 2.2 Update `bot/media_handler.py` to register newly received audio or video media into the user's active AI session if one is already open.
- [x] 2.3 Update `core/ai/agent.py` to inject the active session's available assets into the system prompt context.

## 3. Enhanced Tool Schemas & Compound Execution

- [x] 3.1 Expand `AI_TOOLS_SCHEMA` in `core/ai/tools.py` to support multi-input mapping (`inputs`), stream extraction flags (`-vn`, `-an`), and multiple output deliverable specifications.
- [x] 3.2 Update `SafeToolExecutor.run_execute_ffmpeg` in `core/ai/tools.py` to execute compound operations sequentially, validate sandbox boundaries, and return all generated output paths.
- [x] 3.3 Add skill definitions in `core/ai/skills.py` covering audio extraction, muting, soundtrack replacement, and multi-file remuxing.

## 4. Multi-Deliverable Delivery Pipeline & Conversational UX

- [x] 4.1 Implement `deliver_ai_results` in `bot/ai_handler.py` to sequentially probe, format progress for, and upload multiple output deliverables to Telegram.
- [x] 4.2 Reframe AI session greeting and menus in `bot/ai_handler.py` to emphasize open conversational problem-solving rather than fixed button presets.

## 5. Verification & Testing

- [x] 5.1 Add unit tests in `tests/test_ai_agent.py` for audio extraction (`-vn`), video muting (`-an`), audio replacement, and multi-output delivery.
- [x] 5.2 Execute full test suite `pytest tests/ -v` and verify all tests pass without errors.
