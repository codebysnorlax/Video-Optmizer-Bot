# Spec Delta

## Purpose

Performs pre-execution checks for available disk space and system RAM before launching FFmpeg operations, failing early with a user-friendly message instead of allowing cryptic FFmpeg errors from exhausted resources.

## ADDED Requirements

### Requirement: Disk space pre-check
The system SHALL verify that the available disk space is at least 2x the estimated output size before starting an FFmpeg operation.

#### Scenario: Sufficient disk space
- **WHEN** the estimated output size is 500 MB and available disk space is 3 GB
- **THEN** the operation proceeds normally

#### Scenario: Insufficient disk space
- **WHEN** the estimated output size is 1 GB and available disk space is 800 MB
- **THEN** the system refuses to start the operation and informs the user: "Not enough disk space. X MB free, need approximately Y MB. Please delete unused files via /files."

#### Scenario: Estimation uses input size as baseline
- **WHEN** no explicit output size can be estimated from the plan parameters
- **THEN** the system uses `input_file_size * 1.5` as a conservative estimate for video re-encoding, or `input_file_size * 0.1` for audio extraction

### Requirement: RAM availability check for memory-intensive operations
The system SHALL check current available RAM before starting operations known to buffer frames into memory (reverse, complex filter graphs with multiple inputs).

#### Scenario: Reverse operation on constrained device
- **WHEN** the user requests a video reverse and available RAM is below 300 MB
- **THEN** the system warns the user that the operation may fail due to low memory and suggests closing other apps

#### Scenario: Normal optimization skips RAM check
- **WHEN** the operation is a standard CRF encode without frame buffering
- **THEN** no RAM check is performed because FFmpeg streams frames without buffering

### Requirement: Dynamic RAM reading
The system SHALL read current available RAM at operation time, not rely on the startup-time snapshot.

#### Scenario: RAM read at operation start
- **WHEN** a memory-intensive operation is about to start
- **THEN** the system reads `/proc/meminfo` (Linux) or equivalent to get current available RAM, not the value cached at process startup
