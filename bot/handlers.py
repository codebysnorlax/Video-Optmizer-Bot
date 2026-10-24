"""
Command Handlers (/start, /help, /hardware, /stats, /history)
"""

from telethon import events, Button
from config.settings import settings
from core.hardware import current_hardware
from core.probe import format_bytes
from database.models import (
    upsert_user,
    get_user_stats,
    get_user_recent_tasks,
    get_user_cached_files
)
from bot.keyboards import build_files_list_menu


async def build_start_dashboard(user_id: int, user_name: str) -> tuple[str, list]:
    """Generates the text and interactive keyboard for the /start dashboard."""
    stats = await get_user_stats(user_id)
    disk = settings.get_disk_usage()

    total_tasks = stats.get("total_tasks") or 0
    successful = stats.get("successful_tasks") or 0
    in_bytes = stats.get("total_input_bytes") or 0
    out_bytes = stats.get("total_output_bytes") or 0
    saved_bytes = max(0, in_bytes - out_bytes)
    ratio = (saved_bytes / in_bytes * 100) if in_bytes > 0 else 0

    user_label = user_name if user_name else "User"

    text = (
        "**Video Optimizer & Converter Dashboard**\n\n"
        f"Welcome, **{user_label}**.\n\n"
        "**System Status & Environment:**\n"
        f"• **Engine:** `{current_hardware.description}`\n"
        f"• **Primary Encoder:** `{current_hardware.video_encoder}`\n"
        f"• **Server Storage:** `{disk['free_gb']} GB free / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n"
        f"• **Your Usage:** `{successful}` tasks completed | `{format_bytes(saved_bytes)}` bandwidth saved ({ratio:.1f}%)\n\n"
        "**Available Processing Features:**\n\n"
        "**1. Video Optimization:**\n"
        " • `1. Convert to 720p`: Caps >720p at 720p HD (never upscales <=720p), caps >60fps at 60fps. Includes Keep Same Bitrate & Lesser Bitrate choices.\n"
        " • `2. Convert to 1080p`: Caps >1080p at 1080p FHD (never upscales <=1080p), caps >60fps at 60fps. Includes Keep Same Bitrate & Lesser Bitrate choices.\n"
        " • `3. Auto Recommended`: Smart automatic analyzer. Downscales heavy 4K/2K to 1080p FHD to eliminate mobile decoding lag, optimizes for smooth HD playback on all smartphones without size limits.\n\n"
        "**2. Format Conversion & Audio Fix:**\n"
        " • `Smartphone Audio Fix (AAC)`: Untouched video copy + universal stereo AAC fix in ~1s.\n"
        " • `Transcode`: MP4, MKV, WEBM, AVI, MOV, MPG\n"
        " • `Audio Extraction`: MP3, AAC\n\n"
        "**3. Server File Management:**\n"
        " • Automatic file deduplication (existing server videos are recognized and reused)\n"
        " • Interactive 2-step cleanup after every delivery\n"
        " • Re-optimize from pristine original source with one tap\n"
        " • Manage and recall files anytime via `/files`\n\n"
        "**Send or forward any video here to get started!**"
    )

    buttons = [
        [
            Button.inline("Server Files", data=b"flist:0"),
            Button.inline("My Statistics", data=b"dash_stats")
        ],
        [
            Button.inline("System Diagnostics", data=b"dash_hw"),
            Button.inline("Help & Guide", data=b"dash_help")
        ]
    ]
    return text, buttons


async def _safe_delete_incoming_command(event: events.NewMessage.Event) -> None:
    """Removes the user's incoming '/' command message for a cleaner chat interface."""
    try:
        await event.delete()
    except Exception:
        pass


async def handle_start(event: events.NewMessage.Event) -> None:
    """Handles /start command."""
    user = await event.get_sender()
    first_name = getattr(user, "first_name", "") or ""
    username = getattr(user, "username", "") or ""
    await upsert_user(event.sender_id, username, first_name)

    text, buttons = await build_start_dashboard(event.sender_id, first_name)
    await event.respond(text, buttons=buttons)
    await _safe_delete_incoming_command(event)


async def handle_help(event: events.NewMessage.Event) -> None:
    """Handles /help command."""
    text = (
        "**How to Use Video Optimizer Bot:**\n\n"
        "1. **Send Any Video:** Send or forward any video file or clip (up to 2,000 MB).\n"
        "2. **Instant Media Analysis:** The bot immediately probes resolution, duration, codecs, framerate, and bitrate.\n"
        "3. **Choose Your Action:**\n"
        "   • **1. Convert to 720p:** Caps >720p at 720p HD (never upscales), caps >60fps at 60fps for budget & older phones.\n"
        "   • **2. Convert to 1080p:** Caps >1080p at 1080p FHD (never upscales), caps >60fps at 60fps for mid-range phones.\n"
        "   • **3. Auto Recommended (Smart Fix):** Universal mobile compatibility, downscales 4K/2K to 1080p FHD to eliminate mobile lag and thermal stutter, outputs buttery smooth HD video.\n"
        "   • **Smartphone Audio Fix (AAC):** Fast untouched video copy + universal AAC stereo audio in ~1s.\n"
        "   • **Format Conversion:** Transcode to MP4, MKV, WEBM, AVI, MOV, MPG or extract MP3 / AAC audio.\n"
        "4. **Live Progress:** Watch download, transcoding, and upload progress in real-time.\n"
        "5. **Interactive Cleanup & Re-Optimization:** Delete files from server or try another preset directly from the pristine original source.\n\n"
        "**Available Commands:**\n"
        "• `/start` - Open the main dashboard\n"
        "• `/files` - View, recall, or delete server cached files\n"
        "• `/stats` - View your total saved bandwidth and usage statistics\n"
        "• `/hardware` - View host hardware acceleration & system diagnostics\n"
        "• `/history` - View your recent tasks\n"
        "• `/storage` - Check host disk space & active temporary cache"
    )
    await event.respond(text)
    await _safe_delete_incoming_command(event)


async def handle_hardware(event: events.NewMessage.Event) -> None:
    """Handles /hardware diagnostics command."""
    disk = settings.get_disk_usage()
    text = (
        "**Host Hardware & Platform Diagnostics**\n\n"
        f"• **OS:** `{current_hardware.os_name}`\n"
        f"• **Architecture:** `{current_hardware.arch}`\n"
        f"• **Termux Mobile Server:** `{'Yes' if current_hardware.is_termux else 'No'}`\n"
        f"• **CPU Model:** `{current_hardware.cpu_model}`\n"
        f"• **CPU Cores:** `{current_hardware.cpu_cores} active threads`\n"
        f"• **RAM Usage:** `{current_hardware.available_ram_mb} MB free / {current_hardware.total_ram_mb} MB total`\n"
        f"• **Hardware Acceleration:** `{current_hardware.description}`\n"
        f"• **Primary Codec:** `{current_hardware.video_encoder}`\n\n"
        f"• **Server Storage:** `{disk['free_gb']} GB free / {disk['total_gb']} GB total` ({disk['percent_used']}% used)"
    )
    await event.respond(text)
    await _safe_delete_incoming_command(event)


async def handle_stats(event: events.NewMessage.Event) -> None:
    """Handles /stats command."""
    user_id = event.sender_id
    stats = await get_user_stats(user_id)
    disk = settings.get_disk_usage()

    total_tasks = stats.get("total_tasks") or 0
    successful = stats.get("successful_tasks") or 0
    in_bytes = stats.get("total_input_bytes") or 0
    out_bytes = stats.get("total_output_bytes") or 0
    avg_time = stats.get("avg_time") or 0.0

    saved_bytes = max(0, in_bytes - out_bytes)
    ratio = (saved_bytes / in_bytes * 100) if in_bytes > 0 else 0

    text = (
        "**Your Video Optimization Statistics**\n\n"
        f"• **Total Tasks:** `{total_tasks}` (`{successful}` completed)\n"
        f"• **Total Input Data:** `{format_bytes(in_bytes)}`\n"
        f"• **Total Output Data:** `{format_bytes(out_bytes)}`\n"
        f"• **Total Bandwidth Saved:** `{format_bytes(saved_bytes)}` ({ratio:.1f}%)\n"
        f"• **Average Processing Time:** `{avg_time:.1f}s`\n\n"
        f"• **Server Disk Status:** `{disk['free_gb']} GB free / {disk['total_gb']} GB total`"
    )
    await event.respond(text)
    await _safe_delete_incoming_command(event)


async def handle_history(event: events.NewMessage.Event) -> None:
    """Handles /history command."""
    user_id = event.sender_id
    tasks = await get_user_recent_tasks(user_id, limit=5)
    
    if not tasks:
        await event.respond("You have no processing history yet. Send a video to get started.")
        await _safe_delete_incoming_command(event)
        return

    lines = ["**Your Recent Processing History:**\n"]
    for t in tasks:
        status_tag = "[OK]" if t['status'] == "completed" else "[FAILED]"
        lines.append(
            f"{status_tag} **{t['file_name']}**\n"
            f"   • **Action:** `{t['action']}` | **Date:** `{t['created_at']}`\n"
            f"   • **Size:** `{format_bytes(t['input_size'])}` -> `{format_bytes(t['output_size'])}`"
        )
    await event.respond("\n".join(lines))
    await _safe_delete_incoming_command(event)


async def handle_storage(event: events.NewMessage.Event) -> None:
    """Handles /storage command."""
    disk = settings.get_disk_usage()
    dl_size = sum(f.stat().st_size for f in settings.DOWNLOAD_DIR.glob("*") if f.is_file())
    out_size = sum(f.stat().st_size for f in settings.OUTPUT_DIR.glob("*") if f.is_file())
    text = (
        "**Server Storage Diagnostics**\n\n"
        f"• **Free Space:** `{disk['free_gb']} GB free`\n"
        f"• **Total Space:** `{disk['total_gb']} GB total`\n"
        f"• **Used Space:** `{disk['used_gb']} GB` ({disk['percent_used']}% used)\n\n"
        "**Active Temporary Cache:**\n"
        f"• Downloads folder: `{format_bytes(dl_size)}`\n"
        f"• Outputs folder: `{format_bytes(out_size)}`\n\n"
        "Use `/files` to view, recall, or delete individual cached files."
    )
    await event.respond(text)
    await _safe_delete_incoming_command(event)


async def handle_files(event: events.NewMessage.Event) -> None:
    """Handles /files command to view, recall, and delete server cached files."""
    user_id = event.sender_id
    files = await get_user_cached_files(user_id)
    disk = settings.get_disk_usage()

    if not files:
        text = (
            "**Server Cached Files**\n\n"
            "You have no active files currently cached on the server.\n"
            "All past files have either been deleted or cleaned up.\n\n"
            f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)"
        )
        await event.respond(text)
        await _safe_delete_incoming_command(event)
        return

    total_size = sum(f["size"] for f in files)
    text = (
        f"**Server Cached Files ({len(files)} stored)**\n\n"
        f"• **Total Cache Stored:** `{format_bytes(total_size)}`\n"
        f"• **Server Free Space:** `{disk['free_gb']} GB / {disk['total_gb']} GB` ({disk['percent_used']}% used)\n\n"
        "Select any file below to **Recall** (send back to you) or **Delete** from server:"
    )
    menu = build_files_list_menu(files, page=0)
    await event.respond(text, buttons=menu)
    await _safe_delete_incoming_command(event)
