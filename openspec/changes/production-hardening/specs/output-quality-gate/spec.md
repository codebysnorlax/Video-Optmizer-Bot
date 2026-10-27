# Spec Delta

## Purpose

Validates FFmpeg output against the operation plan before delivering to the user, detecting slop output (wrong resolution, truncated duration, inflated file size) and auto-retrying on recoverable failures.

## ADDED Requirements

### Requirement: Resolution validation
The system SHALL verify that the output video resolution matches the target resolution specified in the FFmpeg plan before delivery.

#### Scenario: Output matches requested resolution
- **WHEN** the AI plan specifies downscaling to 1080p and FFmpeg produces a 1920x1080 output
- **THEN** validation passes and the file is delivered to the user

#### Scenario: Output resolution is drastically wrong
- **WHEN** the AI plan specifies 1080p but FFmpeg produces a 360p output (not explicitly requested by the user)
- **THEN** the system discards the output, logs the failure in `debug-ai/`, and retries once with corrected scale parameters

#### Scenario: Retry also fails
- **WHEN** the automatic retry also produces an output that fails validation
- **THEN** the system reports the failure to the user with the specific mismatch details and does NOT deliver the bad file

### Requirement: Duration integrity check
The system SHALL verify that the output duration is within 2% of the input duration for operations that do not intentionally alter duration.

#### Scenario: Normal optimization preserves duration
- **WHEN** a 120-second video is optimized to 1080p
- **THEN** the output duration is between 117.6 and 122.4 seconds

#### Scenario: Truncated output detected
- **WHEN** a 120-second video produces a 5-second output after optimization
- **THEN** the system flags this as a truncated output, discards it, and retries once

#### Scenario: Intentional duration change passes
- **WHEN** the user requests trimming to the first 30 seconds using `-ss` and `-to` flags
- **THEN** duration validation is skipped for that operation

### Requirement: File size sanity check for optimization
The system SHALL warn when an optimization operation produces a file larger than the input, since the user's goal is typically size reduction.

#### Scenario: Optimization increases file size
- **WHEN** the AI performs a "compression" or "optimization" plan and the output is larger than the input
- **THEN** the system delivers the file but includes a warning in the AI response: the output is larger because the source was already highly compressed

#### Scenario: Non-optimization plan skips size check
- **WHEN** the operation is a format conversion, audio replacement, or creative filter (not labeled as optimization)
- **THEN** no file size comparison warning is generated

### Requirement: Empty and corrupt output detection
The system SHALL reject outputs that are zero bytes, cannot be probed by FFprobe, or have no valid streams.

#### Scenario: Zero-byte output
- **WHEN** FFmpeg finishes but produces a 0-byte output file
- **THEN** the system returns an error to the user and does NOT attempt delivery

#### Scenario: Corrupt output with no streams
- **WHEN** FFprobe reports no video and no audio streams in the output
- **THEN** the system returns an error to the user with a clear message
