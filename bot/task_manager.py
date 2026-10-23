"""
In-flight Task Manager & Concurrency Control
"""

import asyncio
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional
from core.probe import MediaMetadata
from config.settings import settings


@dataclass
class TaskContext:
    task_id: str
    user_id: int
    input_path: Path
    output_path: Optional[Path]
    meta: MediaMetadata
    status_message: any
    original_message_id: int
    t_received: float = 0.0
    download_time: float = 0.0
    probe_time: float = 0.0


class TaskManager:
    """Manages active tasks and worker queue concurrency."""

    def __init__(self, max_concurrent: int = 1):
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._active_tasks: Dict[str, TaskContext] = {}

    def register_task(self, context: TaskContext) -> None:
        self._active_tasks[context.task_id] = context

    def get_task(self, task_id: str) -> Optional[TaskContext]:
        return self._active_tasks.get(task_id)

    def remove_task(self, task_id: str) -> Optional[TaskContext]:
        return self._active_tasks.pop(task_id, None)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._semaphore


task_manager = TaskManager(max_concurrent=settings.MAX_CONCURRENT_ENCODING)
