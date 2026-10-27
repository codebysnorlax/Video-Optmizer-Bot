# Spec Delta

## Purpose

Persists AI conversation history, session asset references, and agent configuration to SQLite so active sessions survive bot restarts, phone reboots, and unhandled exceptions and can be transparently restored without user intervention.

## ADDED Requirements

### Requirement: Conversation history persistence
The system SHALL persist the complete AI message history (user, assistant, tool call, and tool result messages) to SQLite after each turn completes.

#### Scenario: History saved after successful tool execution
- **WHEN** the AI agent completes a turn that includes a tool call and receives a tool result
- **THEN** the full message history (including the tool call and tool result) is written to the `ai_sessions` table keyed by `(user_id, task_id)`

#### Scenario: History saved after pure text reply
- **WHEN** the AI agent completes a turn with a conversational text reply (no tool calls)
- **THEN** the updated message history is written to the `ai_sessions` table

#### Scenario: History saved after API error
- **WHEN** the AI agent encounters an OpenRouter API error during a turn
- **THEN** the history as it existed before the failed call is still persisted (no corruption of prior state)

### Requirement: Session asset persistence
The system SHALL persist the session asset registry (asset keys, file paths, media types, and metadata) to SQLite alongside the conversation history.

#### Scenario: Asset registry saved after deliverable registered
- **WHEN** a new deliverable is registered as a session asset (e.g., `[v2]` or `[a1]`)
- **THEN** the updated asset registry is written to the `ai_sessions` table

#### Scenario: New upload asset persisted
- **WHEN** the user uploads a new audio or video file into an active AI session
- **THEN** the asset is added to the registry and the registry is persisted before the auto-prompt is sent to the AI

### Requirement: Transparent session restore
The system SHALL transparently restore the AI agent, conversation history, and session assets from SQLite when a user interacts with a session that is no longer in memory.

#### Scenario: Session restored after bot restart
- **WHEN** the bot process restarts and a user clicks an AI session button or sends a text message referencing an existing task_id
- **THEN** the system loads the conversation history and asset registry from `ai_sessions`, recreates the `VideoAIAgent` with the stored configuration, and resumes the session without asking the user to re-send the video

#### Scenario: Session restore fails gracefully on missing files
- **WHEN** the stored `input_path` for a session no longer exists on disk
- **THEN** the system informs the user that the original file is no longer available and asks them to re-send the video, rather than crashing

### Requirement: Agent configuration persistence
The system SHALL persist the agent's preferred model and the original input file path as part of the session record.

#### Scenario: Model preference restored
- **WHEN** a session is restored from SQLite
- **THEN** the `VideoAIAgent` is initialized with the model that was active when the session was last saved, not the current global default

### Requirement: Session cleanup on explicit close
The system SHALL delete persisted session data from SQLite only when the user explicitly exits the AI session or the session reaches its idle timeout.

#### Scenario: Session data deleted on explicit exit
- **WHEN** the user navigates to "Main Menu" and the AI session is deactivated
- **THEN** the `ai_sessions` row for that `(user_id, task_id)` is deleted

#### Scenario: Session data NOT deleted on bot restart
- **WHEN** the bot process restarts
- **THEN** all `ai_sessions` rows remain in the database for future restoration
