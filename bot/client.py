"""
Telethon MTProto Client Initialization & Event Dispatcher
"""

import logging
from telethon import TelegramClient, events, functions, types
from config.settings import settings
from bot.handlers import (
    handle_start,
    handle_help,
    handle_hardware,
    handle_stats,
    handle_history,
    handle_storage,
    handle_files,
    handle_ai_models
)
from bot.media_handler import handle_incoming_media, is_video_message
from bot.callbacks import handle_callback_query

logger = logging.getLogger(__name__)


async def register_bot_menu_commands(client: TelegramClient) -> None:
    """Registers official Telegram Bot menu commands for auto-complete in chat input."""
    commands = [
        types.BotCommand(command="start", description="Start the bot and view instructions"),
        types.BotCommand(command="help", description="How to optimize and convert videos"),
        types.BotCommand(command="ai_setting", description="Configure AI free model settings"),
        types.BotCommand(command="files", description="View, recall, or delete server cached files"),
        types.BotCommand(command="hardware", description="View CPU/GPU hardware acceleration info"),
        types.BotCommand(command="stats", description="View total bandwidth saved & task count"),
        types.BotCommand(command="history", description="View your recent processing tasks"),
        types.BotCommand(command="storage", description="Check host disk space & active cache")
    ]
    try:
        await client(functions.bots.SetBotCommandsRequest(
            scope=types.BotCommandScopeDefault(),
            lang_code="",
            commands=commands
        ))
        logger.info("Telegram input box command menu registered successfully.")
    except Exception as e:
        logger.warning(f"Could not register Telegram bot menu commands: {e}")


def create_bot_client() -> TelegramClient:
    """Creates and configures the Telethon client in Bot mode."""
    from core.crypto_patch import apply_crypto_patch
    apply_crypto_patch()

    session_name = "optimizer_bot_session"

    client = TelegramClient(
        session_name,
        api_id=settings.TELEGRAM_API_ID,
        api_hash=settings.TELEGRAM_API_HASH
    )

    # Register Slash Commands
    @client.on(events.NewMessage(pattern=r"^/start$"))
    async def _start(event):
        await handle_start(event)

    @client.on(events.NewMessage(pattern=r"^/help$"))
    async def _help(event):
        await handle_help(event)

    @client.on(events.NewMessage(pattern=r"^/hardware$"))
    async def _hardware(event):
        await handle_hardware(event)

    @client.on(events.NewMessage(pattern=r"^/stats$"))
    async def _stats(event):
        await handle_stats(event)

    @client.on(events.NewMessage(pattern=r"^/history$"))
    async def _history(event):
        await handle_history(event)

    @client.on(events.NewMessage(pattern=r"^/storage$"))
    async def _storage(event):
        await handle_storage(event)

    @client.on(events.NewMessage(pattern=r"^/files$"))
    async def _files(event):
        await handle_files(event)

    @client.on(events.NewMessage(pattern=r"^/(ai[-_]settings?|model)$"))
    async def _model(event):
        await handle_ai_models(event)

    # Register Incoming Media & Text Listener
    @client.on(events.NewMessage())
    async def _media_or_text(event):
        # Ignore commands
        if event.raw_text and event.raw_text.startswith("/"):
            return
        if is_video_message(event.message):
            await handle_incoming_media(event, client)
        elif event.raw_text:
            # Check if user has an active AI Manual session
            from bot.task_manager import task_manager
            active_task = task_manager.get_active_ai_task_for_user(event.sender_id)
            if active_task:
                from bot.ai_handler import handle_ai_user_text
                await handle_ai_user_text(event, active_task, client)

    # Register Callback Query Listener
    @client.on(events.CallbackQuery())
    async def _callback(event):
        try:
            await handle_callback_query(event, client)
        except Exception as e:
            from telethon.errors import MessageNotModifiedError
            if isinstance(e, MessageNotModifiedError):
                return
            logger.error(f"Unhandled callback error: {e}", exc_info=True)

    return client

