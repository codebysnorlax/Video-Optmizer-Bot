"""
Command Handlers (/start, /help, /hardware, /stats, /history)
"""

from telethon import events
from config.settings import settings
from core.hardware import current_hardware
from core.probe import format_bytes
from database.models import upsert_user, get_user_stats, get_user_recent_tasks


async def handle_start(event: events.NewMessage.Event) -> None:
    """Handles /start command."""
    user = await event.get_sender()
    await upsert_user(event.sender_id, getattr(user, "username", ""), getattr(user, "first_name", ""))

    text = (
        "👋 *Welcome to the Ultra-Fast Video Optimizer & Converter Bot!*\n\n"
        "Send or forward *any video format* to get started. I will instantly analyze it and give you powerful options:\n\n"
        "🚀 *1. Optimize:*\n"
        " • `Low Device`: Max playback compatibility on older phones & TVs\n"
        " • `Moderate`: Crisp quality with mid-range bitrate\n"
        " • `Auto Recommended`: Smart AI-like analyzer that fixes raw camera bitrate bloat (e.g. 500MB ➡️ 18MB) with zero perceptual quality loss\n\n"
        "🔄 *2. Convert:*\n"
        " • Transcode to MP4, MKV, WEBM, AVI, MOV, MPG\n"
        " • Extract audio to MP3, AAC, OGG, WAV\n\n"
        "🤖 *3. AI Manual (Coming Soon)*\n\n"
        "⚡ *Engine:* Powered by `" + current_hardware.description + "`\n\n"
        "💡 *Just drop or forward a video here to begin!*"
    )
    await event.reply(text)


async def handle_help(event: events.NewMessage.Event) -> None:
    """Handles /help command."""
    text = (
        "📖 *How to use Video Optimizer Bot:*\n\n"
        "1️⃣ *Send any video:* Up to 2,000 MB (2 GB) supported!\n"
        "2️⃣ *Review Analysis:* The bot immediately inspects resolution, duration, codecs, and bitrate.\n"
        "3️⃣ *Select Action:*\n"
        "   - *Optimize (Auto Recommended):* Best for camera videos where 40s takes 500MB. Shrinks file size up to 95% with pristine quality.\n"
        "   - *Optimize (Low Device):* Guarantees smooth playback on budget phones and older TVs.\n"
        "   - *Optimize (Moderate):* Balanced crisp profile.\n"
        "   - *Convert:* Transcode video containers or extract high-quality audio.\n"
        "4️⃣ *Live Progress:* Watch download, transcoding, and upload progress in real-time.\n"
        "5️⃣ *Manage Server Cache:* Prompt to delete or keep the server copy to save disk space.\n\n"
        "🛠 *Commands:*\n"
        "• `/stats` - View your total saved bandwidth and usage\n"
        "• `/hardware` - View host hardware acceleration & system info\n"
        "• `/history` - View your recent tasks"
    )
    await event.reply(text)


async def handle_hardware(event: events.NewMessage.Event) -> None:
    """Handles /hardware diagnostics command."""
    disk = settings.get_disk_usage()
    text = (
        "⚙️ *Host Hardware & Platform Diagnostics*\n\n"
        f"🖥 *OS:* `{current_hardware.os_name}`\n"
        f"🏗 *Architecture:* `{current_hardware.arch}`\n"
        f"📱 *Termux Mobile Server:* `{'Yes' if current_hardware.is_termux else 'No'}`\n"
        f"🧠 *CPU Model:* `{current_hardware.cpu_model}`\n"
        f"🧵 *CPU Cores:* `{current_hardware.cpu_cores} active threads`\n"
        f"📊 *RAM Usage:* `{current_hardware.available_ram_mb} MB free / {current_hardware.total_ram_mb} MB total`\n"
        f"🚀 *Hardware Acceleration:* `{current_hardware.description}`\n"
        f"🎞 *Primary Codec:* `{current_hardware.video_encoder}`\n\n"
        f"💾 *Server Storage:* `{disk['free_gb']} GB free / {disk['total_gb']} GB total` ({disk['percent_used']}% used)"
    )
    await event.reply(text)


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
        "📊 *Your Video Optimization Statistics*\n\n"
        f"🎯 *Total Tasks:* `{total_tasks}` (`{successful}` completed)\n"
        f"📥 *Total Input Data:* `{format_bytes(in_bytes)}`\n"
        f"📤 *Total Output Data:* `{format_bytes(out_bytes)}`\n"
        f"📉 *Total Bandwidth Saved:* `{format_bytes(saved_bytes)}` ({ratio:.1f}%)\n"
        f"⏱ *Average Processing Time:* `{avg_time:.1f}s`\n\n"
        f"💾 *Server Disk Status:* `{disk['free_gb']} GB free / {disk['total_gb']} GB total`"
    )
    await event.reply(text)


async def handle_history(event: events.NewMessage.Event) -> None:
    """Handles /history command."""
    user_id = event.sender_id
    tasks = await get_user_recent_tasks(user_id, limit=5)
    
    if not tasks:
        await event.reply("📂 You have no processing history yet. Send a video to get started!")
        return

    lines = ["📜 *Your Recent Processing History:*\n"]
    for t in tasks:
        status_emoji = "✅" if t['status'] == "completed" else "❌"
        lines.append(
            f"{status_emoji} *{t['file_name']}*\n"
            f"   • Action: `{t['action']}` | Date: `{t['created_at']}`\n"
            f"   • Size: `{format_bytes(t['input_size'])}` ➡️ `{format_bytes(t['output_size'])}`"
        )
    await event.reply("\n".join(lines))
