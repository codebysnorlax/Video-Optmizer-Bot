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
    """Builds the 4 optimization sub-options menu."""
    return [
        [Button.inline("1. Low Device (Max Compatibility)", data=f"opt_run:low:{task_id}".encode())],
        [Button.inline("2. Moderate (Mid-Range Device)", data=f"opt_run:mod:{task_id}".encode())],
        [Button.inline("3. Auto Recommended (Smart Fix)", data=f"opt_run:auto:{task_id}".encode())],
        [Button.inline("4. Auto + Best Quality + Fix Size", data=f"opt_run:best:{task_id}".encode())],
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


def build_files_list_menu(files: list, page: int = 0, page_size: int = 5) -> List[List[Button]]:
    """Builds interactive list of cached files with pagination."""
    buttons = []
    total_files = len(files)
    start_idx = page * page_size
    end_idx = min(start_idx + page_size, total_files)

    for i in range(start_idx, end_idx):
        f = files[i]
        type_tag = "[Orig]" if f["file_type"] == "original" else "[Opt]"
        # Format size
        size_mb = f["size"] / (1024 * 1024)
        size_str = f"{size_mb:.1f}M" if size_mb >= 1 else f"{f['size'] // 1024}K"
        btn_label = f"{type_tag} {f['name'][:18]} ({size_str})"
        callback = f"fview:{f['file_type']}:{f['task_id']}"
        buttons.append([Button.inline(btn_label, data=callback.encode())])

    # Pagination controls
    nav_row = []
    if page > 0:
        nav_row.append(Button.inline("< Prev", data=f"flist:{page - 1}".encode()))
    if end_idx < total_files:
        nav_row.append(Button.inline("Next >", data=f"flist:{page + 1}".encode()))
    if nav_row:
        buttons.append(nav_row)

    buttons.append([Button.inline("Refresh List", data=f"flist:{page}".encode())])
    return buttons


def build_file_action_menu(task_id: str, file_type: str) -> List[List[Button]]:
    """Builds actions for a specific cached file: Recall or Delete."""
    return [
        [
            Button.inline("Recall / Send to Me", data=f"recall:{file_type}:{task_id}".encode())
        ],
        [
            Button.inline("Delete from Server", data=f"del_f:{file_type}:{task_id}".encode())
        ],
        [
            Button.inline("< Back to Files List", data=b"flist:0")
        ]
    ]


def build_recall_progress_menu(recall_id: str) -> List[List[Button]]:
    """Builds progress keyboard with Stop Sending button."""
    return [
        [
            Button.inline("Stop Sending", data=f"stop_rec:{recall_id}".encode())
        ]
    ]


def build_confirm_stop_menu(recall_id: str) -> List[List[Button]]:
    """Builds confirmation keyboard to verify user wants to stop transfer."""
    return [
        [
            Button.inline("Yes, Stop Sending", data=f"yes_stop:{recall_id}".encode()),
            Button.inline("No, Continue Sending", data=f"no_stop:{recall_id}".encode())
        ]
    ]
