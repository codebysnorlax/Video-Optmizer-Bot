"""
Terminal Styling, Colorful Structured Logging & Animated Braille Spinners
Supports in-place dynamic spinner line updates (\r\033[K) so progress does not spam new lines.
"""

import sys
import time
import asyncio
import logging
from typing import Optional, Dict, Any

# ANSI Color Codes
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

RED = "\033[31m"
GREEN = "\033[32m"
YELLOW = "\033[33m"
BLUE = "\033[34m"
MAGENTA = "\033[35m"
CYAN = "\033[36m"
WHITE = "\033[37m"

BOLD_RED = "\033[1;31m"
BOLD_GREEN = "\033[1;32m"
BOLD_YELLOW = "\033[1;33m"
BOLD_BLUE = "\033[1;34m"
BOLD_MAGENTA = "\033[1;35m"
BOLD_CYAN = "\033[1;36m"
BOLD_WHITE = "\033[1;37m"

# Braille Spinner Frames (user requested like ⣷)
SPINNER_FRAMES = ["⣾", "⣽", "⣻", "⢿", "⡿", "⣟", "⣯", "⣷"]
_spinner_idx = 0
_is_live = False
_live_data: Optional[Dict[str, Any]] = None
_spinner_task: Optional[asyncio.Task] = None


def get_spinner() -> str:
    """Returns the next Braille spinner frame advancing at high speed."""
    global _spinner_idx
    char = SPINNER_FRAMES[_spinner_idx % len(SPINNER_FRAMES)]
    _spinner_idx += 1
    return char


def _format_line(
    stage: str,
    message: str,
    task_id: Optional[str] = None,
    progress: Optional[float] = None,
    extra: Optional[str] = None,
    spinner: Optional[str] = None
) -> str:
    timestamp = time.strftime("%H:%M:%S")
    sp = spinner or get_spinner()
    stage_upper = stage.upper().strip()

    # Stage color styles
    if stage_upper in ("RECEIVE", "DOWNLOAD"):
        badge = f"{BOLD_CYAN}[RECEIVE]{RESET}"
        icon = f"{BOLD_CYAN}{sp}{RESET}"
    elif stage_upper in ("PROBE", "INSPECT"):
        badge = f"{BOLD_BLUE}[PROBE]  {RESET}"
        icon = f"{BOLD_BLUE}{sp}{RESET}"
    elif stage_upper in ("PROCESS", "OPTIMIZE", "TRANSCODE", "CONVERT"):
        badge = f"{BOLD_YELLOW}[PROCESS]{RESET}"
        icon = f"{BOLD_YELLOW}{sp}{RESET}"
    elif stage_upper in ("UPLOAD", "SEND"):
        badge = f"{BOLD_MAGENTA}[UPLOAD] {RESET}"
        icon = f"{BOLD_MAGENTA}{sp}{RESET}"
    elif stage_upper in ("SUCCESS", "DONE", "DELIVERED"):
        badge = f"{BOLD_GREEN}[DONE]   {RESET}"
        icon = f"{BOLD_GREEN}OK{RESET}"
    elif stage_upper in ("CLEANUP", "DELETE", "STORAGE"):
        badge = f"{CYAN}[STORAGE]{RESET}"
        icon = f"{CYAN}*{RESET}"
    elif stage_upper in ("ERROR", "FAIL"):
        badge = f"{BOLD_RED}[ERROR]  {RESET}"
        icon = f"{BOLD_RED}X{RESET}"
    else:
        badge = f"{BOLD_WHITE}[{stage_upper:<7}]{RESET}"
        icon = f"{sp}"

    task_tag = f"{BOLD_WHITE}[{task_id}]{RESET} " if task_id else ""
    prog_str = f" {BOLD_YELLOW}{progress:.1f}%{RESET}" if progress is not None else ""
    extra_str = f" ({DIM}{extra}{RESET})" if extra else ""

    return f"{DIM}{timestamp}{RESET} {badge} {icon} {task_tag}{message}{prog_str}{extra_str}"


async def _fast_spinner_loop() -> None:
    """Async background task that keeps the terminal spinner animating smoothly and quickly (~14 fps)."""
    global _is_live, _live_data, _spinner_task
    try:
        while _is_live and _live_data is not None:
            await asyncio.sleep(0.07)  # 70ms = fast, energetic, silky-smooth spin
            if not _is_live or _live_data is None:
                break
            line = _format_line(
                stage=_live_data["stage"],
                message=_live_data["message"],
                task_id=_live_data["task_id"],
                progress=_live_data["progress"],
                extra=_live_data["extra"]
            )
            try:
                sys.stdout.write(f"\r\033[K{line}")
                sys.stdout.flush()
            except Exception:
                pass
    except asyncio.CancelledError:
        pass
    finally:
        _spinner_task = None


def clear_live_line() -> None:
    """Clears the live in-place progress line if active."""
    global _is_live, _live_data, _spinner_task
    if _is_live:
        _is_live = False
        _live_data = None
        if _spinner_task is not None and not _spinner_task.done():
            _spinner_task.cancel()
            _spinner_task = None
        try:
            sys.stdout.write("\r\033[K")
            sys.stdout.flush()
        except Exception:
            pass


class ColoredLogFormatter(logging.Formatter):
    """Formats standard library logging with clean ANSI color badges."""

    LEVEL_COLORS = {
        logging.DEBUG: DIM + WHITE,
        logging.INFO: BOLD_CYAN,
        logging.WARNING: BOLD_YELLOW,
        logging.ERROR: BOLD_RED,
        logging.CRITICAL: "\033[1;41m" + BOLD_WHITE
    }

    def format(self, record: logging.LogRecord) -> str:
        color = self.LEVEL_COLORS.get(record.levelno, BOLD_WHITE)
        timestamp = time.strftime("%H:%M:%S", time.localtime(record.created))
        level_tag = f"{color}[{record.levelname:<5}]{RESET}"
        name_tag = f"{DIM}{record.name}:{RESET}"
        return f"{DIM}{timestamp}{RESET} {level_tag} {name_tag} {record.getMessage()}"


class InteractiveStreamHandler(logging.StreamHandler):
    """Custom StreamHandler that clears in-place live line before logging."""

    def emit(self, record: logging.LogRecord) -> None:
        clear_live_line()
        super().emit(record)


def term_log(
    stage: str,
    message: str,
    task_id: Optional[str] = None,
    progress: Optional[float] = None,
    extra: Optional[str] = None,
    live: bool = False
) -> None:
    """
    Prints a colorful, well-structured process log line to the terminal.
    When live=True, it updates the same line in-place using carriage returns (\r\033[K)
    and spins the Braille character quickly and continuously (~14 fps)!
    """
    global _is_live, _live_data, _spinner_task
    
    line = _format_line(stage, message, task_id, progress, extra)
    
    if live:
        _live_data = {
            "stage": stage,
            "message": message,
            "task_id": task_id,
            "progress": progress,
            "extra": extra
        }
        _is_live = True
        sys.stdout.write(f"\r\033[K{line}")
        sys.stdout.flush()

        # Start or resume fast background spinner loop if in an active event loop
        try:
            loop = asyncio.get_running_loop()
            if _spinner_task is None or _spinner_task.done():
                _spinner_task = loop.create_task(_fast_spinner_loop())
        except RuntimeError:
            pass
    else:
        clear_live_line()
        print(line, flush=True)


def print_banner_box(title: str, items: list) -> None:
    """Renders a colorful, structured box for terminal startup."""
    clear_live_line()
    width = 68
    border_color = BOLD_CYAN
    line_top = f"{border_color}┌{'─' * (width - 2)}┐{RESET}"
    line_bot = f"{border_color}└{'─' * (width - 2)}┘{RESET}"
    
    print(line_top)
    title_line = f"│ {BOLD_WHITE}{title.center(width - 4)}{RESET} │"
    print(title_line)
    print(f"{border_color}├{'─' * (width - 2)}┤{RESET}")
    
    for key, val in items:
        content = f"  {BOLD_CYAN}{key:<18}{RESET}: {WHITE}{val}{RESET}"
        # Pad to border
        raw_len = len(f"  {key:<18}: {val}")
        pad = max(0, width - 4 - raw_len)
        print(f"{border_color}│{RESET}{content}{' ' * pad}{border_color}│{RESET}")

    print(line_bot)
