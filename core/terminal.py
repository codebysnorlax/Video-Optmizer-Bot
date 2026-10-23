"""
Terminal Styling, Colorful Structured Logging & Animated Braille Spinners
"""

import sys
import time
import logging
from typing import Optional

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


def get_spinner() -> str:
    """Returns the next Braille spinner frame."""
    global _spinner_idx
    char = SPINNER_FRAMES[_spinner_idx % len(SPINNER_FRAMES)]
    _spinner_idx += 1
    return char


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


def term_log(
    stage: str,
    message: str,
    task_id: Optional[str] = None,
    progress: Optional[float] = None,
    extra: Optional[str] = None
) -> None:
    """
    Prints a colorful, well-structured process log line to the terminal
    featuring animated braille spinners for active stages.
    """
    timestamp = time.strftime("%H:%M:%S")
    spinner = get_spinner()
    
    stage_upper = stage.upper().strip()
    
    # Stage color styles
    if stage_upper in ("RECEIVE", "DOWNLOAD"):
        badge = f"{BOLD_CYAN}[RECEIVE]{RESET}"
        icon = f"{BOLD_CYAN}{spinner}{RESET}"
    elif stage_upper in ("PROBE", "INSPECT"):
        badge = f"{BOLD_BLUE}[PROBE]  {RESET}"
        icon = f"{BOLD_BLUE}{spinner}{RESET}"
    elif stage_upper in ("PROCESS", "OPTIMIZE", "TRANSCODE", "CONVERT"):
        badge = f"{BOLD_YELLOW}[PROCESS]{RESET}"
        icon = f"{BOLD_YELLOW}{spinner}{RESET}"
    elif stage_upper in ("UPLOAD", "SEND"):
        badge = f"{BOLD_MAGENTA}[UPLOAD] {RESET}"
        icon = f"{BOLD_MAGENTA}{spinner}{RESET}"
    elif stage_upper in ("SUCCESS", "DONE", "DELIVERED"):
        badge = f"{BOLD_GREEN}[DONE]   {RESET}"
        icon = f"{BOLD_GREEN}OK{RESET}"
    elif stage_upper in ("CLEANUP", "DELETE"):
        badge = f"{CYAN}[STORAGE]{RESET}"
        icon = f"{CYAN}*{RESET}"
    elif stage_upper in ("ERROR", "FAIL"):
        badge = f"{BOLD_RED}[ERROR]  {RESET}"
        icon = f"{BOLD_RED}X{RESET}"
    else:
        badge = f"{BOLD_WHITE}[{stage_upper:<7}]{RESET}"
        icon = f"{spinner}"

    task_tag = f"{BOLD_WHITE}[{task_id}]{RESET} " if task_id else ""
    
    prog_str = ""
    if progress is not None:
        prog_str = f" {BOLD_YELLOW}{progress:.1f}%{RESET}"

    extra_str = f" ({DIM}{extra}{RESET})" if extra else ""

    line = f"{DIM}{timestamp}{RESET} {badge} {icon} {task_tag}{message}{prog_str}{extra_str}"
    print(line, flush=True)


def print_banner_box(title: str, items: list) -> None:
    """Renders a colorful, structured box for terminal startup."""
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
