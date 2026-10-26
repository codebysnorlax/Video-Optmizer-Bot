# Design

## Context

Current implementation in `core/ai/tools.py` and `bot/ai_handler.py` assumes a 1-to-1 mapping between a single incoming video file and a single encoded output. The host environment is an Android 10 Termux smartphone (Snapdragon 636, ~1.7 GB free RAM) or standard Linux server running FFmpeg with thread and memory constraints. All operations must strictly run inside `storage/Ai_Workplace/<user_id>_<task_id>/`.

## Goals / Non-Goals

**Goals:**
- Session-level multi-asset awareness: Users can supply multiple assets (video, audio files) in an ongoing session.
- Flexible FFmpeg operation schema: Support stream mapping (`-map`), track splitting (`-vn`, `-an`), audio replacement, clipping, speed adjustments, and format conversion.
- Compound multi-output execution: Execute multi-step tasks producing multiple output deliverables (e.g. video without audio + extracted MP3) in one interaction turn.
- Multi-file delivery: Upload each generated deliverable with live progress bars and before/after metadata.
- Fix identified runtime bugs: `record_task_complete()` argument mismatch and CallbackQuery `event.message` attribute errors.

**Non-Goals:**
- Arbitrary terminal shell execution (all operations are strictly constrained to sanitized FFmpeg arguments).
- Real-time video preview streaming in chat (files are probed and uploaded upon completion).

## Decisions

### 1. Multi-Asset Representation in TaskContext
- **Decision**: Extend `TaskContext` to maintain an asset directory `assets: Dict[str, SessionAsset]` where keys are friendly identifiers (`v1`, `a1`, etc.) containing the file path, media type, and probed metadata.
- **Rationale**: The LLM needs clear, stable tokens to reference inputs when constructing multi-input FFmpeg operations (e.g. `-i v1 -i a1 -map 0:v -map 1:a`).
- **Alternative Considered**: Relying on arbitrary raw file paths. Rejected because raw paths risk directory traversal and are error-prone for LLM generation.

### 2. Multi-Output FFmpeg Tool Schema
- **Decision**: Update `execute_ffmpeg_plan` in `core/ai/tools.py` to accept an `outputs` array of deliverables:
  ```json
  {
    "plan_title": "Split Video & Audio",
    "operations": [
      {
        "inputs": ["v1"],
        "output_filename": "video_mute.mp4",
        "video_codec": "copy",
        "audio_codec": "none",
        "extra_args": ["-map", "0:v:0"]
      },
      {
        "inputs": ["v1"],
        "output_filename": "audio_track.mp3",
        "video_codec": "none",
        "audio_codec": "mp3",
        "extra_args": ["-map", "0:a:0"]
      }
    ]
  }
  ```
- **Rationale**: Allows the agent to either generate single files or multiple distinct deliverables atomically, validating each step inside the sandbox.

### 3. Sequential Multi-File Delivery Pipeline
- **Decision**: Replace `deliver_ai_output` with `deliver_ai_results`, iterating over all generated deliverables, uploading them sequentially via MTProto, and providing individual progress status.
- **Rationale**: Uploading sequentially prevents overwhelming the host's memory and bandwidth while giving the user clear, discrete download messages for each requested file.

### 4. Open Conversational UX
- **Decision**: Update the AI greeting card and keyboards to deemphasize preset buttons in favor of natural language prompting. Keep quick prompts as optional examples rather than hard-coded limits.

## Risks / Trade-offs

- **[Risk: RAM exhaustion on mobile Termux host during simultaneous transcode]** → Mitigated by running compound steps sequentially, using stream copying (`-c:v copy`) whenever re-encoding is unnecessary, and capping threads at 4.
- **[Risk: Telegram 64-byte callback limit on interactive buttons]** → Maintained index-based callbacks for any pagination or file selection.
- **[Risk: LLM hallucinating invalid FFmpeg arguments]** → Tool executor validates arguments against an allowlist and sanitizes output file paths strictly inside the user's isolated `Ai_Workplace` folder.
