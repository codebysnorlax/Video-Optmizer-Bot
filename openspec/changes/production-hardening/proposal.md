# Proposal

## Why

The Video Optimizer Bot has a solid architecture but every piece of runtime state — AI conversation history, session assets, processing context — lives exclusively in Python memory. A bot restart, phone reboot, or unhandled exception erases all active sessions with no recovery path. Additionally, the AI workflow has 7 confirmed bugs (history window too small, follow-up timeout too short, missing task records, global model mutation, race condition on `is_processing`, workplace cleanup destroying chained assets, and invalid history sequences after truncation) and delivers outputs without verifying they match what the user requested. These gaps must be closed before the system can run unattended or serve more than one user reliably.

## What Changes

- **Session persistence**: Persist AI conversation history, session assets, and agent configuration to SQLite so sessions survive bot restarts and can be restored transparently.
- **Output quality gate**: Validate FFmpeg output (resolution, duration, file size) against the operation plan before delivering to the user. Auto-retry once on detectable failures; report and discard slop output instead of silently delivering it.
- **Bug fixes (7 confirmed)**:
  - Increase AI context window from 8 to 20 messages with smart compression for older turns.
  - Raise follow-up completion timeout from 12s to 30s for free-model tolerance.
  - Call `record_task_start` for AI manual mode operations.
  - Remove global model mutation from `ai_client.set_model()`; use per-user DB lookup only.
  - Make `is_processing` guard atomic with an asyncio Lock.
  - Defer workplace cleanup until explicit user action or session timeout, not on "Main Menu" navigation.
  - Guard history truncation to never start on an orphaned `tool` message.
- **Pre-flight resource checks**: Verify disk space and RAM before launching FFmpeg. Fail early with a clear message instead of letting FFmpeg crash with cryptic errors.
- **User-facing plan preview**: Show the AI's decided FFmpeg plan (operation, codec, resolution, CRF) to the user before execution starts, with estimated processing time.
- **Idle session timeout**: Auto-cleanup AI sessions after 30 minutes of inactivity to free memory on resource-constrained devices.
- **Workspace file discovery tool**: Add a `list_workspace_files` tool so the AI can inspect what intermediate files exist in the sandbox.
- **Progress heartbeat during stalls**: Detect when FFmpeg progress stalls for >30 seconds and send a "Still processing, FFmpeg is working on a complex segment..." heartbeat so the user doesn't think the bot died.
- **Smart history compression**: Summarize older conversation turns into a compact context block so the AI retains session knowledge without consuming excessive tokens.

## Capabilities

### New Capabilities
- `session-persistence`: Durable storage and transparent recovery of AI conversation state, session assets, and agent configuration across bot restarts.
- `output-quality-gate`: Post-processing validation of FFmpeg output against the operation plan, with auto-retry on detectable quality failures and slop detection.
- `resource-preflight`: Pre-execution checks for disk space and available RAM, with early failure and user-friendly messaging.
- `session-lifecycle`: Idle timeout, workspace cleanup policy, and progress heartbeat for long-running operations.

### Modified Capabilities

_None — no existing specs exist to modify._

## Impact

- **Database**: New tables `ai_sessions` (conversation history JSON), `ai_session_assets` (asset registry), and a column on `user_preferences` for session config. Migration required on `database/db.py`.
- **Core AI**: Changes to `core/ai/agent.py` (history management, smart compression, plan preview), `core/ai/client.py` (remove global mutation), `core/ai/tools.py` (output validation, workspace listing, resource checks).
- **Bot layer**: Changes to `bot/ai_handler.py` (session restore, plan preview, heartbeat), `bot/callbacks.py` (deferred cleanup, atomic processing guard), `bot/task_manager.py` (idle timeout, session TTL).
- **Engine**: Changes to `core/engine.py` (stall detection callback).
- **Dependencies**: No new external dependencies — all changes use existing `aiosqlite`, `asyncio`, `pathlib`, and `shutil`.
