"""
backend/task_manager.py
Task queue and lifecycle management.
Coordinates tasks, tracks execution states, and stores task histories.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.schemas.task import (
    ImplementationTask,
    TaskResult,
    TaskSpec,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class TaskManager:
    """
    Manages in-memory task states, queues, and execution histories.
    """

    def __init__(self) -> None:
        self._tasks: Dict[str, TaskSpec] = {}
        self._subtasks: Dict[str, List[ImplementationTask]] = {}
        self._results: Dict[str, TaskResult] = {}
        self._lock = asyncio.Lock()

    async def register_task(self, task: TaskSpec) -> TaskSpec:
        """Register a new top-level task."""
        async with self._lock:
            self._tasks[task.id] = task
        logger.info("Task registered: %s (%s)", task.id, task.description[:60])
        return task

    async def register_subtasks(
        self, parent_task_id: str, subtasks: List[ImplementationTask]
    ) -> None:
        """Register decomposed subtasks for a parent task."""
        async with self._lock:
            self._subtasks[parent_task_id] = subtasks
        logger.info(
            "Registered %d subtasks for parent %s", len(subtasks), parent_task_id
        )

    async def update_subtask_status(
        self,
        parent_task_id: str,
        subtask_id: str,
        status: TaskStatus,
        result: Optional[TaskResult] = None,
    ) -> None:
        """Update status and result of a specific subtask."""
        async with self._lock:
            sublist = self._subtasks.get(parent_task_id, [])
            for st in sublist:
                if st.id == subtask_id:
                    st.status = status
                    if result:
                        st.result = result
                    break

    async def store_result(self, result: TaskResult) -> None:
        """Store the final result of a task."""
        async with self._lock:
            self._results[result.task_id] = result
        logger.info("Stored result for task %s (status=%s)", result.task_id, result.status)

    async def get_task(self, task_id: str) -> Optional[TaskSpec]:
        async with self._lock:
            return self._tasks.get(task_id)

    async def get_subtasks(self, parent_task_id: str) -> List[ImplementationTask]:
        async with self._lock:
            return list(self._subtasks.get(parent_task_id, []))

    async def get_result(self, task_id: str) -> Optional[TaskResult]:
        async with self._lock:
            return self._results.get(task_id)

    async def list_recent_tasks(self, limit: int = 20) -> List[TaskSpec]:
        async with self._lock:
            tasks = list(self._tasks.values())
            tasks.sort(key=lambda t: t.created_at, reverse=True)
            return tasks[:limit]

