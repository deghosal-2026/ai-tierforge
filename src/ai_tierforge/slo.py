"""
Escalation tracker and routing logger for ai-tierforge.

This module contains two components:

1. **EscalationTracker** — tracks escalation events and computes
   escalation rates as an SLO (Service Level Objective).  If the
   escalation rate exceeds the configured threshold, it signals that
   tier routing isn't saving money.

2. **RoutingLogger** — separates routing decisions (intentional tier
   choices) from failover events (forced escalations, budget
   downgrades).  Both are logged as structured JSON lines.

Escalation direction
--------------------
Tiers are ordered by priority (from YAML insertion order)::

    tier_order = ["architect", "workhorse", "utility"]
                   ↑ priority 0    ↑ priority 1    ↑ priority 2

- **Escalation (up)**: move to a *lower* priority number
  (workhorse → architect).  Used when the current tier fails.
- **Downgrade (down)**: move to a *higher* priority number
  (architect → workhorse).  Used by the budget enforcer.
"""

import json
import logging
import sys
import threading

from ai_tierforge.types import (
    EscalationConfig,
    EscalationEvent,
    LoggingConfig,
    RouteDecisionType,
    RouteLogEntry,
    TaskId,
    TierName,
)

# Module-level logger for non-structured log messages (warnings, etc.)
logger = logging.getLogger("ai_tierforge")


class EscalationTracker:
    """Tracks escalation rates and determines when to escalate.

    The escalation rate is the percentage of tasks where a lower tier
    failed and a higher tier had to take over.  High escalation rate
    means tier routing isn't saving money — you're paying for a cheap
    attempt *plus* an expensive call on every escalated task.

    Supports querying by task_type or tier name, with per-tier
    threshold overrides.
    """

    def __init__(
        self,
        config: EscalationConfig,
        tier_order: list[TierName],
    ) -> None:
        self._config = config
        self._tier_order = tier_order
        self._events: list[EscalationEvent] = []
        self._total_tasks_by_type: dict[str, set[TaskId]] = {}
        self._total_tasks_by_tier: dict[str, set[TaskId]] = {}
        self._lock = threading.Lock()

    def record_task(self, task_id: TaskId, task_type: str, tier: TierName) -> None:
        with self._lock:
            self._total_tasks_by_type.setdefault(task_type, set()).add(task_id)
            self._total_tasks_by_tier.setdefault(tier, set()).add(task_id)

    def record(self, event: EscalationEvent) -> None:
        """Record an escalation event.

        Called by the router each time it escalates a task from one
        tier to a higher-priority tier.

        Args:
            event: The ``EscalationEvent`` to record.
        """
        with self._lock:
            self._events.append(event)

    def escalation_rate(self, key: str) -> float:
        with self._lock:
            total = self._total_tasks_by_type.get(key)
            if total is None:
                total = self._total_tasks_by_tier.get(key)
            if not total:
                return 0.0
            total_count = len(total)
            escalated_ids = {
                e.task_id for e in self._events
                if e.task_type == key or e.from_tier == key
            }
            return len(escalated_ids) / total_count

    def threshold_breached(self, key: str) -> bool:
        """Check if escalation rate exceeds the configured threshold.

        Uses per-tier threshold override if the key matches a tier
        name, otherwise uses the default threshold.

        Args:
            key: A task_type or tier name to check.

        Returns:
            True if the escalation rate exceeds the threshold,
            False otherwise.
        """
        rate = self.escalation_rate(key)
        # Use per-tier override if available, else default
        threshold = self._config.per_tier.get(
            key, self._config.default_threshold
        )
        return rate > threshold

    def next_tier(self, current_tier: TierName) -> TierName:
        """Return the next higher-priority tier (lower priority number).

        Given tier_order = ["architect", "workhorse", "utility"]:
        - workhorse → architect (escalate up)
        - utility → workhorse (escalate up)
        - architect → architect (already at highest, can't escalate)

        Args:
            current_tier: The tier to escalate from.

        Returns:
            The next higher-priority tier, or the same tier if already
            at the highest.

        Raises:
            ValueError: If current_tier is not in tier_order.
        """
        if current_tier not in self._tier_order:
            raise ValueError(f"unknown tier '{current_tier}'")
        idx = self._tier_order.index(current_tier)
        # idx 0 = highest priority (architect) — can't go higher
        if idx == 0:
            return current_tier
        # Move to the previous entry (lower index = higher priority)
        return self._tier_order[idx - 1]

    def trace(self, task_id: str) -> list[EscalationEvent]:
        """Return all escalation events for a given task.

        Used by ``RouterExhaustedError`` to show the full escalation
        path in the error message — useful for debugging why a task
        kept failing.

        Args:
            task_id: The task to trace.

        Returns:
            List of ``EscalationEvent`` objects for this task,
            in the order they occurred.
        """
        with self._lock:
            return [e for e in self._events if e.task_id == task_id]


class RoutingLogger:
    """Separates routing decisions from failover events.

    This is a key feature of ai-tierforge: routing decisions ("routed
    to DeepSeek for cost") are logged separately from failover events
    ("fell back to GLM because DeepSeek timed out") so users can
    distinguish cost decisions from availability events in their
    log pipeline.

    Both types of entries are written as structured JSON lines to
    stdout (or a file), with in-memory buffers for recent-access
    queries.
    """

    def __init__(self, config: LoggingConfig) -> None:
        """Initialise the logger with output configuration.

        Args:
            config: Logging config — controls which event types are
                    logged, the log level, and the output destination.
        """
        self._config = config
        # In-memory buffers for recent-access queries
        self._routes: list[RouteLogEntry] = []
        self._failovers: list[RouteLogEntry] = []
        self._lock = threading.Lock()

    def log_route(self, entry: RouteLogEntry) -> None:
        entry.decision = RouteDecisionType.ROUTE
        with self._lock:
            self._routes.append(entry)
        if self._config.routing:
            self._emit(entry)

    def log_failover(self, entry: RouteLogEntry) -> None:
        entry.decision = RouteDecisionType.FAILOVER
        with self._lock:
            self._failovers.append(entry)
        if self._config.failover:
            self._emit(entry)

    def recent_routes(self, n: int = 10) -> list[RouteLogEntry]:
        """Return the most recent n route entries.

        Args:
            n: Number of entries to return (default 10).

        Returns:
            List of up to n ``RouteLogEntry`` objects, most recent last.
        """
        with self._lock:
            return self._routes[-n:]

    def recent_failovers(self, n: int = 10) -> list[RouteLogEntry]:
        """Return the most recent n failover entries.

        Args:
            n: Number of entries to return (default 10).

        Returns:
            List of up to n ``RouteLogEntry`` objects, most recent last.
        """
        with self._lock:
            return self._failovers[-n:]

    def summary(self) -> dict:
        """Return summary statistics for routes and failovers.

        Returns:
            Dict with keys:
            - ``total_routes``: count of route entries
            - ``total_failovers``: count of failover entries
            - ``failover_rate``: failovers / (routes + failovers)
        """
        with self._lock:
            total_routes = len(self._routes)
            total_failovers = len(self._failovers)
            total = total_routes + total_failovers
            return {
                "total_routes": total_routes,
                "total_failovers": total_failovers,
                "failover_rate": (
                    total_failovers / total if total > 0 else 0.0
                ),
            }

    def _emit(self, entry: RouteLogEntry) -> None:
        """Write a structured JSON log line to the configured output.

        At ``info`` level: timestamp, event type, task_id, tier, model,
        decision, reason.
        At ``debug`` level: same fields plus prompt, response, tokens.

        Output goes to stdout (default) or a file path if configured.

        Args:
            entry: The ``RouteLogEntry`` to emit.
        """
        log_line: dict[str, object] = {
            "timestamp": entry.timestamp,
            "level": "debug" if self._config.level == "debug" else "info",
            "event": entry.decision.value,
            "task_id": entry.task_id,
            "tier": entry.tier,
            "model": entry.model,
            "decision": entry.decision.value,
            "reason": entry.reason,
            "prompt": entry.prompt,
            "response": entry.response,
            "tokens_in": entry.tokens_in,
            "tokens_out": entry.tokens_out,
        }
        # Write to stdout or append to a file
        output = self._config.output
        if output == "stdout":
            sys.stdout.write(json.dumps(log_line) + "\n")
            sys.stdout.flush()
        else:
            with open(output, "a") as f:
                f.write(json.dumps(log_line) + "\n")
