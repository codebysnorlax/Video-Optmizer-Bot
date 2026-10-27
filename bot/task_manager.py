"""
In-flight Task Manager & Concurrency Control
"""

import asyncio
import time
import logging
from pathlib import Path
from dataclasses import dataclass, field
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
    _processing_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
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
    last_activity_time: float = 0.0

    def __post_init__(self):
        if self.last_activity_time == 0.0:
            self.last_activity_time = time.time()

    def touch(self) -> None:
        """Updates last_activity_time to current timestamp."""
        self.last_activity_time = time.time()

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

    async def _idle_session_reaper(self, bot_client=None) -> None:
        """Background coroutine that reaps AI sessions idle for > AI_SESSION_IDLE_TIMEOUT."""
        IDLE_TIMEOUT = getattr(settings, "AI_SESSION_IDLE_TIMEOUT", 3600)
        CHECK_INTERVAL = 60  # check every 60 seconds

        while True:
            try:
                await asyncio.sleep(CHECK_INTERVAL)
                now = time.time()
                tasks_to_reap = []

                for task_id, task_ctx in list(self._active_tasks.items()):
                    # NEVER reap tasks that are currently busy or actively encoding/uploading/downloading/processing
                    is_busy = (
                        task_ctx.is_processing
                        or (hasattr(task_ctx, "_processing_lock") and task_ctx._processing_lock.locked())
                        or task_ctx.current_process is not None
                        or (task_ctx.active_async_task is not None and not task_ctx.active_async_task.done())
                        or task_ctx.current_stage not in ("idle", "")
                        or task_id in self._active_encoders
                    )
                    if is_busy:
                        task_ctx.touch()
                        continue

                    if (task_ctx.ai_session_active and
                            now - task_ctx.last_activity_time > IDLE_TIMEOUT):
                        tasks_to_reap.append((task_id, task_ctx))

                for task_id, task_ctx in tasks_to_reap:
                    # Double-check busy status right before cleanup
                    is_busy = (
                        task_ctx.is_processing
                        or (hasattr(task_ctx, "_processing_lock") and task_ctx._processing_lock.locked())
                        or task_ctx.current_process is not None
                        or (task_ctx.active_async_task is not None and not task_ctx.active_async_task.done())
                        or task_ctx.current_stage not in ("idle", "")
                        or task_id in self._active_encoders
                    )
                    if is_busy:
                        task_ctx.touch()
                        continue

                    user_id = task_ctx.user_id
                    timeout_mins = int(IDLE_TIMEOUT // 60)
                    logger.info(f"Idle session reaper: cleaning up session for user {user_id}, task {task_id} (inactive for {int(now - task_ctx.last_activity_time)}s)")

                    # (a) Deactivate session
                    task_ctx.ai_session_active = False

                    # (b) Clear agent reference
                    task_ctx.ai_agent = None

                    # (c) Clean workplace
                    try:
                        settings.clean_ai_workplace(user_id, task_id)
                    except Exception as e:
                        logger.warning(f"Error cleaning workplace during idle reap: {e}")

                    # (d) Delete persisted session
                    try:
                        from database.models import delete_ai_session
                        await delete_ai_session(user_id, task_id)
                    except Exception as e:
                        logger.warning(f"Error deleting session during idle reap: {e}")

                    # (e) Send timeout message to user
                    if bot_client:
                        try:
                            await bot_client.send_message(
                                user_id,
                                f"**AI session timed out after {timeout_mins} minutes of inactivity.**\n"
                                "Your session has been cleaned up to free resources. "
                                "You can start a new AI session anytime by sending a video."
                            )
                        except Exception as e:
                            logger.debug(f"Could not send idle timeout message to user {user_id}: {e}")

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Idle session reaper error: {e}")

    def start_idle_reaper(self, bot_client=None) -> asyncio.Task:
        """Starts the idle session reaper as a background task."""
        return asyncio.create_task(self._idle_session_reaper(bot_client))


task_manager = TaskManager(max_concurrent=settings.MAX_CONCURRENT_ENCODING)
