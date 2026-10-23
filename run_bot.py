#!/usr/bin/env python3
"""
Main Application Entry Point: Video Optimizer & Converter Telegram Bot
Dual Support: Ubuntu LTS (Intel VAAPI GPU) & Nokia 6.1 Plus Snapdragon 636 (Termux Android 10)
"""

import sys
import logging
import asyncio
from config.settings import settings
from core.hardware import current_hardware
from core.terminal import ColoredLogFormatter, print_banner_box, BOLD_GREEN, RESET
from database.db import init_db
from bot.client import create_bot_client

# Configure colorful structured logging
handler = logging.StreamHandler(sys.stdout)
handler.setFormatter(ColoredLogFormatter())
root_logger = logging.getLogger()
root_logger.setLevel(logging.INFO)
root_logger.handlers = [handler]

logger = logging.getLogger("OptimizerBot")


def print_startup_banner() -> None:
    """Prints a clear, colorful system diagnostic box."""
    disk = settings.get_disk_usage()
    items = [
        ("OS Platform", f"{current_hardware.os_name} ({current_hardware.arch})"),
        ("Mobile Termux", "YES (Android 10 SDM636)" if current_hardware.is_termux else "NO (Standard Linux)"),
        ("CPU Model", f"{current_hardware.cpu_model} ({current_hardware.cpu_cores} threads)"),
        ("Memory Status", f"{current_hardware.available_ram_mb} MB free / {current_hardware.total_ram_mb} MB total"),
        ("Hardware Accel", current_hardware.description),
        ("Primary Encoder", current_hardware.video_encoder),
        ("Host Disk Space", f"{disk['free_gb']} GB free / {disk['total_gb']} GB total"),
        ("Storage Temp Dir", str(settings.DOWNLOAD_DIR)),
        ("Database File", str(settings.DB_PATH))
    ]
    print_banner_box("TELEGRAM VIDEO OPTIMIZER & CONVERTER BOT", items)


async def main() -> None:
    # 1. Print colorful system diagnostic banner
    print_startup_banner()

    # 2. Validate configuration
    if not settings.BOT_TOKEN or not settings.TELEGRAM_API_ID or not settings.TELEGRAM_API_HASH:
        logger.error("Missing credentials in .env file! Ensure BOT_TOKEN, TELEGRAM_API_ID, and TELEGRAM_API_HASH are set.")
        sys.exit(1)

    # 3. Ensure directories & initialize SQLite database
    settings.ensure_directories()
    logger.info("Initializing SQLite database...")
    await init_db()

    # 4. Instantiate Telethon Bot Client
    logger.info("Connecting to Telegram MTProto network...")
    client = create_bot_client()

    await client.start(bot_token=settings.BOT_TOKEN)
    me = await client.get_me()
    logger.info(f"Bot successfully authenticated as @{me.username} (ID: {me.id})")
    print(f"\n{BOLD_GREEN}>>> Bot is online and listening for videos as @{me.username} <<<{RESET}\n", flush=True)

    # 5. Run bot loop until disconnected
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped gracefully.")
