"""
Cost ledger — tracks cost per completed task with thread-safe aggregation.

The cost ledger is the heart of ai-tierforge's "cost-per-task" metric.
Unlike per-call cost tracking, it aggregates *all* calls for a task
(original + retries + escalations) and computes the real cost of
completing that task.

Key design decisions:
- **In-memory only (v1)**: No persistence, no DB.  State is cleared
  on process exit.  This keeps the dependency count at 2.
- **Thread-safe**: Uses per-task ``threading.Lock`` so concurrent
  agents can record calls without corrupting the ledger.
- **Decimal precision**: All monetary values use ``Decimal``, never
  ``float``, to avoid binary rounding errors.
- **Failed calls are recorded**: A failed retry still costs money
  (input tokens were consumed), so it's included in total_cost.

Example::

    ledger = CostLedger()
    ledger.record_call("task-1", failed_call)    # cost: $0.001
    ledger.record_call("task-1", failed_call)    # cost: $0.001
    ledger.record_call("task-1", success_call)   # cost: $0.011
    task = ledger.finalize_task("task-1", "code", "architect")
    # task.total_cost == $0.013  (not $0.011!)
"""

from decimal import Decimal
import threading
from typing import Optional

from ai_tierforge.types import (
    CostReport,
    EscalationEvent,
    ModelCall,
    TaskCost,
    TaskId,
    TierName,
)


class CostLedger:
    """Thread-safe in-memory cost ledger.

    Records every model call and escalation event per task, then
    computes total cost per completed task on finalization.

    Thread safety:
    - A global lock protects the lock-creation path.
    - Each task gets its own lock, so concurrent calls to *different*
      tasks don't contend.
    - Per-task locks are evicted after ``finalize_task`` to prevent
      unbounded lock growth.
    """

    def __init__(self) -> None:
        # task_id → TaskCost (the actual cost data)
        self._tasks: dict[TaskId, TaskCost] = {}
        # task_id → threading.Lock (one lock per task for concurrent access)
        self._locks: dict[TaskId, threading.Lock] = {}
        # Global lock to protect the _locks dict itself (since multiple
        # threads might try to create a per-task lock simultaneously)
        self._global_lock = threading.Lock()

    def _get_lock(self, task_id: TaskId) -> threading.Lock:
        """Get or create a per-task lock.

        Uses the global lock to ensure we don't create two locks for
        the same task_id when two threads race on first access.

        Args:
            task_id: The task to get a lock for.

        Returns:
            A ``threading.Lock`` specific to this task.
        """
        with self._global_lock:
            if task_id not in self._locks:
                self._locks[task_id] = threading.Lock()
            return self._locks[task_id]

    def record_call(self, task_id: TaskId, call: ModelCall) -> None:
        """Append a model call to the task's record.

        Creates a new ``TaskCost`` entry if this is the first call for
        the task.  Both successful and failed calls are recorded —
        failed retries still cost money.

        Args:
            task_id: The task this call belongs to.
            call:    The ``ModelCall`` to record.
        """
        with self._get_lock(task_id):
            # Lazy-create the TaskCost on first call for this task
            if task_id not in self._tasks:
                self._tasks[task_id] = TaskCost(
                    task_id=task_id,
                    tier=call.tier,
                    task_type=call.task_type,
                    total_cost=Decimal("0"),
                )
            task = self._tasks[task_id]
            task.calls.append(call)

    def record_escalation(
        self, task_id: TaskId, event: EscalationEvent
    ) -> None:
        """Append an escalation event to the task's record.

        Creates a new ``TaskCost`` if the task doesn't exist yet (edge
        case: escalation recorded before any call, which shouldn't
        normally happen but is handled defensively).

        Args:
            task_id: The task that was escalated.
            event:   The ``EscalationEvent`` to record.
        """
        with self._get_lock(task_id):
            if task_id not in self._tasks:
                self._tasks[task_id] = TaskCost(
                    task_id=task_id,
                    tier=event.to_tier,
                    task_type=event.task_type,
                    total_cost=Decimal("0"),
                )
            self._tasks[task_id].escalations.append(event)

    def finalize_task(
        self,
        task_id: TaskId,
        task_type: str,
        final_tier: TierName,
    ) -> TaskCost:
        with self._get_lock(task_id):
            task = self._tasks.get(task_id)
            if task is None:
                task = TaskCost(
                    task_id=task_id,
                    tier=final_tier,
                    task_type=task_type,
                    total_cost=Decimal("0"),
                )
                self._tasks[task_id] = task
            task.tier = final_tier
            task.task_type = task_type
            task.total_cost = sum(
                ((c.cost_in + c.cost_out) for c in task.calls),
                Decimal("0"),
            )
            task.finalized = True
            self._locks.pop(task_id, None)
            return task

    def cost_per_task(self, task_id: TaskId) -> Optional[TaskCost]:
        """Return the TaskCost for a task, or None if not found.

        Works for both in-progress tasks (before finalize) and
        finalized tasks.  The returned TaskCost may have total_cost=0
        if finalize hasn't been called yet.

        Args:
            task_id: The task to look up.

        Returns:
            ``TaskCost`` if the task exists, else ``None``.
        """
        return self._tasks.get(task_id)

    def cost_report(self) -> CostReport:
        report = CostReport()
        for task_id, task_cost in self._tasks.items():
            if not task_cost.finalized:
                continue
            report.per_task[task_id] = task_cost
            tier = task_cost.tier
            report.per_tier[tier] = (
                report.per_tier.get(tier, Decimal("0"))
                + task_cost.total_cost
            )
            tt = task_cost.task_type
            report.per_type[tt] = (
                report.per_type.get(tt, Decimal("0"))
                + task_cost.total_cost
            )
        return report

    def reset(self) -> None:
        """Clear all data.  For testing only.

        Removes all task records and locks.  Should not be called in
        production — the ledger is meant to accumulate costs for the
        lifetime of the process.
        """
        self._tasks.clear()
        self._locks.clear()
