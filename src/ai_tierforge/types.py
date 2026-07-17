"""
Shared data structures for ai-tierforge.

This module defines all the dataclasses, enums, and type aliases that
flow through the system.  Every other module imports from here, so it
contains zero logic — only data definitions.

Design notes
------------
* All monetary values use ``decimal.Decimal`` (never ``float``) to avoid
  binary-representation rounding errors in cost calculations.
* Dataclasses use plain ``@dataclass`` (not ``slots=True``) for v1
  simplicity; ``slots=True`` can be added later without breaking the API.
* Enums derive from ``Enum`` / ``auto()`` so their values are stable
  across pickle / unpickle and JSON serialisation.
"""

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional
import time

# ─── Identifiers ───────────────────────────────────────────────────────
# Simple string aliases — kept as type aliases (not NewType) so they
# are interchangeable with plain ``str`` in JSON / YAML contexts.

TaskId = str         # uuid4 hex, e.g. "a1b2c3d4e5f6..."
ScopeId = str        # budget scope: "project:name", "user:email", "team:payments"
TierName = str       # any string; tier order is defined by YAML position


# ─── Tier Configuration ────────────────────────────────────────────────
# These dataclasses represent the *static* configuration loaded from
# tiers.yaml.  They are created once at startup and never mutated.

@dataclass
class TierConfig:
    """Configuration for a single tier.

    A tier is a named group of LLM models with a shared purpose.
    Typical tiers: ``architect`` (expensive, high-reasoning),
    ``workhorse`` (cheap, fast), ``utility`` (local, free).

    Attributes:
        model:       Model identifier passed to the provider adapter,
                     e.g. ``"glm-5.2"`` or ``"omlx:qwen2.5-coder:7b"``.
        max_tokens:  Maximum output tokens for calls to this tier.
        use_for:     List of task-type labels this tier handles
                     (e.g. ``["code", "tests"]``).  The router matches
                     the first tier whose ``use_for`` contains the
                     requested task_type.
        provider:    Adapter name — must match a key in the
                     ``adapters`` dict passed to ``TierRouter``.
                     Built-in: ``"openai-compatible"`` | ``"omlx"``.
        endpoint:    Optional override for the provider's default URL.
                     Useful for pointing at a gateway (LiteLLM, Portkey)
                     or Zen.  Used by the default adapter factory.
        api_key_env: Optional override for the env var name holding the
                     API key.  Defaults to ``"OPENAI_API_KEY"`` for
                     ``openai-compatible``.  Used by the default adapter
                     factory so CLI users can specify ``OPENCODE_API_KEY``.
        priority:    Escalation priority (0 = highest = architect).
                     Auto-assigned from YAML position if not explicitly
                     set; can be overridden with the ``priority:`` field.
    """
    model: str
    max_tokens: int
    use_for: list[str]
    provider: str
    endpoint: Optional[str] = None
    api_key_env: Optional[str] = None
    priority: int = 0


@dataclass
class EscalationConfig:
    """Escalation SLO (Service Level Objective) configuration.

    The escalation rate is the percentage of tasks where a lower tier
    failed and a higher tier had to take over.  If it exceeds the
    threshold, routing isn't saving money.

    Attributes:
        default_threshold:  Alert when escalation rate exceeds this
                            fraction (0.0–1.0).  Default 30%.
        per_tier:           Per-tier threshold overrides, keyed by
                            tier name.  E.g. ``{"utility": 0.50}``
                            allows the utility tier a higher tolerance.
        max_retries:        Retries within a single tier before
                            escalating to the next higher tier.
    """
    default_threshold: float = 0.30
    per_tier: dict[str, float] = field(default_factory=dict)
    max_retries: int = 3


@dataclass
class RouterConfig:
    """Router-level retry configuration.

    Attributes:
        max_retries: Total attempts across *all* tiers before
                     ``RouterExhaustedError`` is raised.  This is the
                     outer loop limit; per-tier retries are governed
                     by ``EscalationConfig.max_retries``.
    """
    max_retries: int = 3


class OnExceedAction(Enum):
    """Action to take when a budget limit is exceeded.

    The enforcer evaluates all active budgets and returns the *most
    restrictive* action found (HARD_STOP > DOWNGRADE > WARN).

    Values:
        WARN:      Log a warning but allow the call to proceed.
        DOWNGRADE: Drop to a cheaper tier automatically
                   (architect → workhorse → utility).
        HARD_STOP: Reject the call entirely; raises
                   ``BudgetExceededError``.
    """
    WARN = "warn"
    DOWNGRADE = "downgrade"
    HARD_STOP = "hard_stop"


@dataclass
class BudgetConfig:
    """Single budget scope configuration.

    Attributes:
        limit:      Maximum spend in USD (as Decimal for precision).
        on_exceed:  Action when the limit is breached.
    """
    limit: Decimal
    on_exceed: OnExceedAction


@dataclass
class BudgetsConfig:
    """All budget scope configurations.

    Each scope is independently optional — users can enforce budgets
    at whichever granularity they need.

    Attributes:
        per_task:    Budget per individual task (per task_id).
        per_day:     Budget per day per scope (reset via CLI or scheduler).
        per_project: Budget per project (cumulative across days).
    """
    per_task: Optional[BudgetConfig] = None
    per_day: Optional[BudgetConfig] = None
    per_project: Optional[BudgetConfig] = None


@dataclass
class LoggingConfig:
    """Logging configuration for the routing logger.

    Attributes:
        routing:   If True, log intentional routing decisions.
        failover:  If True, log forced failover events (escalation, budget).
        level:     ``"info"`` (default) or ``"debug"``.  Debug level
                   adds prompt/response/token fields to log entries.
        output:    ``"stdout"`` (default) or a file path for log lines.
    """
    routing: bool = True
    failover: bool = True
    level: str = "info"
    output: str = "stdout"


@dataclass
class TierForgeConfig:
    """Top-level configuration combining all sub-configs.

    This is the root object returned by ``TierForgeConfigLoader``.
    The ``tiers`` dict preserves YAML insertion order (Python 3.7+
    dicts are ordered), which determines escalation priority:
    first tier = highest priority (architect), last = lowest (utility).

    Attributes:
        tiers:      Ordered dict of tier name → TierConfig.
        escalation: Escalation SLO thresholds and retry limits.
        router:     Router-level total retry limit.
        budgets:    Per-scope budget enforcement rules.
        logging:    Routing/failover log output configuration.
        pricing:    Optional dict of model → (cost_in, cost_out) per-token
                    pricing.  Merged with the adapter's default pricing
                    so users only need to specify custom models.
    """
    tiers: dict[str, TierConfig]
    escalation: EscalationConfig = field(default_factory=EscalationConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    budgets: BudgetsConfig = field(default_factory=BudgetsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)
    pricing: dict[str, tuple[Decimal, Decimal]] = field(default_factory=dict)


# ─── Runtime Data ──────────────────────────────────────────────────────
# These dataclasses represent *runtime* objects — created during call
# processing, not at config-load time.  They flow through the cost
# ledger, escalation tracker, and routing logger.

@dataclass
class ModelCall:
    """Record of a single model invocation.

    Created by a provider adapter for every call (success or failure).
    The router enriches it with ``task_id``, ``task_type``, ``tier``,
    and ``attempt`` before recording it in the cost ledger.

    Attributes:
        task_id:      Unique task identifier (uuid4 hex).
        task_type:    Task type label (e.g. "code", "spec").
        tier:         Name of the tier that handled this call.
        model:        Model name passed to the provider.
        prompt:       The original prompt text.
        response:     Model response text (None if call failed).
        tokens_in:    Input token count from provider usage data.
        tokens_out:   Output token count from provider usage data.
        cost_in:      Cost of input tokens (Decimal).
        cost_out:     Cost of output tokens (Decimal).
        duration_ms:  Wall-clock call duration in milliseconds.
        success:      True if the call completed successfully.
        error:        Error string if ``success`` is False, else None.
        attempt:      Which retry attempt this is (0 = first call).
    """
    task_id: TaskId
    task_type: str
    tier: TierName
    model: str
    prompt: str
    response: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    cost_in: Decimal = Decimal("0")
    cost_out: Decimal = Decimal("0")
    duration_ms: int = 0
    success: bool = True
    error: Optional[str] = None
    attempt: int = 0


class EscalationCause(Enum):
    """Reason for an escalation event.

    Used by ``EscalationEvent.cause`` to categorise why the router
    moved a task from one tier to a higher-priority tier.

    Values:
        RETRY_EXCEEDED:   Per-tier retry limit hit; model kept failing.
        CONTENT_TOO_LONG: Response exceeded max_tokens.
        TIMEOUT:          Provider timed out (immediate escalate).
        BUDGET_DOWNGRADE: Budget enforcer triggered a tier downgrade.
        PROVIDER_ERROR:   5xx, rate limit, or auth failure.
    """
    RETRY_EXCEEDED = auto()
    CONTENT_TOO_LONG = auto()
    TIMEOUT = auto()
    BUDGET_DOWNGRADE = auto()
    PROVIDER_ERROR = auto()


@dataclass
class EscalationEvent:
    """Record of a single escalation from one tier to another.

    Created by the router when it decides to escalate.  Stored in the
    cost ledger (for per-task history) and the escalation tracker
    (for SLO rate calculations).

    Attributes:
        task_id:                 The task that was escalated.
        task_type:               Task type label.
        from_tier:               Tier that failed and triggered escalation.
        to_tier:                 Higher-priority tier that takes over.
        cause:                   Why the escalation happened.
        failure_count:           Number of failed attempts before escalation.
        cost_before_escalation:  Total cost of failed calls in the
                                 lower tier before escalating.  For
                                 reporting/debugging only — not added
                                 separately to total cost.
    """
    task_id: TaskId
    task_type: str
    from_tier: TierName
    to_tier: TierName
    cause: EscalationCause
    failure_count: int = 0
    cost_before_escalation: Decimal = Decimal("0")


class RouteDecisionType(Enum):
    """Whether a routing decision was intentional or forced.

    This distinction is a key feature of ai-tierforge: routing decisions
    ("routed to DeepSeek for cost") are logged separately from failover
    events ("fell back to GLM because DeepSeek timed out") so users can
    distinguish cost decisions from availability events.

    Values:
        ROUTE:     Intentional choice based on task_type matching.
        FAILOVER:  Forced by error, escalation, or budget enforcement.
    """
    ROUTE = "route"
    FAILOVER = "failover"


@dataclass
class RouteLogEntry:
    task_id: TaskId
    tier: TierName
    model: str
    decision: RouteDecisionType
    reason: str
    prompt: Optional[str] = None
    response: Optional[str] = None
    tokens_in: int = 0
    tokens_out: int = 0
    timestamp: float = field(default_factory=time.time)


@dataclass
class TaskCost:
    task_id: TaskId
    tier: TierName
    task_type: str
    total_cost: Decimal
    finalized: bool = False
    calls: list[ModelCall] = field(default_factory=list)
    escalations: list[EscalationEvent] = field(default_factory=list)


@dataclass
class CostReport:
    """Aggregated cost report across all tasks.

    Built by ``CostLedger.cost_report()`` from all finalised tasks.
    Supports querying by task type and computing escalation rates.

    Attributes:
        per_task:  Dict of task_id → TaskCost.
        per_tier:  Dict of tier name → total cost across all tasks
                   that ended on that tier.
        per_type:  Dict of task_type → total cost across all tasks
                   of that type.
    """
    per_task: dict[TaskId, TaskCost] = field(default_factory=dict)
    per_tier: dict[TierName, Decimal] = field(default_factory=dict)
    per_type: dict[str, Decimal] = field(default_factory=dict)

    def cost_per_type(self, task_type: str) -> Decimal:
        return sum(
            (tc.total_cost for tc in self.per_task.values()
             if tc.task_type == task_type),
            Decimal("0"),
        )

    def escalation_rate(self, task_type: str) -> float:
        """Fraction of tasks of this type that had at least one escalation.

        High escalation rate means tier routing isn't saving money —
        you're paying for a cheap attempt *plus* an expensive call on
        every escalated task.

        Returns:
            Float 0.0–1.0, or 0.0 if no tasks of this type exist.
        """
        tasks = [t for t in self.per_task.values() if t.task_type == task_type]
        if not tasks:
            return 0.0
        escalated = sum(1 for t in tasks if t.escalations)
        return escalated / len(tasks)


# ─── Budget Check Result ───────────────────────────────────────────────

@dataclass
class BudgetCheck:
    """Result of a budget enforcement check.

    Returned by ``BudgetEnforcer.check(scope)``.  The router uses
    ``action`` to decide whether to proceed, downgrade, or stop.

    Attributes:
        allowed:   True if the call may proceed without action.
        action:    Most restrictive action found across all budgets.
        reason:    Human-readable explanation of the action.
        new_tier:  Populated only when ``action == DOWNGRADE``;
                   the tier the router should switch to.
    """
    allowed: bool
    action: OnExceedAction
    reason: str
    new_tier: Optional[TierName] = None
