"""
Asynchronous FFmpeg Execution Engine with Live Progress Parser
"""

import asyncio
import logging
import re
import time
from typing import List, Callable, Optional, Awaitable, Any

logger = logging.getLogger(__name__)

# Type for progress callback: accepts percent, speed_str, eta_sec, fps_val, and optional kwargs like step_label
ProgressCallback = Callable[..., Awaitable[Any]]


def parse_time_str(time_str: str) -> float:
    """Parses FFmpeg out_time string (HH:MM:SS.micro) to seconds."""
    parts = time_str.strip().split(":")
    if len(parts) == 3:
        try:
            return float(parts[0]) * 3600 + float(parts[1]) * 60 + float(parts[2])
        except ValueError:
            pass
    return 0.0


async def run_ffmpeg_with_progress(
    cmd: List[str],
    total_duration_seconds: float,
    progress_callback: Optional[ProgressCallback] = None,
    progress_interval: float = 0.2,
    on_process_started: Optional[Callable[[Any], None]] = None
) -> None:
    """
    Executes FFmpeg with asynchronous line-by-line progress parsing.
    """
    # Ensure -progress pipe:1 and -nostats are present
    full_cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    
    # Check if -progress already in cmd
    has_progress = "-progress" in cmd
    if not has_progress:
        full_cmd.extend(["-progress", "pipe:1", "-nostats"])
    
    full_cmd.extend(cmd)
    logger.info(f"Running FFmpeg command: {' '.join(full_cmd)}")

    proc = await asyncio.create_subprocess_exec(
        *full_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE
    )

    if on_process_started:
        try:
            on_process_started(proc)
        except Exception:
            pass

    current_fps = 0.0
    current_speed = "1.0x"
    last_update_time = 0.0
    last_callback_time = time.time()
    last_percent = 0.0
    loop = asyncio.get_event_loop()

    async def _stall_detector():
        nonlocal last_callback_time, last_percent
        while True:
            await asyncio.sleep(10)
            if time.time() - last_callback_time > 30 and proc.returncode is None:
                if progress_callback:
                    try:
                        await progress_callback(last_percent, current_speed, 0, current_fps, stalled=True)
                    except TypeError:
                        await progress_callback(last_percent, current_speed, 0, current_fps)
                    except Exception:
                        pass

    stall_task = asyncio.create_task(_stall_detector()) if progress_callback else None

    try:
        while True:
            line_bytes = await proc.stdout.readline()
            if not line_bytes:
                break
            line = line_bytes.decode(errors="ignore").strip()
            if not line or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            value = value.strip()

            if key == "fps":
                try:
                    current_fps = float(value)
                except ValueError:
                    pass
            elif key == "speed":
                current_speed = value.rstrip()
            elif key in ("out_time_ms", "out_time_us"):
                try:
                    val = float(value)
                    # out_time_us is in microseconds, out_time_ms is in microseconds in FFmpeg progress
                    cur_sec = val / 1_000_000.0
                    if total_duration_seconds > 0:
                        percent = min(99.0, (cur_sec / total_duration_seconds) * 100.0)
                        
                        now = loop.time()
                        if progress_callback and (now - last_update_time >= progress_interval):
                            last_update_time = now
                            
                            # Estimate ETA
                            speed_num = 1.0
                            m = re.search(r"([\d\.]+)x", current_speed)
                            if m:
                                speed_num = float(m.group(1)) or 1.0
                            remaining_sec = max(0.0, total_duration_seconds - cur_sec)
                            eta = int(remaining_sec / speed_num) if speed_num > 0 else None

                            try:
                                await progress_callback(percent, current_speed, eta, current_fps)
                                last_callback_time = time.time()
                                last_percent = percent
                            except Exception as e:
                                logger.debug(f"Progress callback exception: {e}")
                except Exception:
                    pass
            elif key == "progress" and value == "end":
                if progress_callback:
                    try:
                        await progress_callback(100.0, current_speed, 0, current_fps)
                        last_callback_time = time.time()
                        last_percent = 100.0
                    except Exception:
                        pass

        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            err_msg = stderr.decode(errors="ignore")
            logger.error(f"FFmpeg process returned code {proc.returncode}: {err_msg}")
            raise RuntimeError(f"FFmpeg encoding error (code {proc.returncode}): {err_msg}")

    except asyncio.CancelledError:
        try:
            proc.kill()
            await proc.wait()
        except Exception:
            pass
        raise
    finally:
        if stall_task and not stall_task.done():
            stall_task.cancel()
            try:
                await stall_task
            except asyncio.CancelledError:
                pass
            except Exception:
                pass

