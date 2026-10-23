"""
Interactive Telegram Inline Keyboard Builders
"""

from telethon import Button
from typing import List


def build_main_menu(task_id: str) -> List[List[Button]]:
    """Builds the primary 3-option menu."""
    return [
        [
            Button.inline("1. Optimize", data=f"opt_menu:{task_id}".encode()),
            Button.inline("2. Convert", data=f"conv_menu:{task_id}".encode())
        ],
        [
            Button.inline("3. AI Manual (Coming Soon)", data=f"ai_manual:{task_id}".encode())
        ]
    ]


def build_optimize_menu(task_id: str) -> List[List[Button]]:
    """Builds the 3 optimization sub-options menu."""
    return [
        [Button.inline("1. Low Device (Max Compatibility)", data=f"opt_run:low:{task_id}".encode())],
        [Button.inline("2. Moderate (Mid-Range Device)", data=f"opt_run:mod:{task_id}".encode())],
        [Button.inline("3. Auto Recommended (Smart Fix)", data=f"opt_run:auto:{task_id}".encode())],
        [Button.inline("< Back to Main Menu", data=f"main_menu:{task_id}".encode())]
    ]


def build_convert_menu(task_id: str) -> List[List[Button]]:
    """Builds video conversion and audio extraction choices."""
    return [
        [
            Button.inline("Fix Audio (Smartphone AAC)", data=f"conv_run:audio_fix:{task_id}".encode())
        ],
        [
            Button.inline("MP4", data=f"conv_run:mp4:{task_id}".encode()),
            Button.inline("MKV", data=f"conv_run:mkv:{task_id}".encode()),
            Button.inline("WEBM", data=f"conv_run:webm:{task_id}".encode())
        ],
        [
            Button.inline("AVI", data=f"conv_run:avi:{task_id}".encode()),
            Button.inline("MOV", data=f"conv_run:mov:{task_id}".encode()),
            Button.inline("MPG", data=f"conv_run:mpg:{task_id}".encode())
        ],
        [
            Button.inline("Extract MP3", data=f"conv_run:mp3:{task_id}".encode()),
            Button.inline("Extract AAC", data=f"conv_run:aac:{task_id}".encode())
        ],
        [
            Button.inline("< Back to Main Menu", data=f"main_menu:{task_id}".encode())
        ]
    ]


def build_cleanup_original_menu(task_id: str) -> List[List[Button]]:
    """Builds Step 1 menu: Delete or Keep the Original source file."""
    return [
        [
            Button.inline("Delete Original File", data=f"del_orig:{task_id}".encode()),
            Button.inline("Keep Original File", data=f"keep_orig:{task_id}".encode())
        ]
    ]


def build_cleanup_output_menu(task_id: str) -> List[List[Button]]:
    """Builds Step 2 menu: Delete or Keep the Processed/Optimized file."""
    return [
        [
            Button.inline("Delete Processed File", data=f"del_opt:{task_id}".encode()),
            Button.inline("Keep Processed File", data=f"keep_opt:{task_id}".encode())
        ]
    ]
