# Telegram Video Optimizer & Converter Bot

A high-performance, asynchronous Telegram bot designed for video ingestion, intelligent bitrate debloating, cross-format conversion, and audio extraction. Engineered specifically for **dual-environment operation**:
- **Ubuntu 24/26 LTS**: Full hardware GPU acceleration via Intel QuickSync / VAAPI (`/dev/dri/renderD128`).
- **Nokia 6.1 Plus (Termux Android 10)**: Qualcomm Snapdragon 636 8-core NEON CPU multi-threading (`aarch64`) with thermal-safe presets and single-worker queue stability.

---

## Key Features

1. **Large File Support (Up to 2,000 MB / 2 GB)**:
   - Standard Telegram Bot API fails with `File is too big` on any video > 20MB.
   - Built on **Telethon MTProto**, enabling chunked, high-speed streaming directly from Telegram datacenters for videos up to **2 GB**.
2. **Instant Video Probing (<200ms)**:
   - Uses `ffprobe` to extract resolution, aspect ratio, frame rate, duration, container, and stream bitrates.
   - Automatically detects severe bitrate bloat (e.g. 40s raw camera video taking 500MB).
3. **Interactive Inline Menu**:
   - **`1. Optimize`**:
     - `1. Low Device (Max Compatibility)`: H.264 Baseline/Main profile, YUV420p, resolution capped to 720p, bitrate constrained for smooth playback on budget phones and older TVs.
     - `2. Moderate (Mid-Range Device)`: Balanced high-profile encoding with crisp clarity and mid-range bitrate.
     - `3. Auto Recommended (Smart Fix)`: Smart analyzer that calculates perceptual CRF + VBV buffer caps, normalizes audio, and enables `-movflags +faststart` for instant Telegram streaming. Reduces 500MB raw videos down to ~15-20MB with **zero perceptible visual loss**.
   - **`2. Convert`**:
     - Video containers: `MP4 (H.264)`, `MKV`, `WEBM (VP9)`, `AVI`, `MOV`, `MPG`.
     - Audio extraction: `MP3`, `AAC (M4A)`, `OGG (Opus)`, `WAV`.
   - **`3. AI Manual`**:
     - Placeholder for upcoming AI video features.
4. **Live Real-Time Progress Reporting**:
   - Throttled real-time progress bars for **Download**, **FFmpeg Transcoding** (FPS, speed multiplier, ETA), and **Upload**.
5. **Post-Delivery Disk Space Prompt**:
   - After delivering the optimized video, the bot displays remaining server disk space and prompts:
     `[ Delete from Server ]` or `[ Keep in Server Cache ]`.
6. **SQLite History & Metrics**:
   - Tracks processed tasks, saved bandwidth, and user history in `database/optimizer.db`.
   - Commands: `/stats`, `/history`, `/hardware`, `/help`.

---

## Architecture

```
optmizer/
├── config/
│   ├── __init__.py
│   └── settings.py          # Environment, paths, and disk space monitor
├── core/
│   ├── __init__.py
│   ├── hardware.py          # Auto-detects Intel VAAPI GPU vs Termux Snapdragon 636 CPU
│   ├── probe.py             # FFprobe metadata parser & markdown analysis card
│   ├── engine.py            # Async FFmpeg subprocess with live progress parsing
│   ├── optimizer.py         # Low Device, Moderate, and Auto Recommended algorithms
│   └── converter.py         # Multi-format video and audio converters
├── database/
│   ├── __init__.py
│   ├── db.py                # Async SQLite database initializer
│   └── models.py            # User and Task history repositories
├── bot/
│   ├── __init__.py
│   ├── client.py            # Telethon MTProto client setup & event routing
│   ├── handlers.py          # Slash commands (/start, /help, /hardware, /stats, /history)
│   ├── media_handler.py     # Streaming download worker & video prober
│   ├── keyboards.py         # Dynamic inline keyboards
│   ├── callbacks.py         # Button click dispatcher & transcode execution
│   ├── progress.py          # Throttled live Telegram progress updater
│   └── task_manager.py      # Concurrency semaphore & in-flight state tracking
├── storage/
│   ├── downloads/           # Temporary download directory
│   └── outputs/             # Transcoded output directory
├── tests/                   # Pytest test suite (100% passing)
├── run_bot.py               # Main application entry point
├── requirements.txt         # Production dependencies
└── .env                     # Credentials & configuration
```

---

## Setup & Execution Guide

### Option A: Running on Ubuntu 24/26 LTS (with GPU)

1. **System Prerequisites**:
   ```bash
   sudo apt update
   sudo apt install -y python3 python3-venv python3-pip ffmpeg
   ```
2. **Verify GPU / VAAPI**:
   ```bash
   ls -la /dev/dri
   # If /dev/dri/renderD128 is present, the bot will automatically use Intel VAAPI GPU!
   ```
3. **Set Up Python Virtual Environment**:
   ```bash
   cd /home/snorlax/Desktop/POC/optmizer
   python3 -m venv .venv
   .venv/bin/pip install -r requirements.txt
   ```
4. **Configure Environment (`.env`)**:
   Verify your `.env` contains:
   ```env
   BOT_TOKEN=your_bot_token_here
   TELEGRAM_API_ID=your_api_id_here
   TELEGRAM_API_HASH=your_api_hash_here
   ```
5. **Run the Bot**:
   ```bash
   .venv/bin/python run_bot.py
   ```

---

### Option B: Running on Nokia 6.1 Plus (Termux Android 10)

1. **Keep Termux Awake in Background**:
   Android 10 puts the CPU to sleep when the screen locks. Run this once:
   ```bash
   termux-wake-lock
   ```
   *(Also disable battery optimization for the Termux app in Android Settings -> Apps -> Termux -> Battery -> Unrestricted).*

2. **Install Required Packages**:
   In your Termux shell (zsh or bash):
   ```bash
   pkg update -y && pkg upgrade -y
   pkg install -y python ffmpeg git clang libjpeg-turbo tmux
   ```

3. **Transfer or Clone Project**:
   - **Method 1: Transfer from PC via SSH/SCP**:
     In Termux, start SSH:
     ```bash
     pkg install openssh
     sshd
     whoami    # shows your termux user (e.g. u0_a123)
     passwd    # set a password
     ifconfig  # find phone IP (e.g. 192.168.1.50)
     ```
     From Ubuntu PC:
     ```bash
     scp -P 8022 -r /home/snorlax/Desktop/POC/optmizer <termux_user>@<phone_ip>:~/
     ```
   - **Method 2: Clone from Git**:
     ```bash
     cd ~
     git clone <your-repo-url> optmizer
     ```

4. **Initialize Virtual Environment**:
   ```bash
   cd ~/optmizer
   python -m venv .venv
   .venv/bin/pip install --upgrade pip
   .venv/bin/pip install -r requirements.txt
   ```

5. **Ensure `.env` Credentials are Set**:
   ```bash
   cat << 'EOF' > .env
   BOT_TOKEN=your_bot_token_here
   TELEGRAM_API_ID=your_api_id_here
   TELEGRAM_API_HASH=your_api_hash_here
   DOWNLOAD_DIR=storage/downloads
   OUTPUT_DIR=storage/outputs
   DB_PATH=database/optimizer.db
   MAX_CONCURRENT_ENCODING=1
   PROGRESS_UPDATE_INTERVAL=2.5
   EOF
   ```

6. **Run 24/7 in Background using `tmux`**:
   ```bash
   tmux new -s bot
   cd ~/optmizer
   .venv/bin/python run_bot.py
   ```
   - **Detach from session**: Press `Ctrl + b`, then release and press `d`. The bot keeps running in the background even if you close the Termux app!
   - **Re-attach to check logs**: `tmux attach -t bot`
   - **Stop the bot**: `tmux kill-session -t bot`

*Note: The bot auto-detects Termux (`aarch64`), engages all 8 Kryo 260 CPU cores (`-threads 8`), uses thermal-efficient `veryfast` preset, and enforces single-worker serialization to keep your Nokia 6.1 Plus cool and stable.*

---

## Running Automated Tests

Run the test suite anytime:
```bash
.venv/bin/python -m pytest tests/ -v
```

---

## Telegram Bot Commands

| Command | Description |
| :--- | :--- |
| `/start` | Welcome greeting and service overview |
| `/help` | Detailed guide on optimization modes and format conversion |
| `/hardware` | Live host diagnostics: CPU cores, RAM, GPU/VAAPI status, and disk space |
| `/stats` | User metrics: total processed videos, total bandwidth saved, and host storage |
| `/history` | Recent 5 tasks with compression ratios |
