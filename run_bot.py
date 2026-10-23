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
from database.db import init_db
from bot.client import create_bot_client

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S"
)
logger = logging.getLogger("OptimizerBot")


def print_startup_banner() -> None:
    """Prints a clear system status diagnostic banner."""
    disk = settings.get_disk_usage()
    banner = f"""
======================================================================
🚀 TELEGRAM VIDEO OPTIMIZER & CONVERTER BOT
======================================================================
🖥  OS Platform       : {current_hardware.os_name} ({current_hardware.arch})
📱 Mobile Termux     : {"YES (Android 10 SDM636)" if current_hardware.is_termux else "NO (Standard Linux)"}
🧠 CPU Specification : {current_hardware.cpu_model} ({current_hardware.cpu_cores} threads)
📊 Memory Status     : {current_hardware.available_ram_mb} MB available / {current_hardware.total_ram_mb} MB total
⚙️ Hardware Accel    : {current_hardware.description}
🎞 Primary Encoder   : {current_hardware.video_encoder}
💾 Host Disk Space   : {disk['free_gb']} GB free / {disk['total_gb']} GB total ({disk['percent_used']}% used)
📁 Storage Temp Dirs : {settings.DOWNLOAD_DIR}
🗄️ Database Location : {settings.DB_PATH}
======================================================================
"""
    print(banner, flush=True)


async def main() -> None:
    # 1. Print system diagnostic banner
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
    print(f"\n>>> Bot is online and listening for videos as @{me.username} <<<\n", flush=True)

    # 5. Run bot loop until disconnected
    await client.run_until_disconnected()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Bot stopped gracefully.")
