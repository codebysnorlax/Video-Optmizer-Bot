# Spec Delta

## Purpose

Enables natural language video diagnosis, conversational assistance, dynamic skill-based recipe selection, and sandboxed FFmpeg execution for user video tasks.

## ADDED Requirements

### Requirement: Activate AI Manual session
The system SHALL provide an interactive AI Manual mode for any active video task, allowing the user to submit natural language queries and editing requests.

#### Scenario: User triggers AI Manual button
- **WHEN** user clicks "3. AI Manual" on a probed video message
- **THEN** system initializes an AI conversation context for the task, prompts the user for instructions, and updates the inline keyboard with an "Exit AI Mode" option

#### Scenario: Expired or deleted task session
- **WHEN** user clicks "3. AI Manual" for a video file that is no longer on disk
- **THEN** system responds with an alert indicating the file or session has expired and prompts the user to re-upload the video

### Requirement: Video diagnostic analysis
The system SHALL parse probed video and audio metadata to diagnose playback bottlenecks, severe bitrate bloat, and mobile device incompatibilities upon user inquiry.

#### Scenario: User queries playback lag or stutter
- **WHEN** user asks why a high-resolution or high-bitrate video lags on their device
- **THEN** system inspects codec, resolution, framerate, and bitrate, explains the decoding bottleneck in plain language, and suggests a concrete optimization

#### Scenario: User queries excessive file size
- **WHEN** user asks why a short clip has a disproportionately large file size
- **THEN** system compares duration against overall bitrate, identifies uncompressed or bloated streams, and explains potential compression savings

### Requirement: Natural language editing planning
The system SHALL map natural language video transformation prompts to validated FFmpeg filter and codec recipes using the indexed skills repository.

#### Scenario: Downscaling request with quality preservation
- **WHEN** user requests downscaling from 4K to 1080p without perceptible quality loss
- **THEN** system selects a high-fidelity CRF range, optimal scaling filter, and target H.264 profile, presenting the planned transcode to the user

#### Scenario: Aspect ratio transformation
- **WHEN** user requests changing the aspect ratio to 9:16 or 16:9
- **THEN** system determines appropriate padding or cropping filter arguments, preserving video sync without distortion

#### Scenario: Media reversal request
- **WHEN** user requests reversing video and audio playback
- **THEN** system configures video and audio reverse filters, verifying duration constraints before execution

### Requirement: Sandboxed FFmpeg execution
The system SHALL execute all AI-directed media operations exclusively within an isolated task directory under `storage/Ai_Workplace/` without modifying or deleting original media.

#### Scenario: Successful sandboxed transcode with live progress
- **WHEN** user confirms or triggers a planned video transformation
- **THEN** system runs FFmpeg writing strictly to `storage/Ai_Workplace/<user_id>_<task_id>/`, reports live percentage progress in Telegram, and probes output integrity before delivery

#### Scenario: Transcode failure handling
- **WHEN** an FFmpeg command in the sandbox fails or encounters an unrecoverable filter error
- **THEN** system captures error details, notifies the user with diagnostic troubleshooting, and preserves original source files

### Requirement: Resource protection on mobile hosts
The system SHALL enforce hardware-aware execution constraints, preventing Out-Of-Memory crashes and thermal throttling during memory-intensive operations.

#### Scenario: Memory-heavy reverse operation protection
- **WHEN** user requests reversing a video whose duration or resolution exceeds mobile RAM buffer safety thresholds
- **THEN** system warns the user, suggests trimming or downscaling first, and refuses unbounded full-RAM buffering

#### Scenario: Queue serialization on single-worker hosts
- **WHEN** an AI transformation is initiated while another encoding job is currently active
- **THEN** system queues the AI transcode behind the active job and reports queue position to the user

### Requirement: Session termination and cleanup
The system SHALL allow users to exit the AI session at any time and automatically clean up temporary files in the sandbox workspace.

#### Scenario: User exits AI mode
- **WHEN** user clicks "Exit AI Mode" or issues an exit command
- **THEN** system deactivates conversational AI mode for the task and returns to the primary inline menu

#### Scenario: Post-delivery sandbox cleanup
- **WHEN** an AI-generated output is successfully delivered or the session is concluded
- **THEN** system purges temporary intermediate files from `storage/Ai_Workplace/<user_id>_<task_id>/`
