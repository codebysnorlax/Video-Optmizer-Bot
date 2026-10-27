# Tasks

## 1. Database Schema & Session Persistence Layer

- [ ] 1.1 Add `ai_sessions` table creation to `database/db.py` `init_db()` with the schema from design.md D1 (`user_id`, `task_id`, `history_json`, `assets_json`, `preferred_model`, `input_path`, `is_active`, `last_activity`, `created_at`, PRIMARY KEY `(user_id, task_id)`). Verify by running `init_db()` on the existing database and confirming the table exists via `PRAGMA table_info(ai_sessions)`.

- [ ] 1.2 Add session persistence functions to `database/models.py`: `save_ai_session(user_id, task_id, history, assets, model, input_path)` and `load_ai_session(user_id, task_id) -> Optional[dict]` and `delete_ai_session(user_id, task_id)` and `get_active_sessions_for_user(user_id) -> List[dict]`. `save_ai_session` serializes `history` and `assets` to JSON text. `load_ai_session` returns the deserialized dict or None. Verify by writing a session, loading it back, and confirming round-trip fidelity of history and assets.

- [ ] 1.3 Add `update_session_activity(user_id, task_id)` to `database/models.py` that updates `last_activity = CURRENT_TIMESTAMP` for idle timeout tracking. Verify by saving a session, calling update, and confirming `last_activity` advanced.

## 2. Smart History Compression & Truncation Guard

- [ ] 2.1 In `core/ai/agent.py`, create a `compress_old_history(history: list, keep_recent: int = 20) -> tuple[str, list]` function that: (a) splits history into old (everything before the last `keep_recent` messages) and recent (last `keep_recent` messages), (b) builds a one-paragraph summary of old turns listing all asset keys, operations performed, and user goals, (c) returns `(summary_text, recent_messages)`. Verify by passing a 30-message history and confirming the summary mentions asset keys and the recent list has exactly 20 messages.

- [ ] 2.2 Add a `guard_truncation_boundary(messages: list) -> list` function in `core/ai/agent.py` that checks if the first message in the list has `role == "tool"`. If so, scans backward from the cut point in the full history to include the preceding `assistant` message that contains `tool_calls`. Verify by constructing a history where truncation would start on a `tool` message and confirming the guard pulls in the preceding `assistant`.

- [ ] 2.3 Update `VideoAIAgent.run_turn()` to replace `sanitized_history[-8:]` with the new tiered strategy: prepend the compressed summary as a system-role message after the main system prompt, then include the guarded recent window of 20 messages. Update both the primary API call (line ~227) and the follow-up completion call (line ~384). Verify by running a session with 10+ turns and confirming the API payload uses the compressed + windowed history (check via `ai_debug_logger` output).

## 3. Session Persistence Integration into AI Workflow

- [ ] 3.1 In `VideoAIAgent.run_turn()`, after the turn completes (after debug logging, before returning the result dict), call `save_ai_session(self.user_id, self.task_id, self.history, self.assets, self.preferred_model, str(self.input_path))`. Wrap in try/except so persistence failure never interrupts the main workflow. Verify by running an AI turn and confirming a row exists in `ai_sessions` with valid JSON.

- [ ] 3.2 In `bot/ai_handler.py`, create a `restore_ai_session(task_ctx, client) -> Optional[VideoAIAgent]` function that: (a) calls `load_ai_session(task_ctx.user_id, task_ctx.task_id)`, (b) if a session exists, validates that `input_path` still exists on disk, (c) creates a `VideoAIAgent` with the stored `preferred_model`, restores `history` and `assets`, and (d) returns the agent. If `input_path` is missing, returns None and sends a message asking the user to re-send the video. Verify by persisting a session, clearing in-memory state, and confirming `restore_ai_session` successfully reconstructs the agent.

- [ ] 3.3 Update `bot/callbacks.py` `get_or_restore_task_ctx()` and the AI session activation callback to check for a persisted session via `restore_ai_session()` when `task_ctx.ai_agent is None` but `task_ctx.ai_session_active is True`. Also call it when the task_ctx itself is restored from file_cache (the existing partial-restore path). Verify by simulating a bot restart scenario: persist a session, remove the task from `task_manager._active_tasks`, and confirm clicking an AI button restores the full session.

- [ ] 3.4 Call `delete_ai_session(user_id, task_id)` when the user explicitly exits the AI session (the "Exit AI" or session-close callback). Do NOT call it on "Main Menu" navigation. Verify by confirming that navigating to Main Menu leaves the `ai_sessions` row intact, while explicitly exiting removes it.

## 4. Bug Fix: Remove Global Model Mutation

- [ ] 4.1 In `core/ai/client.py`, modify `OpenRouterClient.set_model()` to remove the line `settings.OPENROUTER_MODEL = model_name`. The method should only update `self.model` on the client instance. Verify by calling `set_model("x")` and confirming `settings.OPENROUTER_MODEL` is unchanged.

- [ ] 4.2 In `bot/callbacks.py` (the model selection callback around line 323), after saving the model to DB via `set_user_ai_model()`, update only the active `task_ctx.ai_agent.preferred_model` if an AI agent exists — do NOT mutate the global singleton. Verify by having two user contexts and confirming each sees their own model.

## 5. Bug Fix: Atomic Processing Guard

- [ ] 5.1 Add a `_processing_lock: asyncio.Lock` field to `TaskContext` in `bot/task_manager.py` using `field(default_factory=asyncio.Lock)`. Verify by creating a `TaskContext` and confirming `_processing_lock` is an asyncio.Lock instance.

- [ ] 5.2 In `bot/ai_handler.py` `execute_ai_query()`, replace the `if task_ctx.is_processing` boolean check-and-set with `if task_ctx._processing_lock.locked(): return` followed by `async with task_ctx._processing_lock:` wrapping the entire processing block. Remove the `task_ctx.is_processing = True/False` assignments. Verify by confirming two concurrent `execute_ai_query` calls for the same task result in one processing and one "AI is busy" response.

## 6. Bug Fix: Follow-up Timeout & Task Recording

- [ ] 6.1 In `core/ai/agent.py`, increase the follow-up completion timeout from `12.0` to `30.0` seconds at line ~385-392. Verify by confirming the `asyncio.wait_for` timeout parameter is 30.0.

- [ ] 6.2 In `bot/ai_handler.py`, add a `record_task_start()` call at the beginning of AI manual mode processing (in `execute_ai_query()`, after the AI returns a tool call but before execution). Use `action="AI Manual"` and the input file's metadata for `file_name`, `input_size`, `duration`. Verify by running an AI operation and confirming a row appears in the `tasks` table with `status='processing'`.

## 7. Bug Fix: Deferred Workspace Cleanup & History Sanitization

- [ ] 7.1 In `bot/callbacks.py`, remove the `settings.clean_ai_workplace(task_ctx.user_id, task_id)` call from the "Main Menu" callback (around line 375). Move workspace cleanup to only occur in: (a) the explicit "Exit AI Session" callback, and (b) the idle timeout handler (Task Group 9). Verify by clicking "Main Menu" during an AI session and confirming the `Ai_Workplace/{user_id}_{task_id}/` directory still exists on disk.

- [ ] 7.2 In `core/ai/agent.py` `sanitize_message_history()`, add a post-sanitization check: after building the cleaned list, if the first message has `role == "tool"`, prepend a synthetic `assistant` message with `content = "I performed the previous operation."` to prevent OpenRouter from rejecting the conversation. Verify by constructing a history that starts with a `tool` message and confirming the sanitizer inserts the assistant message.

## 8. Output Quality Gate

- [ ] 8.1 Create a `validate_output(input_meta, output_meta, ffmpeg_cmd_str, plan_title) -> tuple[bool, str, list[str]]` function in `core/ai/tools.py` that returns `(is_valid, error_message, warnings)`. Implement the 4 checks from design.md D3: resolution match (±2px tolerance), duration integrity (2% tolerance, skip if `-ss`/`-to`/`-t`/`trim` present in cmd), size inflation warning for optimization plans, and stream presence check (no unintended `-an`). Verify by unit-testing with: (a) matching output → valid, (b) 360p output when 1080p expected → invalid, (c) 5s output from 120s input → invalid, (d) larger output from optimization → valid with warning.

- [ ] 8.2 Integrate `validate_output()` into `SafeToolExecutor.execute()` after the FFprobe step (after line ~563 in `tools.py`). On validation failure, retry the FFmpeg command once. If retry also fails, return an error result with the mismatch details. On warnings only, include them in the deliverable dict as a `warnings` list. Verify by simulating a validation failure and confirming the retry happens and the error is returned if both attempts fail.

## 9. Session Lifecycle: Idle Timeout & Workspace Discovery

- [ ] 9.1 Add a `last_activity_time: float` field to `TaskContext` in `bot/task_manager.py`, initialized to `time.time()`. Update it in `execute_ai_query()` at the start of each user interaction. Verify by sending a message and confirming `last_activity_time` updates.

- [ ] 9.2 Create an `async def _idle_session_reaper()` coroutine in `bot/task_manager.py` that runs in a background `asyncio.Task` (started in `run_bot.py` after the bot starts). It loops every 60 seconds, iterating all `_active_tasks` and finding any with `ai_session_active == True` and `time.time() - last_activity_time > 1800` (30 minutes). For each, it: (a) sets `ai_session_active = False`, (b) clears `ai_agent` reference, (c) calls `settings.clean_ai_workplace(user_id, task_id)`, (d) calls `delete_ai_session(user_id, task_id)`, (e) attempts to send a timeout message to the user. Verify by setting `last_activity_time` to 31 minutes ago and confirming the reaper fires and cleans up.

- [ ] 9.3 Add a `list_workspace_files` tool schema to `AI_TOOLS_SCHEMA` in `core/ai/tools.py` and implement the handler in `SafeToolExecutor`. The tool takes no arguments and returns a JSON list of `{filename, size_bytes, media_type}` for all files in `self.sandbox_dir`. Verify by creating test files in a sandbox dir and confirming the tool returns them.

## 10. Resource Pre-flight Checks

- [ ] 10.1 Create a `check_disk_space(sandbox_dir: Path, estimated_output_size: int) -> tuple[bool, str]` function in `core/ai/tools.py`. Uses `shutil.disk_usage(sandbox_dir).free` and requires `free >= estimated_output_size * 2`. Estimates output size as `input_file_size * 1.5` for video re-encoding or `input_file_size * 0.1` for audio extraction. Returns `(ok, message)`. Verify by mocking disk usage to return low space and confirming the check fails with a descriptive message.

- [ ] 10.2 Create a `check_available_ram(min_mb: int = 300) -> tuple[bool, int]` function in `core/ai/tools.py` that reads `/proc/meminfo` for `MemAvailable` (or falls back to `psutil` if available, or returns `(True, 0)` if neither works). Returns `(sufficient, available_mb)`. Verify by reading the function's output on the current system and confirming it returns a reasonable value.

- [ ] 10.3 Integrate both checks into `SafeToolExecutor.execute()` before the FFmpeg command loop. Call `check_disk_space` for all operations. Call `check_available_ram` only when the FFmpeg command contains `reverse`, `areverse`, or complex filter graphs with multiple inputs. On failure, return an error result with the specific resource constraint. Verify by confirming the pre-flight runs before FFmpeg and the error message reaches the user.

## 11. Plan Preview & Progress Heartbeat

- [ ] 11.1 In `bot/ai_handler.py`, after the AI returns an `execute_ffmpeg_plan` tool call but before calling `SafeToolExecutor.execute()`, extract `plan_title`, codec, resolution, and CRF from the tool call arguments. Format a plan preview message (📋 AI Plan: ...) and display it via the `ThrottledMessageUpdater`. Estimate processing time as `input_duration / hardware_speed_factor` where speed factor is ~1.5x for libx264 on SDM636. Verify by running an AI operation and confirming the plan preview appears in the Telegram message before the progress bar starts.

- [ ] 11.2 In `core/engine.py` `run_ffmpeg_with_progress()`, add stall detection: track `last_callback_time` as a nonlocal variable. Create a background `asyncio.Task` that checks every 10 seconds: if `time.time() - last_callback_time > 30` and the process is still running, fire the `progress_callback` with the last known percent and an additional `stalled=True` keyword. Cancel the stall-detection task when the FFmpeg process completes. Verify by simulating a stalled FFmpeg (e.g., long seek) and confirming the heartbeat fires after 30s.

- [ ] 11.3 In `bot/ai_handler.py`, update the transcode progress callback to handle the `stalled=True` kwarg: when stalled, display "⏳ Still processing — FFmpeg is working on a complex segment..." instead of the normal progress bar. When progress resumes (`stalled=False`), switch back to normal progress display. Verify by confirming the stall message appears and then reverts to normal progress.

## 12. Integration Verification

- [ ] 12.1 End-to-end test: Send a video to the bot, activate AI session, send "optimize this video", confirm: (a) plan preview appears, (b) progress bar updates during FFmpeg, (c) output quality gate runs and passes, (d) session is persisted to `ai_sessions` after the turn, (e) deliverable is uploaded to Telegram with correct metadata.

- [ ] 12.2 Crash recovery test: During an active AI session (after at least one successful turn), manually kill and restart the bot process. Confirm: (a) clicking an AI button for the same task_id restores the session from SQLite, (b) conversation history is intact, (c) the user can send a new prompt and the AI continues with context from previous turns.

- [ ] 12.3 Multi-bug regression test: Verify all 7 bug fixes together: (a) send 10+ messages in a session and confirm AI retains context beyond 8 messages, (b) test follow-up completion on a slow model and confirm it doesn't timeout at 12s, (c) verify `tasks` table has a row for the AI operation, (d) switch model in one user context and confirm global default is unchanged, (e) send two messages rapidly and confirm only one is processed, (f) click "Main Menu" and confirm workspace files survive, (g) construct a history starting with a tool message and confirm no API rejection.

## Workflow follow-up

- Archive the change after all tasks are verified and the bot has been tested in at least one full session cycle.
- Verify the archived result with `openspec validate`.
