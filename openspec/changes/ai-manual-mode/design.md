# Design

## Context

The Telegram Video Optimizer Bot runs asynchronously using Telethon MTProto, supporting up to 2 GB video ingestion. It detects host architecture dynamically via `core/hardware.py`, balancing desktop GPU acceleration (Intel VAAPI) against resource-constrained Android Termux environments (Nokia 6.1 Plus Snapdragon 636, 8 cores, ~1.6 GB free RAM). Video operations are executed asynchronously via `core/engine.py` with pipe-based progress parsing, throttled Telegram progress updates (`bot/progress.py`), and concurrency serialization (`bot/task_manager.py`).

See `proposal.md` for motivation and `specs/ai-manual/spec.md` for behavioral requirements.

## Goals / Non-Goals

**Goals:**
- Provide an interactive conversational AI engineer directly inside Telegram for video diagnosis and natural-language transformations.
- Implement a lightweight, non-blocking ReAct agent using `httpx` to interface with OpenRouter free models without heavy third-party framework dependencies.
- Build a modular `skills/` loader that indexes and retrieves domain-specific FFmpeg recipes and safety policies.
- Execute all AI-directed media operations inside an isolated, auto-cleaned workspace (`storage/Ai_Workplace/<user_id>_<task_id>/`).
- Integrate AI FFmpeg executions with existing progress reporters and hardware serialization mechanisms.
- Implement strict RAM safety guards for memory-heavy operations (e.g., `-vf reverse`) on mobile hosts.

**Non-Goals:**
- Allowing arbitrary shell commands or unvetted bash script execution.
- Generative video synthesis or frame diffusion (scope is constrained to media processing and optimization via FFmpeg/ffprobe).
- Heavy multi-agent frameworks requiring compilation of native C/Rust extensions on Android Termux.

## Decisions

### 1. Lightweight Native Async Agent vs. LangChain / LangGraph
- **Decision:** Build a lightweight native async ReAct agent using `httpx` calling OpenRouter's OpenAI-compatible chat completions API with native tool calling (`tools`).
- **Rationale:** LangGraph and LangChain introduce dozens of dependencies (`pydantic-core`, `tiktoken`, `langsmith`), many requiring Rust or C compilers that frequently fail building wheels on Android 10 Termux (aarch64). A native Python agent has a footprint of < 15 MB RAM (vs 300+ MB for LangGraph), starts instantly on the Snapdragon 636, and leaves CPU and memory available for video transcoding.
- **Alternatives Considered:**
  - *LangGraph*: Powerful for state graphs, but too heavy, risky to install in Termux, and introduces high memory pressure.
  - *Standard `openai` SDK*: Viable, but `httpx` is already a universal async HTTP library with minimal surface and direct JSON payload control.

### 2. Sandboxed Safe Tool Dispatcher vs. Direct Shell Access
- **Decision:** Restrict the AI to three vetted tool interfaces:
  1. `diagnose_video(task_id, question)`: Evaluates `MediaMetadata` for bottlenecks and bloat.
  2. `execute_ffmpeg_plan(task_id, recipe_args, output_filename)`: Validates arguments, prepends hardware-specific flags (`-threads 8`, `-preset veryfast`), directs outputs to `storage/Ai_Workplace/`, and hooks into `run_ffmpeg_with_progress`.
  3. `verify_media(file_path)`: Runs `ffprobe` to validate headers and stream decodability.
- **Rationale:** Passing raw shell access to an LLM creates severe security risks (accidental deletions, escaping paths, runaway forks). Constrained tools guarantee output path confinement and preserve the integrity of original uploads.
- **Alternatives Considered:**
  - *Terminal tool with bash execution*: Rejected due to high risk of corrupted commands, rogue background processes, or accidental deletion.

### 3. Dynamic Skills Indexing and Context Loading
- **Decision:** Organize skills under the root `skills/` directory (`skills/ffmpeg/`, `skills/video_diagnosis/`, `skills/creative_fx/`). A `SkillRegistry` parses YAML frontmatter and extracts relevant instruction blocks on-demand.
- **Rationale:** Storing extensive recipes (500+ lines) directly in the LLM system prompt wastes context window tokens and degrades prompt attention. Dynamic retrieval injects only relevant recipe subsets (e.g., aspect ratio filters or reverse rules).
- **Alternatives Considered:**
  - *Hardcoding recipes in Python functions*: Inflexible and difficult to extend without rewriting code.
  - *Static prompt dumping*: Exceeds token budget on free-tier OpenRouter models and increases response latency.

### 4. Memory Buffering Guardrails on Snapdragon 636
- **Decision:** Enforce pre-flight validation on memory-buffering FFmpeg filters. For example, video reversal (`-vf reverse`) buffers all uncompressed frames in RAM; if the video exceeds 15 seconds or 720p on Termux, the agent automatically warns the user, suggests trimming, or applies downscaling before reversal.
- **Rationale:** A 1080p uncompressed frame stream can consume > 1 GB of RAM within seconds, triggering Android's Low Memory Killer (LMK) and terminating the bot process.
- **Alternatives Considered:**
  - *Unconditional execution*: Will crash Termux on longer clips.

### 5. Multi-Model OpenRouter Fallback Cascade
- **Decision:** Default to top-performing free tool-calling models on OpenRouter (`google/gemini-2.0-flash-exp:free`, `meta-llama/llama-3.3-70b-instruct:free`, `qwen/qwen-2.5-coder-32b-instruct:free`) with an automatic fallback mechanism if a free model endpoint experiences temporary rate limits.
- **Rationale:** Free-tier endpoints may occasionally experience high traffic or transient 429/503 responses. Cascading ensures resilient operation.

## Architecture & Data Flow

```
+--------------------------------------------------------------------------+
|                          TELETHON BOT DISPATCHER                         |
|   (bot/callbacks.py, bot/handlers.py, bot/client.py)                     |
+--------------------------------------------------------------------------+
           |                                              |
           | User enters AI Manual                        | User sends prompt
           v                                              v
+-----------------------+                    +-----------------------------+
| TaskContext           |                    | AI Session Manager          |
| - task_id             |                    | - user_id, task_id          |
| - meta (ffprobe info) |                    | - conversation history      |
| - input_path (orig)   |                    | - session state             |
+-----------------------+                    +-----------------------------+
           |                                              |
           +----------------------+-----------------------+
                                  |
                                  v
+--------------------------------------------------------------------------+
|                            CORE AI AGENT ENGINE                          |
|                             (core/ai/agent.py)                           |
|                                                                          |
|  1. SkillRegistry (core/ai/skills.py): Loads relevant recipes from       |
|     skills/ffmpeg/, skills/video_diagnosis/, skills/creative_fx/         |
|  2. OpenRouterClient (core/ai/client.py): Calls free models with tools   |
|  3. Safe Tool Dispatcher (core/ai/tools.py):                             |
|     - tool_diagnose()                                                    |
|     - tool_execute_ffmpeg()                                              |
|     - tool_verify_media()                                                |
+--------------------------------------------------------------------------+
                                  |
                   Executes planned FFmpeg transcode
                                  v
+--------------------------------------------------------------------------+
|                  EXECUTION SANDBOX & HARDWARE QUEUE                      |
|                                                                          |
|  - Concurrency Lock: bot/task_manager.py (Single worker on Termux)       |
|  - Engine: core/engine.py (run_ffmpeg_with_progress)                     |
|  - Isolated Workspace: storage/Ai_Workplace/<user_id>_<task_id>/         |
|  - Live Progress: bot/progress.py (FPS, percentage, ETA)                 |
+--------------------------------------------------------------------------+
                                  |
                 Delivery & Cleanup Confirmation
                                  v
+--------------------------------------------------------------------------+
|  1. Upload output file to Telegram                                       |
|  2. Prompt user for retention: [ Keep Output ] / [ Delete Output ]       |
|  3. Auto-purge temporary artifacts from Ai_Workplace/                    |
+--------------------------------------------------------------------------+
```

## Risks / Trade-offs

- **[Risk] OpenRouter Free API Rate Limits or Latency Spikes**  
  → *Mitigation:* Implement retry with exponential backoff and automatic model fallback cascade (`gemini-2.0-flash-exp:free` -> `llama-3.3-70b-instruct:free`). Clearly notify user in chat if API limits are hit.
- **[Risk] Snapdragon 636 Thermal Throttling or Out-of-Memory (OOM)**  
  → *Mitigation:* Cap FFmpeg encoding to `-threads 8 -preset veryfast` on Termux. Enforce memory safety limits on buffering filters (`reverse`, `framerate` interpolation). Serialize execution through the existing `TaskManager` semaphore.
- **[Risk] Accumulation of Temporary Files in `storage/Ai_Workplace/`**  
  → *Mitigation:* Implement automated cleanup of intermediate files upon session completion or task reset, integrated into disk space management.
- **[Risk] Missing `OPENROUTER_API_KEY`**  
  → *Mitigation:* Gracefully disable the "AI Manual" button with an informative alert when the key is not configured, guiding the admin to add it to `.env`.
