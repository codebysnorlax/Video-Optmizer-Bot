"""
In-flight Task Manager & Concurrency Control
"""

import asyncio
import logging
from pathlib import Path
from dataclasses import dataclass
from typing import Dict, Optional, List, Set, Any
from contextlib import asynccontextmanager
from core.probe import MediaMetadata
from config.settings import settings

logger = logging.getLogger(__name__)


@dataclass
class TaskContext:
    task_id: str
    user_id: int
    input_path: Path
    output_path: Optional[Path]
    meta: Optional[MediaMetadata]
    status_message: Any
    original_message_id: int
    t_received: float = 0.0
    download_time: float = 0.0
    probe_time: float = 0.0
    is_processing: bool = False
    current_stage: str = "idle"  # "downloading", "queued", "encoding", "uploading", "idle"
    is_cancelled: bool = False
    is_confirming_stop: bool = False
    active_async_task: Optional[asyncio.Task] = None
    current_process: Optional[Any] = None
    active_stage_label: str = ""
    ai_session_active: bool = False
    ai_agent: Optional[Any] = None
    ai_workplace_dir: Optional[Path] = None
    ai_last_action_plan: Optional[Dict[str, Any]] = None
    assets: Optional[Dict[str, Any]] = None
    processing_elapsed_time: float = 0.0

    def get_assets(self) -> Dict[str, Any]:
        """Returns registered session assets, initializing v1 from input_path if needed."""
        if self.assets is None:
            self.assets = {}
        if "v1" not in self.assets and self.input_path:
            self.assets["v1"] = {
                "id": "v1",
                "filename": self.input_path.name,
                "filepath": self.input_path,
                "media_type": "video" if (self.meta and self.meta.has_video) else "media",
                "meta": self.meta
            }
        return self.assets

    def add_asset(self, filepath: Path, meta: Optional[MediaMetadata] = None, media_type: str = "media") -> str:
        """Registers a newly uploaded asset and returns its alias key (e.g. 'a1', 'v2')."""
        assets = self.get_assets()
        prefix = "a" if media_type == "audio" else "v"
        idx = 1
        key = f"{prefix}{idx}"
        while key in assets:
            idx += 1
            key = f"{prefix}{idx}"
        assets[key] = {
            "id": key,
            "filename": filepath.name,
            "filepath": filepath,
            "media_type": media_type,
            "meta": meta
        }
        return key


@dataclass
class RecallContext:
    recall_id: str
    user_id: int
    task_id: str
    file_type: str
    file_path: Path
    file_name: str
    file_size: int
    status_msg: Any
    upload_task: Optional[asyncio.Task] = None
    is_cancelled: bool = False
    is_confirming_stop: bool = False


@dataclass
class QueuedEncodingTask:
    task_id: str
    event: asyncio.Event
    updater: Optional[Any] = None
    mode_label: str = ""
    file_name: str = ""


class TaskManager:
    """Manages active tasks, recall operations, and media processing queue concurrency."""

    def __init__(self, max_concurrent: int = 1):
        self._max_concurrent = max(1, max_concurrent)
        self._semaphore = asyncio.Semaphore(self._max_concurrent)
        self._active_tasks: Dict[str, TaskContext] = {}
        self._active_recalls: Dict[str, RecallContext] = {}
        self._active_encoders: Set[str] = set()
        self._wait_queue: List[QueuedEncodingTask] = []
        self._queue_lock = asyncio.Lock()

    def register_task(self, context: TaskContext) -> None:
        self._active_tasks[context.task_id] = context

    def get_task(self, task_id: str) -> Optional[TaskContext]:
        return self._active_tasks.get(task_id)

    def get_active_ai_task_for_user(self, user_id: int) -> Optional[TaskContext]:
        """Finds any active AI session task for a specific user."""
        for task in self._active_tasks.values():
            if task.user_id == user_id and task.ai_session_active:
                return task
        return None

    def get_latest_task_for_user(self, user_id: int) -> Optional[TaskContext]:
        """Finds the most recent task for a user, regardless of whether AI session was toggled."""
        for task in reversed(list(self._active_tasks.values())):
            if task.user_id == user_id:
                return task
        return None

    def remove_task(self, task_id: str) -> Optional[TaskContext]:
        return self._active_tasks.pop(task_id, None)

    def register_recall(self, ctx: RecallContext) -> None:
        self._active_recalls[ctx.recall_id] = ctx

    def get_recall(self, recall_id: str) -> Optional[RecallContext]:
        return self._active_recalls.get(recall_id)

    def remove_recall(self, recall_id: str) -> Optional[RecallContext]:
        return self._active_recalls.pop(recall_id, None)

    @property
    def semaphore(self) -> asyncio.Semaphore:
        return self._semaphore

    def get_queue_position(self, task_id: str) -> Optional[int]:
        """Returns 1-based queue position if waiting, 0 if currently active, or None if not found."""
        if task_id in self._active_encoders:
            return 0
        for i, item in enumerate(self._wait_queue):
            if item.task_id == task_id:
                return i + 1
        return None

    def get_queue_length(self) -> int:
        return len(self._wait_queue)

    def get_active_encoder_count(self) -> int:
        return len(self._active_encoders)

    def _grant_next_waiter(self) -> None:
        """Must be called while holding self._queue_lock. Grants available slots to queued tasks."""
        while len(self._active_encoders) < self._max_concurrent and self._wait_queue:
            next_waiter = self._wait_queue.pop(0)
            self._active_encoders.add(next_waiter.task_id)
            next_waiter.event.set()

    async def _notify_queue_positions(self) -> None:
        """Broadcasts updated queue positions to all waiting users."""
        async with self._queue_lock:
            waiters = list(self._wait_queue)
            active_jobs = len(self._active_encoders)
            total_queued = len(self._wait_queue)

        if not waiters:
            return

        from bot.progress import format_queue_status
        from bot.keyboards import build_task_progress_menu

        for i, waiter in enumerate(waiters):
            if waiter.updater:
                try:
                    # Do not overwrite if the user is currently reading the confirmation dialog
                    t_ctx = self.get_task(waiter.task_id)
                    if t_ctx and t_ctx.is_confirming_stop:
                        continue

                    pos = i + 1
                    msg = format_queue_status(
                        position=pos,
                        action_label=waiter.mode_label,
                        file_name=waiter.file_name,
                        active_jobs=active_jobs,
                        total_queued=total_queued
                    )
                    await waiter.updater.update(msg, buttons=build_task_progress_menu(waiter.task_id), force=True)
                except Exception as e:
                    logger.debug(f"Queue position update error for task {waiter.task_id}: {e}")

    @asynccontextmanager
    async def acquire_encoding_slot(
        self,
        task_id: str,
        updater: Optional[Any] = None,
        mode_label: str = "",
        file_name: str = ""
    ):
        """
        Acquires an exclusive media encoding slot.
        If all slots are busy, queues the task, notifies the user of their exact position,
        and automatically advances when earlier jobs finish.
        Only media transcoding holds this lock; downloads and uploads run concurrently outside.
        """
        queued_item: Optional[QueuedEncodingTask] = None
        slot_acquired = False

        async with self._queue_lock:
            if len(self._active_encoders) < self._max_concurrent:
                self._active_encoders.add(task_id)
                slot_acquired = True
            else:
                queued_item = QueuedEncodingTask(
                    task_id=task_id,
                    event=asyncio.Event(),
                    updater=updater,
                    mode_label=mode_label,
                    file_name=file_name
                )
                self._wait_queue.append(queued_item)
                position = len(self._wait_queue)
                total_queued = len(self._wait_queue)
                active_jobs = len(self._active_encoders)

        if not slot_acquired and queued_item is not None:
            # Set task context stage to queued
            task_ctx = self.get_task(task_id)
            if task_ctx:
                task_ctx.current_stage = "queued"
                task_ctx.active_stage_label = f"Queued for {mode_label}"

            if updater:
                from bot.progress import format_queue_status
                from bot.keyboards import build_task_progress_menu
                queue_text = format_queue_status(
                    position=position,
                    action_label=mode_label,
                    file_name=file_name,
                    active_jobs=active_jobs,
                    total_queued=total_queued
                )
                await updater.update(queue_text, buttons=build_task_progress_menu(task_id), force=True)

            try:
                await queued_item.event.wait()
                slot_acquired = True
            except asyncio.CancelledError:
                async with self._queue_lock:
                    if queued_item in self._wait_queue:
                        self._wait_queue.remove(queued_item)
                    elif task_id in self._active_encoders:
                        self._active_encoders.discard(task_id)
                        self._grant_next_waiter()
                asyncio.create_task(self._notify_queue_positions())
                raise

        try:
            task_ctx = self.get_task(task_id)
            if task_ctx:
                task_ctx.current_stage = "encoding"
                task_ctx.active_stage_label = mode_label

            yield
        finally:
            async with self._queue_lock:
                if queued_item and queued_item in self._wait_queue:
                    self._wait_queue.remove(queued_item)
                if task_id in self._active_encoders:
                    self._active_encoders.discard(task_id)
                    self._grant_next_waiter()

            asyncio.create_task(self._notify_queue_positions())


task_manager = TaskManager(max_concurrent=settings.MAX_CONCURRENT_ENCODING)
