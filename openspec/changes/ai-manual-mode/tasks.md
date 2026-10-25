# Tasks

## 1. Environment & Configuration Setup

- [ ] 1.1 Add `httpx` to `requirements.txt` and verify installation succeeds without compilation errors on Termux/Linux
- [ ] 1.2 Update `config/settings.py` and `.env.example` to include `OPENROUTER_API_KEY`, `OPENROUTER_MODEL`, and `AI_WORKPLACE_DIR`, verifying default configuration loads correctly
- [ ] 1.3 Create the sandbox directory `storage/Ai_Workplace/` and add directory creation/cleanup helpers in `config/settings.py`, verifying directories are created with correct permissions

## 2. Modular Skills Architecture

- [ ] 2.1 Audit existing `skills/ffmpeg/` and create new skill directories `skills/video_diagnosis/SKILL.md` and `skills/creative_fx/SKILL.md` (covering reverse, aspect ratio changes, and mobile lag diagnostics), verifying valid markdown and YAML frontmatter
- [ ] 2.2 Implement `core/ai/skills.py` (`SkillRegistry`) to discover, parse, and dynamically extract relevant skill sections by trigger or keyword, verifying with a unit test that queries return expected recipe excerpts
- [ ] 2.3 Implement memory safety constraint rules in `skills/creative_fx/SKILL.md` to guard against high-RAM operations (e.g. reverse on long or high-res videos), verifying constraint checks pass unit testing

## 3. Core AI Agent & Safe Tool Dispatcher

- [ ] 3.1 Implement `core/ai/client.py` using `httpx` to handle async communication with OpenRouter's chat completions API with tool calling support and model fallback cascade, verifying API communication with mock responses
- [ ] 3.2 Implement `core/ai/tools.py` with vetted tool functions: `diagnose_video`, `execute_ffmpeg_plan`, and `verify_media`, restricting file operations strictly to `storage/Ai_Workplace/<user_id>_<task_id>/`
- [ ] 3.3 Implement `core/ai/agent.py` ReAct reasoning loop that manages multi-turn conversation context, executes tool calls, parses results, and verifies completion with unit tests for diagnostic and action prompts

## 4. Telegram UI & State Integration

- [ ] 4.1 Update `bot/keyboards.py` to activate `3. AI Manual` button, and add an AI session keyboard with `Exit AI Mode` and action confirmation buttons, verifying keyboard generation in unit tests
- [ ] 4.2 Extend `TaskContext` in `bot/task_manager.py` to track active AI session state, conversation history, and sandbox path
- [ ] 4.3 Update `bot/callbacks.py` to handle the `ai_manual` button click, initialize the AI session card, and prompt the user for input
- [ ] 4.4 Update `bot/client.py` and `bot/handlers.py` to route user text messages to the active AI agent when an AI session is active for a task, verifying routing in integration tests
- [ ] 4.5 Connect AI-generated FFmpeg transcode plans to `core/engine.py` and `bot/progress.py` to provide live Telegram progress reporting during AI executions, verifying progress updates during test runs

## 5. End-to-End Testing & Verification

- [ ] 5.1 Implement comprehensive unit and integration tests in `tests/test_ai_agent.py` covering diagnostic prompts, downscaling plans, reverse guardrails, and sandbox isolation
- [ ] 5.2 Run the full test suite (`pytest tests/ -v`) to verify that all existing tests and new AI manual tests pass with 100% success
- [ ] 5.3 Update `README.md` to document the new AI Manual feature, OpenRouter setup, and sandbox folder structure
