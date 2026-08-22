"""WorkerAgent — 瞬时子代理执行引擎。

单 agent 和多 agent 共享此实现。
"""

from coworker.workers.worker_config import TaskBrief, WorkerConfig, WorkerResult
from coworker.workers.worker import WorkerAgent

__all__ = ["WorkerAgent", "WorkerConfig", "TaskBrief", "WorkerResult"]
