"""传输队列。

设计要点：
- 传输全部在后台线程池执行，绝不占用 UI 线程
- 每条任务独立建连（FTP 控制连接不是线程安全的，共享会串包）
- 任务状态通过回调推给上层，UI 只负责渲染
"""
from __future__ import annotations

import logging
import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, List, Optional

from .backend import (
    RemoteFile,
    SessionConfig,
    SessionError,
    TransferCancelled,
    UnsupportedOperation,
    create_session,
)

log = logging.getLogger(__name__)


class TaskState(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass
class TransferTask:
    direction: str                 # upload | download
    local_path: str
    remote_path: str
    size: int = 0
    done: int = 0
    state: TaskState = TaskState.QUEUED
    error: str = ""
    started_at: float = 0.0
    finished_at: float = 0.0
    _cancel: threading.Event = field(default_factory=threading.Event, repr=False)

    @property
    def name(self) -> str:
        return os.path.basename(self.local_path) or os.path.basename(self.remote_path)

    @property
    def percent(self) -> float:
        if self.size <= 0:
            return 100.0 if self.state == TaskState.DONE else 0.0
        return min(100.0, self.done * 100.0 / self.size)

    @property
    def speed(self) -> float:
        if self.state != TaskState.RUNNING or not self.started_at:
            return 0.0
        elapsed = max(0.001, time.time() - self.started_at)
        return self.done / elapsed

    def cancel(self) -> None:
        self._cancel.set()

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class TransferQueue:
    """线程安全的传输队列。"""

    def __init__(self, cfg_provider: Callable[[], SessionConfig], max_workers: int = 2):
        self._cfg_provider = cfg_provider
        self._pool = ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="xfer")
        self._tasks: List[TransferTask] = []
        self._lock = threading.Lock()
        self._listeners: List[Callable[[TransferTask], None]] = []
        self.max_workers = max_workers

    # ------------------------------------------------------------ 订阅
    def add_listener(self, cb: Callable[[TransferTask], None]) -> None:
        self._listeners.append(cb)

    def _notify(self, task: TransferTask) -> None:
        for cb in list(self._listeners):
            try:
                cb(task)
            except Exception:
                log.exception("传输回调异常")

    # ------------------------------------------------------------ 任务
    def submit(
        self,
        direction: str,
        local_path: str,
        remote_path: str,
        size: int = 0,
        resume: bool = False,
    ) -> TransferTask:
        task = TransferTask(
            direction=direction,
            local_path=local_path,
            remote_path=remote_path,
            size=size,
        )
        task._resume = resume  # type: ignore[attr-defined]
        with self._lock:
            self._tasks.append(task)
        self._notify(task)
        self._pool.submit(self._run, task)
        return task

    def tasks(self) -> List[TransferTask]:
        with self._lock:
            return list(self._tasks)

    def clear_finished(self) -> None:
        with self._lock:
            self._tasks = [t for t in self._tasks
                           if t.state in (TaskState.QUEUED, TaskState.RUNNING)]

    def cancel_all(self) -> None:
        for t in self.tasks():
            if t.state in (TaskState.QUEUED, TaskState.RUNNING):
                t.cancel()

    def shutdown(self) -> None:
        self.cancel_all()
        self._pool.shutdown(wait=False)

    # ------------------------------------------------------------ 执行
    def _run(self, task: TransferTask) -> None:
        task.state = TaskState.RUNNING
        task.started_at = time.time()
        self._notify(task)
        try:
            cfg = self._cfg_provider()
            offset = self._resume_offset(task, cfg)
            with create_session(cfg) as sess:
                cb = lambda d, t: self._on_progress(task, d, t)  # noqa: E731
                if task.direction == "upload":
                    sess.put(task.local_path, task.remote_path,
                             offset=offset, progress=cb,
                             cancel_flag=task.cancelled)
                else:
                    sess.get(task.remote_path, task.local_path,
                             offset=offset, progress=cb,
                             cancel_flag=task.cancelled)
            task.state = TaskState.CANCELLED if task.cancelled() else TaskState.DONE
        except TransferCancelled:
            task.state = TaskState.CANCELLED
        except UnsupportedOperation as e:
            task.state = TaskState.FAILED
            task.error = f"该协议不支持此操作：{e}"
        except SessionError as e:
            task.state = TaskState.FAILED
            task.error = str(e)
        except Exception as e:  # noqa: BLE001 兜底，避免线程静默死掉
            log.exception("传输异常")
            task.state = TaskState.FAILED
            task.error = f"{type(e).__name__}: {e}"
        finally:
            task.finished_at = time.time()
            self._notify(task)

    def _resume_offset(self, task: TransferTask, cfg: SessionConfig) -> int:
        """只在明确要求续传时返回偏移量。

        判定条件：远端（或本地）已有部分数据，且长度小于完整文件。
        满足才续传，否则从 0 开始覆盖，避免拼接出损坏文件。
        """
        if not getattr(task, "_resume", False):
            return 0
        try:
            with create_session(cfg) as probe:
                if not probe.supports_resume():
                    return 0
                if task.direction == "upload":
                    local_size = os.path.getsize(task.local_path)
                    try:
                        remote_size = probe.stat(task.remote_path).size
                    except Exception:
                        return 0
                    if 0 < remote_size < local_size:
                        log.info("续传上传 %s 从 %d 字节处继续", task.name, remote_size)
                        return remote_size
                else:
                    if not os.path.exists(task.local_path):
                        return 0
                    local_size = os.path.getsize(task.local_path)
                    remote_size = probe.stat(task.remote_path).size
                    if 0 < local_size < remote_size:
                        log.info("续传下载 %s 从 %d 字节处继续", task.name, local_size)
                        return local_size
        except Exception as e:  # noqa: BLE001
            log.debug("续传探测失败，按全量传输：%s", e)
        return 0

    def _on_progress(self, task: TransferTask, done: int, total: int) -> None:
        task.done = done
        if total > 0:
            task.size = total
        self._notify(task)
