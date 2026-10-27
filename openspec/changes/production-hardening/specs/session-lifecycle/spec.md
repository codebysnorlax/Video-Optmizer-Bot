# Spec Delta

## Purpose

Manages AI session idle timeout, workspace cleanup policy, progress heartbeat during stalled operations, and plan preview so users always know what is happening and resources are reclaimed predictably.

## ADDED Requirements

### Requirement: Idle session timeout
The system SHALL automatically deactivate AI sessions that have been idle (no user messages or tool executions) for 30 minutes and reclaim their in-memory resources.

#### Scenario: Session idle for 30 minutes
- **WHEN** no user message or AI tool execution occurs for 30 minutes in an active AI session
- **THEN** the system sets `ai_session_active = False`, clears the in-memory `VideoAIAgent` reference, and sends a message: "AI session timed out after 30 minutes of inactivity. You can resume anytime."

#### Scenario: Session activity resets timer
- **WHEN** the user sends a message to the AI session
- **THEN** the idle timer resets to 30 minutes from that point

#### Scenario: Timed-out session can be resumed
- **WHEN** the user sends a message or clicks a button for a timed-out session
- **THEN** the system restores the session from persisted state (if available) or creates a fresh session with the same input file

### Requirement: Deferred workspace cleanup
The system SHALL NOT delete the `Ai_Workplace/{user_id}_{task_id}/` sandbox directory when the user navigates to "Main Menu". Cleanup occurs only on explicit session close, idle timeout, or explicit file deletion.

#### Scenario: Main Menu navigation preserves workspace
- **WHEN** the user clicks "Main Menu" during an active AI session
- **THEN** the workspace directory and its intermediate files are preserved, and the user can return to the AI session and reference those files

#### Scenario: Workspace cleaned on idle timeout
- **WHEN** the idle timeout fires for a session
- **THEN** the workspace directory is deleted if no files in it are referenced by the asset registry

### Requirement: Progress heartbeat during FFmpeg stalls
The system SHALL detect when FFmpeg progress has not advanced for more than 30 seconds and send a heartbeat message so the user knows processing is still active.

#### Scenario: FFmpeg stalls during complex segment
- **WHEN** the FFmpeg progress callback has not fired for 30 seconds during an active transcode
- **THEN** the system updates the Telegram message with: "Still processing — FFmpeg is working on a complex segment. This may take a moment."

#### Scenario: Normal progress resumes after heartbeat
- **WHEN** FFmpeg progress resumes after a stall heartbeat was sent
- **THEN** the system returns to normal progress bar updates without any special message

### Requirement: Plan preview before execution
The system SHALL display a brief summary of the AI's decided FFmpeg plan (operation name, target codec, resolution, CRF, estimated time) to the user before starting the FFmpeg subprocess.

#### Scenario: Plan preview shown before transcode
- **WHEN** the AI generates an FFmpeg plan with `execute_ffmpeg_plan`
- **THEN** the system displays: "AI Plan: [plan_title] | [codec] | [resolution target] | CRF [value]" and waits 1 second before starting the FFmpeg process

#### Scenario: Estimated time shown when calculable
- **WHEN** the input duration and hardware speed factor are known
- **THEN** the plan preview includes an estimated processing time (e.g., "Estimated: ~45s")

### Requirement: Smart history compression
The system SHALL compress conversation turns older than the most recent 6 user-assistant exchanges into a compact summary block, preserving the key decisions and asset state without consuming excessive tokens.

#### Scenario: History compressed after 6 exchanges
- **WHEN** the conversation history exceeds 6 user-assistant exchange pairs (12+ messages)
- **THEN** older turns are summarized into a single system message: "Previous session context: [v1] original uploaded. [v2] optimized to 1080p CRF 22. User requested audio replacement."

#### Scenario: Compressed summary always includes asset state
- **WHEN** history compression runs
- **THEN** the summary includes all registered asset keys and their descriptions (e.g., "[v1] source 4K, [v2] optimized 1080p, [a1] uploaded audio.mp3")

### Requirement: Atomic processing guard
The system SHALL use a per-task asyncio Lock to prevent concurrent message processing within the same AI session, replacing the non-atomic boolean `is_processing` check.

#### Scenario: Two messages arrive simultaneously
- **WHEN** two user messages arrive within 10ms for the same task
- **THEN** only one is processed; the other receives "AI is currently busy processing a task"

### Requirement: Workspace file discovery tool
The system SHALL provide a `list_workspace_files` tool that the AI can call to discover intermediate files in the sandbox.

#### Scenario: AI inspects workspace contents
- **WHEN** the AI calls `list_workspace_files`
- **THEN** it receives a list of filenames, sizes, and media types for all files in `Ai_Workplace/{user_id}_{task_id}/`
