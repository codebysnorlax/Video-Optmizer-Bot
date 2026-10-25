# Proposal

## Why

Users often encounter complex video playback issues (e.g., thermal stuttering or lag on mobile hardware, bloated camera bitrates, wrong aspect ratios) but lack the technical expertise to select the exact FFmpeg parameters, CRF values, scaling algorithms, or codecs. The existing menu options provide predefined presets (720p, 1080p, Auto), but cannot diagnose why a specific video is lagging or execute arbitrary custom transformations (e.g., reverse video, change aspect ratio to 9:16, convert 4K to 1080p with perceptual lossless quality) via conversational requests.

Adding an intelligent "AI Manual" mode empowers users to ask questions and issue natural-language editing instructions. By combining deep video probing, a modular skills cookbook, OpenRouter free models, and a resource-conscious sandbox running smoothly on mobile hardware (Snapdragon 636 / Termux Android 10), the bot delivers autonomous diagnosis and smart execution without draining system RAM.

## What Changes

- **AI Manual Activation & Chat Session**: Tapping "3. AI Manual" transitions the user into an active conversational AI session scoped to the selected video.
- **Diagnostic Intelligence**: The AI analyzes detailed `ffprobe` metadata and explains in plain language why a video lags, has excessive file size, or fails to play on specific devices.
- **Natural Language FFmpeg Execution**: The AI translates user requests (e.g., downscale, reverse, change aspect ratio, extract or fix streams) into safe, optimized FFmpeg plans.
- **Isolated Sandbox Workspace (`storage/Ai_Workplace/`)**: All AI-driven temporary steps, filter chains, and intermediate files are processed in an isolated per-task directory, keeping pristine source files safe.
- **Dynamic Skills Architecture (`skills/`)**: Modular markdown-based skills (`skills/ffmpeg/`, `skills/video_diagnosis/`, `skills/creative_fx/`) are indexed and dynamically provided to the model to eliminate hallucinations and preserve token efficiency.
- **Termux & Snapdragon 636 Resource Guardrails**: Lightweight async execution (using `httpx` and OpenRouter API) prevents RAM bloat, while strict execution rules safeguard low-memory devices against OOM crashes during heavy filters (like `reverse`).
- **Interactive Execution Confirmation & Progress**: Transcoding plans display estimated parameters and run through the existing asynchronous progress pipeline with live Telegram status updates.

## Capabilities

### New Capabilities

- `ai-manual`: Natural language video diagnosis, conversational assistance, dynamic skill-based recipe selection, and sandboxed FFmpeg execution for user video tasks.

### Modified Capabilities

None. (There are no pre-existing spec-level requirements in the repository).

## Impact

- **Bot UI & Handlers**: Updates `bot/keyboards.py` to activate `ai_manual`, adds message listeners in `bot/client.py` and state handling in `bot/callbacks.py`.
- **Core Engine & Architecture**: Introduces `core/ai/` module containing the OpenRouter client, dynamic skill loader, ReAct agent loop, and safe tool dispatcher.
- **Storage**: Adds `storage/Ai_Workplace/` with automated session cleanup.
- **Dependencies**: Adds `httpx` to `requirements.txt` for lightweight, non-blocking HTTP requests to OpenRouter. No heavy C/Rust dependencies (like LangChain/LangGraph) required for Termux compatibility.
- **Environment**: Adds `OPENROUTER_API_KEY` and optional `OPENROUTER_MODEL` configuration in `config/settings.py` and `.env.example`.
