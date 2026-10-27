# Design

## Context

See proposal.md — Why for the motivation. The current system stores all runtime AI state in Python memory: `VideoAIAgent.history` (list of dicts), `TaskContext.assets` (dict), `TaskContext.ai_agent` (object reference), and `TaskContext.ai_session_active` (bool). A bot process restart — whether from an unhandled exception, Termux OOM kill, or phone reboot — destroys every active session. The existing `get_or_restore_task_ctx` in `bot/callbacks.py` can partially recover the input file path from the `file_cache` DB table, but cannot restore the AI conversation or assets.

The bot runs on a Nokia 6.1 Plus (Snapdragon 636, 4GB RAM) under Termux Android 10. All processing uses aiosqlite for DB, asyncio for concurrency, and subprocess FFmpeg for media operations. No external services beyond OpenRouter (AI) and Telegram MTProto are involved.

## Goals / Non-Goals

**Goals:**
- Every active AI session survives a full bot restart with conversation history and assets intact
- FFmpeg output is validated against the operation plan before delivery — slop output is caught and retried
- All 7 confirmed bugs from the audit are fixed
- Users see what the AI decided before execution and receive honest progress (including during stalls)
- Resource exhaustion is detected before FFmpeg launches, not after it crashes
- Idle sessions are cleaned up automatically on a memory-constrained phone

**Non-Goals:**
- Multi-device session roaming (sessions are tied to one bot instance)
- Real-time streaming of FFmpeg output frames for visual preview
- Multi-worker process architecture — the bot stays single-process async
- Encryption of persisted session data (local SQLite, single-user phone)
- GPU/hardware transcoding changes — encoder selection stays as-is
- Full-featured undo/redo history with branching — only linear session flow

## Decisions

### D1: Session persistence via SQLite JSON column (not files)

**Decision:** Store conversation history and asset registry as JSON text columns in a new `ai_sessions` table.

**Why not separate JSON files?**
- The project already uses aiosqlite throughout. Adding file-based persistence would create two persistence mechanisms to maintain.
- SQLite handles concurrent reads/writes safely; file writes during async operations risk partial writes.
- Session data is small (typically 5-30 KB of history JSON per session).

**Why not Redis/external DB?**
- The bot runs on a phone under Termux. Adding Redis or PostgreSQL is impractical and unnecessary for a single-user/few-users system.

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS ai_sessions (
    user_id INTEGER,
    task_id TEXT,
    history_json TEXT,         -- JSON: list of message dicts
    assets_json TEXT,          -- JSON: asset registry dict
    preferred_model TEXT,
    input_path TEXT,
    is_active INTEGER DEFAULT 1,
    last_activity TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (user_id, task_id)
);
```

**Write strategy:** After each completed turn in `VideoAIAgent.run_turn()`, persist the full `self.history` and `self.assets`. This adds ~2-5ms of aiosqlite overhead per turn — negligible compared to the 1-35s OpenRouter API call.

### D2: Smart history compression with tiered windowing

**Decision:** Use a 3-tier history strategy instead of a flat `[-8:]` truncation:

1. **Compressed summary** (1 system message): For all turns older than the most recent 6 exchanges, generate a one-paragraph summary capturing: what assets exist, what operations were performed, and what the user's stated goals are. This is prepended as a system-role message right after the main system prompt.
2. **Recent window** (20 messages): Keep the most recent 20 raw messages, ensuring the AI has full detail on recent operations.
3. **Truncation guard**: When truncating, scan forward from the cut point to ensure the first message is `user` or `assistant` (never an orphaned `tool` message). If the first message would be `tool`, include one more message backward to capture the preceding `assistant` with `tool_calls`.

**Why 20 instead of 8?** A tool call cycle is 3 messages (user → assistant+tool_call → tool_result). 8 messages = 2.6 cycles. 20 messages = 6.6 cycles, which covers 6 back-and-forth interactions — a reasonable editing session.

**Why not unlimited?** Free OpenRouter models have 8K-32K context windows. The system prompt is ~1500 tokens. 20 messages of conversation ≈ 2000-4000 tokens. This leaves ample room for the model's response while staying under limits.

**Alternatives considered:**
- **Sliding summary only** (no raw history): Loses the AI's ability to see exact FFprobe output from recent operations. Rejected.
- **Token-counted window**: More accurate but requires a tokenizer library. Over-engineered for this use case.

### D3: Output quality gate as a post-FFmpeg validation step in SafeToolExecutor

**Decision:** Add validation logic inside `SafeToolExecutor.execute()` immediately after FFprobe verification (at `tools.py:545`), before adding the deliverable to the results list.

**Validation checks (in order):**
1. **Resolution check**: Compare output `width×height` against the `scale` parameter in the FFmpeg command. If the AI specified `-vf scale=-2:1080` but output is 640×360, flag as resolution mismatch.
2. **Duration check**: Compare `output_duration / input_duration`. If ratio is outside [0.98, 1.02] AND the FFmpeg command contains no `-ss`, `-to`, `-t`, or `trim` filters, flag as truncated/extended.
3. **Size inflation check**: For plans where `plan_title` contains "optimize", "compress", or "reduce", compare output size vs input size. If output > input, add a warning (not a failure).
4. **Stream presence check**: If input had video+audio but output has only video (and no `-an` flag was used), flag as stream loss.

**On failure:** Retry once with the same command. If retry also fails, return error to user with mismatch details. Never deliver a file that fails validation checks 1, 2, or 4.

**Why not ask the AI to validate?** That would cost another API call (tokens + latency). Validation is deterministic — comparing numbers — and belongs in code.

### D4: Remove global model mutation, use per-request DB lookup

**Decision:** Remove `settings.OPENROUTER_MODEL = model_name` from `OpenRouterClient.set_model()`. The `preferred_model` parameter already flows through `chat_completion()` → model selection. Each AI turn reads the user's model from DB via `get_user_ai_model(user_id)` and passes it as `preferred_model`.

**Why?** The current code mutates the global singleton, which affects all users. This is a classic shared-mutable-state bug. Per-request DB lookup costs ~1ms and is already implemented — the mutation is the redundant (and broken) part.

### D5: Atomic processing guard with asyncio.Lock

**Decision:** Replace the boolean `task_ctx.is_processing` check-and-set with a per-task `asyncio.Lock`. The lock is stored on `TaskContext` and acquired at the start of `execute_ai_query()`.

```python
# TaskContext gets a new field:
_processing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)

# In execute_ai_query():
if task_ctx._processing_lock.locked():
    await status_msg.edit("AI is currently processing a task...")
    return
async with task_ctx._processing_lock:
    # ... all processing ...
```

**Why Lock over bool?** `asyncio.Lock` is designed for exactly this pattern — it's atomic within the event loop and prevents the race window between `if is_processing` and `is_processing = True`.

### D6: Deferred workspace cleanup

**Decision:** The `settings.clean_ai_workplace()` call in `callbacks.py:375` is removed from the "Main Menu" callback. Workspace cleanup now happens in exactly two places:
1. **Explicit session close**: When the user clicks "Exit AI Session" (not "Main Menu").
2. **Idle timeout**: When the 30-minute idle timer fires.

**Why?** Currently, clicking "Main Menu" destroys intermediate files that are still referenced by `task_ctx.assets`. If the user returns to the AI session, `_resolve_input_path` crashes with `FileNotFoundError`. Deferring cleanup ensures files remain available as long as the session exists.

### D7: Progress heartbeat via stall detection timer

**Decision:** In `run_ffmpeg_with_progress`, track the timestamp of the last progress callback fire. Start an `asyncio.Task` that checks every 10 seconds: if `time.time() - last_progress_time > 30`, fire the progress callback with the current percent and a `stalled=True` flag. The caller (`ai_handler.py`) formats the stall message.

**Why a separate task instead of inline?** The progress loop in `engine.py` blocks on `proc.stdout.readline()`. If FFmpeg stops writing progress lines (e.g., during a complex seek), no callback fires. A background timer ensures the user always gets feedback.

### D8: Pre-flight resource checks in SafeToolExecutor

**Decision:** Before the FFmpeg loop in `SafeToolExecutor.execute()`, check:
1. `shutil.disk_usage(self.sandbox_dir).free` ≥ `estimated_output_size * 2`
2. For reverse/complex-filter ops: read `/proc/meminfo` for `MemAvailable` ≥ 300MB

These checks use existing stdlib functions (`shutil.disk_usage`, reading `/proc/meminfo`). No new dependencies.

### D9: Plan preview message before FFmpeg execution

**Decision:** In `bot/ai_handler.py`, after the AI returns a `execute_ffmpeg_plan` tool call but before calling `SafeToolExecutor.execute()`, format and display a brief plan summary:

```
📋 AI Plan: [plan_title]
├ Codec: libx264 CRF 22
├ Resolution: 1920×1080
├ Estimated time: ~45s
└ Starting transcoding...
```

The plan data is already available in the tool call arguments — it just needs formatting. This costs zero API tokens.

### D10: Database migration strategy

**Decision:** Use the existing `init_db()` pattern: `CREATE TABLE IF NOT EXISTS` for the new table, plus a `PRAGMA table_info` check for backward compatibility. This matches the existing migration at `db.py:72-77`.

No schema versioning system is needed — the project uses a single SQLite file with additive migrations, and only runs on one device.

## Risks / Trade-offs

**[Risk: Session JSON bloat]** → Conversations with many tool calls could grow large. Mitigation: Smart history compression caps the stored history to the compressed summary + 20 recent messages. Maximum session JSON size ≈ 50KB.

**[Risk: Stale session restore]** → A restored session references file paths that may no longer exist (disk cleanup, manual deletion). Mitigation: Validate `input_path` exists before restoring. If missing, inform user and request re-upload.

**[Risk: Output validation false positives]** → Legitimate operations (e.g., adding audio to video changes resolution slightly due to pixel format conversion) could trigger validation. Mitigation: Use 2% tolerance on duration, allow ±2px on resolution, and never block delivery on size inflation (only warn).

**[Risk: Idle timeout too aggressive]** → 30 minutes may interrupt users who step away briefly during long encodes. Mitigation: The timer resets on any activity including FFmpeg progress callbacks, so active processing never triggers the timeout. Only genuinely idle sessions (no user messages, no active processing) are cleaned up.

**[Risk: asyncio.Lock starvation]** → If a task hangs indefinitely, the Lock is never released. Mitigation: Add a `asyncio.wait_for` wrapper with a generous timeout (10 minutes) on the Lock acquisition, and force-release on timeout.

## Migration Plan

1. **Database migration**: The new `ai_sessions` table is additive (`CREATE TABLE IF NOT EXISTS`). No existing tables are modified. Running the updated `init_db()` on the existing database is safe and non-destructive.
2. **Rollback**: If the new code has issues, reverting to the old code simply means the new table goes unused. No data loss.
3. **Deployment**: Replace the Python files and restart the bot. First restart will create the new table. Existing in-memory sessions will be lost (as they always were), but all future sessions will be persisted.
