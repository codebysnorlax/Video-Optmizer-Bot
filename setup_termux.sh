#!/bin/bash
# ==============================================================================
# Automated Setup & Verification Script for Android Termux
# Telegram Video Optimizer & Converter Bot
# ==============================================================================

set -e

echo "--------------------------------------------------------"
echo "  Setting up Telegram Video Optimizer for Termux        "
echo "--------------------------------------------------------"

# 1. Acquire Android wake lock so Termux does not sleep when screen locks
if command -v termux-wake-lock &> /dev/null; then
    echo "[1/5] Enabling Termux wake lock..."
    termux-wake-lock
else
    echo "[1/5] Notice: termux-wake-lock not found (skip if running in Linux chroot/proot)."
fi

# 2. Update Termux packages and install system requirements
echo "[2/5] Updating Termux packages and installing dependencies..."
pkg update -y
pkg install -y python ffmpeg tmux clang openssl libjpeg-turbo

# 3. Create Python virtual environment
echo "[3/5] Setting up Python virtual environment (.venv)..."
if [ ! -d ".venv" ]; then
    python -m venv .venv
fi

# 4. Install Python dependencies (including high-speed C MTProto crypto)
echo "[4/5] Installing Python packages from requirements.txt..."
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt

# 5. Run pytest test suite to verify all components
echo "[5/5] Running verification tests..."
.venv/bin/pytest tests/ -v

echo ""
echo "========================================================"
echo "  SETUP & VERIFICATION COMPLETE!                        "
echo "  Accelerated MTProto Crypto: Active (cryptg / OpenSSL) "
echo "  Multi-Connection Transfers: Active (4-6 workers)      "
echo "========================================================"
echo "To run the bot directly:"
echo "  .venv/bin/python run_bot.py"
echo ""
echo "To run 24/7 in background using tmux (recommended):"
echo "  tmux new -s bot"
echo "  .venv/bin/python run_bot.py"
echo "  (Press Ctrl+b, then d to detach. Bot runs in background)"
echo "========================================================"
