# SPEC: ai-tierforge — Technical Specification

| Field | Value |
|---|---|
| **Status** | Approved |
| **PRD** | [docs/PRD.md](./PRD.md) |
| **Date** | 2026-07-15 |
| **Target Ship** | Friday 2026-07-18 |

---

## 1. Package Structure

```
ai-tierforge/
├── pyproject.toml
├── README.md
├── LICENSE
├── .gitignore
├── docs/
│   ├── PRD.md
│   └── SPEC.md                    ← this file
├── src/
│   └── ai_tierforge/
│       ├── __init__.py            # Public API exports
│       ├── router.py              # M1: TierRouter
│       ├── config.py              # YAML config loader + schema validation
│       ├── cost.py                # M2: CostLedger
│       ├── slo.py                 # M3: EscalationTracker, RoutingLogger
│       ├── omlx.py                # M4: OMLXAdapter
│       ├── budget.py              # M5: BudgetEnforcer
│       ├── cli.py                 # M6: CLI (argparse)
│       ├── exceptions.py          # All custom exceptions
│       ├── adapters/
│       │   ├── __init__.py
│       │   ├── base.py            # ProviderAdapter protocol
│       │   └── openai_compat.py   # OpenAI-compatible adapter
│       └── types.py               # Shared data structures
├── tests/
│   ├── __init__.py
│   ├── conftest.py                # Fixtures: mock providers, sample configs
│   ├── test_types.py
│   ├── test_config.py
│   ├── test_router.py
│   ├── test_cost.py
│   ├── test_slo.py
│   ├── test_budget.py
│   ├── test_omlx.py
│   ├── test_cli.py
│   ├── test_exceptions.py
│   ├── fixtures/
│   │   ├── tiers.yaml             # Sample valid config
│   │   ├── tiers-invalid.yaml     # Broken config for validation tests
│   │   ├── tiers-custom-tiers.yaml # Non-standard tier names
│   │   └── mock_responses.py      # Mock provider response data
│   └── integration/
│       └── test_router_integration.py
```

---

## 2. System Architecture

### 2.1 System Context

```
┌─────────────┐     ┌───────────────────────────────────┐     ┌──────────────┐
│   Agent /   │────▶│         ai-tierforge               │────▶│  Provider    │
│   App       │     │  TierRouter → CostLedger → Budget  │     │  (OpenAI,    │
│             │     │  EscalationTracker → RoutingLogger │     │  DeepSeek,   │
│             │     │  OMLXAdapter                       │     │  vLLM, OMLX) │
└─────────────┘     └───────────────────────────────────┘     └──────────────┘
                            │
                            ▼
                     ┌──────────────┐
                     │  YAML Config │
                     │  (tiers.yaml)│
                     └──────────────┘
```

### 2.2 Component Architecture

| Component | Responsibility | Key Interfaces |
|---|---|---|
| **TierRouter** | Reads YAML config, routes calls to the right tier based on task type | `route(task_type, prompt, **kwargs) → ModelCall` |
| **CostLedger** | Tracks cost per completed task (retry + escalation aggregation) | `record_call(task_id, call)`, `cost_per_task(task_id) → TaskCost` |
| **BudgetEnforcer** | Per-scope budget with auto-downgrade | `check(scope) → BudgetCheck`, `record_spend(scope, amount)` |
| **EscalationTracker** | Tracks escalation rate, alerts on threshold breach | `record(event)`, `escalation_rate(task_type) → float` |
| **RoutingLogger** | Separates routing decisions from failover events | `log_route(entry)`, `log_failover(entry)` |
| **OMLXAdapter** | First-class support for local models via OMLX | `call(model, prompt, **kwargs) → ModelCall` |

### 2.3 Data Flow

```
1. Agent calls router.route(task_type="code", prompt="...")
2. TierRouter reads config → matches task_type to tier → workhorse (DeepSeek)
3. BudgetEnforcer.check(scope) → allowed
4. Router dispatches to provider adapter → DeepSeek call
5. On success: CostLedger.record_call() + BudgetEnforcer.record_spend()
6. On failure/retry: CostLedger records retry cost, router may retry or escalate
7. On escalation: EscalationTracker.record() + CostLedger records escalation cost
8. RoutingLogger writes structured JSON log entry (route vs failover tagged)
9. CostLedger.finalize_task() → Agent receives response + completed task cost
```

### 2.4 Integration Scenarios

#### Plain Python (no framework)

```python
from ai_tierforge import TierRouter

router = TierRouter.from_yaml("tiers.yaml")
response = router.route("code", "Write a unit test for auth")
print(router.cost_report().cost_per_task("code"))
```

ai-tierforge is a library — call `router.route()` wherever you currently call your LLM provider. No wrapper, no server, no daemon.

#### LangGraph

```python
from langgraph.graph import StateGraph
from ai_tierforge import TierRouter

router = TierRouter.from_yaml("tiers.yaml")

def generate_node(state):
    response = router.route("code", state["prompt"], scope="agent:coder")
    return {"output": response.response, "cost": router.cost_report()}

graph = StateGraph(State)
graph.add_node("generate", generate_node)
```

ai-tierforge replaces the direct `llm.invoke()` call inside each node. The cost report is available per-node or globally via `router.cost_report()`.

#### CrewAI / multi-agent

```python
from ai_tierforge import TierRouter

router = TierRouter.from_yaml("tiers.yaml")

class TieredLLM:
    """Drop-in replacement for CrewAI's LLM class."""
    def call(self, task_type, prompt, **kwargs):
        return router.route(task_type, prompt, **kwargs).response
```

Each agent specifies its `task_type` ("research", "code", "review") and ai-tierforge routes to the matching tier. Budget enforcement works across all agents via `scope="crew:research-team"`.

#### Existing gateway (LiteLLM / Portkey)

ai-tierforge sits **above** your gateway, not in place of it:

```
Agent → ai-tierforge (tier decision + cost tracking) → LiteLLM (provider call) → OpenAI
```

The `OpenAICompatAdapter` can be configured to point at your gateway's endpoint instead of directly at OpenAI. ai-tierforge decides the tier; your gateway handles credentials, caching, rate limits.

---

## 3. Data Structures (`types.py`)

```python
from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum, auto
from typing import Optional
import uuid
import time

# ─── Identifiers ───

TaskId = str         # uuid4 hex
ScopeId = str        # "project:name" or "user:email" or "team:payments"
TierName = str       # any string — tier order defined by YAML position (see §5)

# ─── Tier Configuration ───

@dataclass
class TierConfig:
    model: str                     # "glm-5.2", "deepseek-v4-flash", "omlx:qwen2.5-coder:7b"
    max_tokens: int
    use_for: list[str]             # ["spec", "code", "tickets", ...]
    provider: str                  # "openai-compatible" | "omlx" | custom adapter name
    endpoint: Optional[str] = None # override default endpoint
    priority: int = 0              # escalation order: 0 = highest (architect), 2 = lowest (utility)
                                   # set automatically by YAML position if not specified

@dataclass
class EscalationConfig:
    default_threshold: float = 0.30    # 30%
    per_tier: dict[str, float] = field(default_factory=dict)
    max_retries: int = 3               # retries per tier before escalating

@dataclass
class RouterConfig:
    max_retries: int = 3               # max total retries across all tiers before RouterExhaustedError

class OnExceedAction(Enum):
    WARN = "warn"
    DOWNGRADE = "downgrade"
    HARD_STOP = "hard_stop"

@dataclass
class BudgetConfig:
    limit: Decimal            # e.g. Decimal("0.10") for $0.10 per task
    on_exceed: OnExceedAction

@dataclass
class BudgetsConfig:
    per_task: Optional[BudgetConfig] = None
    per_day: Optional[BudgetConfig] = None
    per_project: Optional[BudgetConfig] = None

@dataclass
class LoggingConfig:
    routing: bool = True
    failover: bool = True
    level: str = "info"       # "info" | "debug"
    output: str = "stdout"    # "stdout" | file path

@dataclass
class TierForgeConfig:
    tiers: dict[str, TierConfig]      # ordered dict — insertion order = escalation priority
    escalation: EscalationConfig = field(default_factory=EscalationConfig)
    router: RouterConfig = field(default_factory=RouterConfig)
    budgets: BudgetsConfig = field(default_factory=BudgetsConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)

# ─── Runtime Data ───

@dataclass
class ModelCall:
    task_id: TaskId
    task_type: str                 # propagated from route() call
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
    attempt: int = 0               # which retry attempt (0 = first call)

class EscalationCause(Enum):
    RETRY_EXCEEDED = auto()        # model kept failing, max_retries hit
    CONTENT_TOO_LONG = auto()      # response exceeded max_tokens
    TIMEOUT = auto()               # provider timed out
    BUDGET_DOWNGRADE = auto()      # enforcer triggered downgrade
    PROVIDER_ERROR = auto()        # 5xx, rate limit, auth failure

@dataclass
class EscalationEvent:
    task_id: TaskId
    task_type: str
    from_tier: TierName
    to_tier: TierName
    cause: EscalationCause
    failure_count: int = 0
    cost_before_escalation: Decimal = Decimal("0")  # sum of failed calls before escalation

class RouteDecisionType(Enum):
    ROUTE = "route"                # intentional choice
    FAILOVER = "failover"          # forced by error / budget / escalation

@dataclass
class RouteLogEntry:
    task_id: TaskId
    tier: TierName
    model: str
    decision: RouteDecisionType
    reason: str                    # "matched task_type 'code'" or "DeepSeek timed out"
    timestamp: float = field(default_factory=time.time)

@dataclass
class TaskCost:
    task_id: TaskId
    tier: TierName                 # final tier that resolved the task
    task_type: str
    total_cost: Decimal            # calls + retries + escalations
    calls: list[ModelCall] = field(default_factory=list)
    escalations: list[EscalationEvent] = field(default_factory=list)

@dataclass
class CostReport:
    per_task: dict[TaskId, TaskCost] = field(default_factory=dict)
    per_tier: dict[TierName, Decimal] = field(default_factory=dict)
    per_type: dict[str, Decimal] = field(default_factory=dict)

    def cost_per_task(self, task_type: str) -> Decimal:
        """Sum total_cost for all tasks of this task_type."""
        return sum(
            tc.total_cost for tc in self.per_task.values()
            if tc.task_type == task_type
        )

    def escalation_rate(self, task_type: str) -> float:
        """Fraction of tasks of this type that had at least one escalation."""
        tasks = [t for t in self.per_task.values() if t.task_type == task_type]
        if not tasks:
            return 0.0
        escalated = sum(1 for t in tasks if t.escalations)
        return escalated / len(tasks)

# ─── Budget Check Result ───

@dataclass
class BudgetCheck:
    allowed: bool
    action: OnExceedAction         # WARN | DOWNGRADE | HARD_STOP
    reason: str
    new_tier: Optional[TierName] = None  # populated only if action == DOWNGRADE
```

---

## 4. Component Interfaces

### 4.1 Config Loader (`config.py`)

```python
class TierForgeConfigLoader:
    @staticmethod
    def from_yaml(path: str | Path) -> TierForgeConfig
    @staticmethod
    def from_dict(data: dict) -> TierForgeConfig
    @staticmethod
    def validate(config: TierForgeConfig) -> list[str]  # returns error messages
```

**Validation rules (returns list of error strings, empty = valid):**

| Rule | Error Message |
|---|---|
| `tiers` dict is empty | `"config must define at least one tier"` |
| `tiers` has only 1 tier | `"config should define at least 2 tiers for escalation to work"` (warning, not error) |
| `use_for` list is empty | `"tier '{name}': use_for must not be empty"` |
| `provider` is not a known adapter name | `"tier '{name}': unknown provider '{provider}'"` |
| `max_tokens` <= 0 | `"tier '{name}': max_tokens must be positive"` |
| `on_exceed` is not a valid `OnExceedAction` value | `"budget: on_exceed must be 'warn', 'downgrade', or 'hard_stop'"` |
| `limit` is negative | `"budget: limit must be non-negative"` |
| `default_threshold` not in [0, 1] | `"escalation: default_threshold must be between 0.0 and 1.0"` |
| Task type appears in multiple tiers' `use_for` | `"task_type '{type}' is claimed by tiers '{a}' and '{b}' — first match wins"` (warning) |
| `max_retries` < 1 | `"router: max_retries must be >= 1"` |

**Tier priority assignment:**

Tiers are assigned escalation priority based on YAML insertion order. The first tier defined is priority 0 (highest = architect), the last is lowest (utility). If `priority:` field is explicitly set in YAML, it overrides the positional default.

**`from_dict` parsing:**

- Converts raw dict from YAML → `TierForgeConfig` dataclasses
- Parses `on_exceed` string → `OnExceedAction` enum
- Parses `limit` string/float → `Decimal`
- Assigns `priority` to each `TierConfig` based on dict order (Python 3.7+ dicts preserve insertion order; PyYAML preserves YAML mapping order)

### 4.2 Tier Router (`router.py`)

```python
class TierRouter:
    def __init__(
        self,
        config: TierForgeConfig,
        adapters: dict[str, ProviderAdapter],
    ) -> None

    @classmethod
    def from_yaml(
        cls,
        config_path: str | Path,
        adapters: dict[str, ProviderAdapter] | None = None,
    ) -> TierRouter
    # If adapters is None, defaults to:
    #   {"openai-compatible": OpenAICompatAdapter(), "omlx": OMLXAdapter()}

    def route(
        self,
        task_type: str,
        prompt: str,
        task_id: TaskId | None = None,
        scope: ScopeId | None = None,
        **kwargs,
    ) -> ModelCall

    def cost_report(self) -> CostReport

    def tier_for_task(self, task_type: str) -> tuple[TierName, TierConfig]
    # First tier whose use_for contains task_type wins.
    # Raises NoTierMatchError if no tier matches.
```

**`tier_for_task` matching logic:**

- Iterate tiers in dict insertion order (YAML order)
- Return the first tier whose `use_for` list contains `task_type`
- If multiple tiers claim the same task_type, first one wins (warning emitted at config validation time)
- Raise `NoTierMatchError(task_type)` if no match

**`should_escalate(error)` logic:**

```python
def should_escalate(error: str | None) -> bool:
    """Determine if an error warrants immediate escalation vs retry."""
    if error is None:
        return False
    # These errors are non-retryable — escalate immediately
    immediate_escalate = [
        "content_too_long",      # max_tokens exceeded
        "context_length_exceeded",
        "rate_limit_exceeded",   # won't resolve on retry anytime soon
    ]
    # These errors are retryable — try again before escalating
    retryable = [
        "timeout",
        "connection_error",
        "5xx",
        "internal_error",
    ]
    lower = error.lower()
    if any(s in lower for s in immediate_escalate):
        return True
    return False  # retryable errors: retry first, escalate after max_retries
```

**Routing logic (`route`):**

```
1. task_id = task_id or uuid4().hex
2. tier_name, tier_config = self.tier_for_task(task_type)
3. routing_logger.log_route(RouteLogEntry(
       task_id, tier_name, tier_config.model,
       RouteDecisionType.ROUTE, f"matched task_type '{task_type}'"
   ))

4. current_tier = tier_name
   current_config = tier_config
   tier_retries = 0  # retries within current tier
   total_attempts = 0

5. while total_attempts < config.router.max_retries:
   a. # Budget check
      scope_key = scope or task_id
      budget_check = budget_enforcer.check(scope_key)
      if budget_check.action == OnExceedAction.HARD_STOP:
          raise BudgetExceededError(scope_key, budget_check.reason)
      if budget_check.action == OnExceedAction.DOWNGRADE:
          new_tier = budget_check.new_tier
          routing_logger.log_failover(RouteLogEntry(
              task_id, new_tier, config.tiers[new_tier].model,
              f"budget: {budget_check.reason}"
          ))
          current_tier = new_tier
          current_config = config.tiers[new_tier]

   b. # Dispatch call
      adapter = adapters[current_config.provider]
      call = adapter.call(
          model=current_config.model,
          prompt=prompt,
          max_tokens=current_config.max_tokens,
          **kwargs,
      )
      call.task_id = task_id
      call.task_type = task_type
      call.tier = current_tier
      call.attempt = total_attempts

   c. # Record cost
      cost_ledger.record_call(task_id, call)
      budget_enforcer.record_spend(scope_key, call.cost_in + call.cost_out)

   d. # Success?
      if call.success:
          task_cost = cost_ledger.finalize_task(task_id, task_type, current_tier)
          return call

   e. # Failure — escalate or retry?
      total_attempts += 1
      tier_retries += 1

      if should_escalate(call.error) or tier_retries >= config.escalation.max_retries:
          # Escalate to next higher tier
          next_tier = escalation_tracker.next_tier(current_tier, config.tiers)
          if next_tier == current_tier:
              # Already at highest tier — can't escalate further
              break

          event = EscalationEvent(
              task_id=task_id,
              task_type=task_type,
              from_tier=current_tier,
              to_tier=next_tier,
              cause=EscalationCause.RETRY_EXCEEDED if tier_retries >= max_retries
                    else EscalationCause.CONTENT_TOO_LONG,
              failure_count=tier_retries,
              cost_before_escalation=cost_ledger.cost_per_task(task_id).total_cost
                  if cost_ledger.cost_per_task(task_id) else Decimal("0"),
          )
          escalation_tracker.record(event)
          cost_ledger.record_escalation(task_id, event)

          routing_logger.log_failover(RouteLogEntry(
              task_id, next_tier, config.tiers[next_tier].model,
              f"escalation: {call.error}"
          ))

          current_tier = next_tier
          current_config = config.tiers[next_tier]
          tier_retries = 0  # reset retry counter for new tier

6. raise RouterExhaustedError(task_id, escalation_tracker.trace(task_id))
```

### 4.3 Provider Adapter Protocol (`adapters/base.py`)

```python
from typing import Protocol, runtime_checkable
from decimal import Decimal

@runtime_checkable
class ProviderAdapter(Protocol):
    @property
    def name(self) -> str: ...

    def call(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        **kwargs,
    ) -> ModelCall: ...

    def calculate_cost(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> tuple[Decimal, Decimal]: ...
        # returns (cost_in, cost_out)

    def check_available(self) -> bool: ...
        # Health check — returns True if provider is reachable.
        # For cloud providers: always True (assume available).
        # For OMLX: GET /api/tags, return False if connection refused.
```

**OpenAI-Compatible Adapter (`adapters/openai_compat.py`):**

```python
class OpenAICompatAdapter:
    def __init__(
        self,
        endpoint: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        pricing: dict[str, tuple[Decimal, Decimal]] | None = None,
        timeout: int = 30,
    ) -> None
    # If pricing is None, uses DEFAULT_PRICING (see §10.1)
    # api_key_env: name of env var to read the API key from
    # endpoint: base URL — can point at OpenAI, DeepSeek, vLLM, or a gateway

    @property
    def name(self) -> str: return "openai-compatible"

    def call(self, model, prompt, max_tokens, **kwargs) -> ModelCall
        # POST {endpoint}/chat/completions
        # Headers: Authorization: Bearer {os.environ[api_key_env]}
        # Body: {"model": model, "messages": [{"role":"user","content":prompt}],
        #         "max_tokens": max_tokens, **kwargs}
        # Retry: 3 attempts, exponential backoff (1s, 2s, 4s) on 5xx/timeout/rate_limit
        # Returns ModelCall with tokens_in, tokens_out, cost_in, cost_out, success, error

    def calculate_cost(self, model, tokens_in, tokens_out) -> tuple[Decimal, Decimal]
        # Looks up model in pricing table.
        # If model not in table: raises KeyError(f"no pricing for model '{model}'")
        # cost_in = tokens_in * pricing[model][0]
        # cost_out = tokens_out * pricing[model][1]

    def check_available(self) -> bool
        # Cloud providers assumed available → always True.
```

**OMLX Adapter — see §5.**

### 4.4 Cost Ledger (`cost.py`)

```python
class CostLedger:
    def __init__(self) -> None

    def record_call(self, task_id: TaskId, call: ModelCall) -> None
        # Appends call to the task's call list.
        # Creates a TaskCost entry if task_id not seen before.

    def record_escalation(self, task_id: TaskId, event: EscalationEvent) -> None
        # Appends escalation event to the task's escalation list.

    def finalize_task(
        self,
        task_id: TaskId,
        task_type: str,
        final_tier: TierName,
    ) -> TaskCost
        # Computes total_cost = sum(all calls' cost_in + cost_out)
        # Sets task_type and tier on the TaskCost.
        # Returns the finalized TaskCost.

    def cost_per_task(self, task_id: TaskId) -> TaskCost | None
        # Returns the TaskCost for a task, or None if not found.
        # Works for in-progress tasks (before finalize).

    def cost_report(self) -> CostReport
        # Builds CostReport from all finalized tasks.
        # per_tier: sum by final_tier across all tasks.
        # per_type: sum by task_type across all tasks.

    def reset(self) -> None
        # Clears all data. For testing.
```

**Aggregation rules:**

- `total_cost = sum(call.cost_in + call.cost_out for call in calls)` — escalation overhead is already included because failed calls' costs are in the `calls` list
- Thread-safe via `threading.Lock` per task (dict of locks, evicted after `finalize_task`)
- In-memory only in v1 (no persistence)
- `CostReport` computed on the fly from stored `TaskCost` records

### 4.5 Escalation Tracker + Routing Logger (`slo.py`)

```python
class EscalationTracker:
    def __init__(self, config: EscalationConfig, tier_order: list[TierName]) -> None
        # tier_order: list of tier names ordered by priority (highest first)
        # e.g. ["architect", "workhorse", "utility"]

    def record(self, event: EscalationEvent) -> None

    def escalation_rate(self, key: str) -> float
        # key can be a task_type OR a tier name.
        # If key matches a task_type: escalated_tasks / total_tasks for that type.
        # If key matches a tier name: escalations_from_tier / total_tasks_at_tier.
        # Ambiguous keys (matches both): prefers task_type match.
        # Returns 0.0 if no data.

    def threshold_breached(self, key: str) -> bool
        # True if escalation_rate(key) > threshold.
        # Threshold: per_tier[key] if key is a tier, else default_threshold.

    def next_tier(self, current_tier: TierName) -> TierName
        # Returns the next higher-priority tier (lower priority number).
        # tier_order = ["architect", "workhorse", "utility"]
        #   architect → architect (already highest, stays)
        #   workhorse → architect
        #   utility   → workhorse
        # If current_tier not in tier_order: raises ValueError.

    def trace(self, task_id: TaskId) -> list[EscalationEvent]
        # Returns all escalation events for a task (for RouterExhaustedError).


class RoutingLogger:
    def __init__(self, config: LoggingConfig) -> None

    def log_route(self, entry: RouteLogEntry) -> None
        # Appends to routes buffer. Emits JSON line if config.routing is True.

    def log_failover(self, entry: RouteLogEntry) -> None
        # Forces entry.decision = RouteDecisionType.FAILOVER.
        # Appends to failovers buffer. Emits JSON line if config.failover is True.

    def recent_routes(self, n: int = 10) -> list[RouteLogEntry]
    def recent_failovers(self, n: int = 10) -> list[RouteLogEntry]

    def summary(self) -> dict
        # {"total_routes": int, "total_failovers": int, "failover_rate": float}
```

**Tier ordering — how `next_tier` and `downgrade_tier` work:**

Both escalation (up) and downgrade (down) use the same tier order, derived from YAML insertion order:

```yaml
tiers:
  architect:    # priority 0 (highest)
  workhorse:    # priority 1
  utility:      # priority 2 (lowest)
```

- **`next_tier` (escalation up):** move to lower priority number (architect ← workhorse ← utility)
- **`downgrade_tier` (budget down):** move to higher priority number (architect → workhorse → utility)

Custom tier names work automatically — order is positional, not name-based.

### 4.6 Budget Enforcer (`budget.py`)

```python
class BudgetEnforcer:
    def __init__(
        self,
        config: BudgetsConfig,
        tier_order: list[TierName],
    ) -> None
        # tier_order: same ordered list as EscalationTracker

    def check(self, scope: ScopeId) -> BudgetCheck
        # Checks per_task, per_day, per_project limits for this scope.
        # Returns BudgetCheck with the most restrictive action found.
        # Priority: HARD_STOP > DOWNGRADE > WARN > allowed.
        # If DOWNGRADE: populates new_tier with downgrade_tier(current_tier_for_scope).

    def record_spend(self, scope: ScopeId, amount: Decimal) -> None
        # Adds amount to per_task, per_day, per_project accumulators for this scope.

    def reset_period(self, scope: ScopeId) -> None
        # Resets per_day accumulator (called by CLI or scheduler).

    def downgrade_tier(self, current_tier: TierName) -> TierName
        # architect → workhorse → utility → utility (stays at lowest)
        # Uses tier_order: move to higher index (lower priority).

    def current_usage(self, scope: ScopeId) -> dict
        # {"per_task_spend": Decimal, "per_task_limit": Decimal,
        #  "per_day_spend": Decimal, "per_day_limit": Decimal, ...}
```

### 4.7 CLI (`cli.py`)

```python
# Implementation: argparse (stdlib — no click dependency)

# Commands:
#   ai-tierforge route <task_type> <prompt> [--config <path>] [--scope <scope>]
#   ai-tierforge report [--config <path>] [--task <id>] [--type <task_type>]
#   ai-tierforge validate <config_path>
#   ai-tierforge budget check [--config <path>] [--scope <scope>]
#   ai-tierforge budget reset [--config <path>] [--scope <scope>]
#   ai-tierforge --version

# Global options:
#   --config <path>   Path to tiers.yaml (default: ./tiers.yaml)
#   --verbose         Enable debug logging
```

---

## 5. OMLX Adapter Details

### 5.1 Interface

```python
class OMLXAdapter:
    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        timeout: int = 60,
    ) -> None

    @property
    def name(self) -> str: return "omlx"

    def call(self, model, prompt, max_tokens, **kwargs) -> ModelCall
        # POST {endpoint}/v1/chat/completions
        # model format: "omlx:qwen2.5-coder:7b" → strip "omlx:" prefix → "qwen2.5-coder:7b"
        # Body: {"model": stripped_model, "messages": [{"role":"user","content":prompt}],
        #         "max_tokens": max_tokens, "stream": False, **kwargs}
        # No API key header — OMLX is local.
        # Returns ModelCall with tokens, cost=(0, 0), success, error.

    def calculate_cost(self, model, tokens_in, tokens_out) -> tuple[Decimal, Decimal]
        # OMLX is free → returns (Decimal("0"), Decimal("0"))

    def check_available(self) -> bool
        # GET {endpoint}/api/tags
        # Returns True if 200, False if connection refused / timeout.
```

### 5.2 Configuration

```yaml
tiers:
  utility:
    model: omlx:qwen2.5-coder:7b
    max_tokens: 4000
    provider: omlx
    endpoint: http://localhost:11434   # optional, defaults to localhost:11434
```

### 5.3 Behavior When OMLX Unavailable

- `check_available()` returns `False` — logged as a warning on router init
- If a task routes to the utility tier and OMLX is down, the adapter call fails with `error="connection_refused"`
- The router treats this as a retryable error → retries → escalates to workhorse tier
- This is the intended behavior: OMLX down = utility unavailable = escalate up

---

## 6. Error Handling

### 6.1 Custom Exceptions (`exceptions.py`)

```python
class TierForgeError(Exception):
    """Base exception for all ai-tierforge errors."""

class ConfigError(TierForgeError):
    """Invalid YAML config or schema validation failure."""
    def __init__(self, errors: list[str]):
        self.errors = errors
        super().__init__(f"config errors: {', '.join(errors)}")

class NoTierMatchError(TierForgeError):
    """No tier handles the given task_type."""
    def __init__(self, task_type: str):
        self.task_type = task_type
        super().__init__(f"no tier matches task_type '{task_type}'")

class ProviderError(TierForgeError):
    """Upstream provider failure after all retries."""
    def __init__(self, model: str, error: str):
        self.model = model
        self.error = error
        super().__init__(f"provider '{model}' failed: {error}")

class RouterExhaustedError(TierForgeError):
    """All retries and escalations exhausted — task could not be completed."""
    def __init__(self, task_id: str, escalation_trace: list):
        self.task_id = task_id
        self.escalation_trace = escalation_trace
        trace_summary = " → ".join(
            f"{e.from_tier}→{e.to_tier}" for e in escalation_trace
        ) or "no escalations"
        super().__init__(
            f"router exhausted for task '{task_id}': {trace_summary}"
        )

class BudgetExceededError(TierForgeError):
    """Hard stop triggered — budget limit exceeded."""
    def __init__(self, scope: str, reason: str):
        self.scope = scope
        self.reason = reason
        super().__init__(f"budget exceeded for scope '{scope}': {reason}")

class ConcurrencyError(TierForgeError):
    """Cost ledger lock timeout."""
    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"concurrency timeout on task '{task_id}'")
```

### 6.2 Retry Policy

| Layer | Retries | Backoff | When |
|---|---|---|---|
| Provider call (adapter) | 3 | Exponential (1s, 2s, 4s) | 5xx, timeout, rate limit |
| Router escalation | `config.escalation.max_retries` per tier (default 3) | Immediate (tier swap) | `should_escalate(error)` or per-tier retries exhausted |
| Router total attempts | `config.router.max_retries` (default 3) | — | Across all tiers — raises `RouterExhaustedError` |

### 6.3 Logging

`RoutingLogger` writes structured JSON lines to stdout (or file if `config.logging.output` is a path).

**Example log entries:**

```json
{"timestamp": 1723636800.123, "level": "info", "event": "route", "task_id": "a1b2c3", "tier": "workhorse", "model": "deepseek-v4-flash", "decision": "route", "reason": "matched task_type 'code'", "duration_ms": 0, "cost": "0.000"}
{"timestamp": 1723636801.456, "level": "info", "event": "failover", "task_id": "a1b2c3", "tier": "architect", "model": "glm-5.2", "decision": "failover", "reason": "escalation: timeout", "duration_ms": 0, "cost": "0.000"}
{"timestamp": 1723636802.789, "level": "info", "event": "route", "task_id": "a1b2c3", "tier": "architect", "model": "glm-5.2", "decision": "route", "reason": "task completed", "duration_ms": 1200, "cost": "0.053"}
```

**At `debug` level**, additional fields are included: `prompt` (full text), `response` (full text), `tokens_in`, `tokens_out`.

---

## 7. Concurrency Model

- `CostLedger`: `threading.Lock` per task ID (dict of locks, evict after `finalize_task`)
- `BudgetEnforcer`: `threading.Lock` per scope
- `TierRouter`: stateless routing; state lives in ledger + enforcer + tracker + logger
- `EscalationTracker`: `threading.Lock` on the events list
- `RoutingLogger`: `threading.Lock` on the log buffer
- **v1 constraint:** single-process only. Multi-process support would need a shared store (Redis, SQLite).

---

## 8. Testing Strategy

### 8.1 Test Layers

| Layer | Scope | Framework |
|---|---|---|
| Unit | Each component in isolation | pytest |
| Integration | Router + ledger + adapter end-to-end | pytest |
| Config | YAML parsing, validation, error messages | pytest |
| CLI | CLI invocation, output parsing | pytest + `subprocess` |
| Mock | All provider calls mocked; no real API calls | `unittest.mock` + `MockProviderAdapter` |

### 8.2 Mock Provider Adapter

```python
class MockProviderAdapter:
    """Test-only adapter that returns canned responses without network calls."""
    def __init__(
        self,
        name: str,
        success_rate: float = 1.0,
        responses: list[str] | None = None,
        fail_times: list[int] | None = None,
    ) -> None
        # success_rate: probability of success per call (0.0–1.0)
        # responses: canned response strings (cycled through)
        # fail_times: list of attempt numbers that should fail (overrides success_rate)

    @property
    def name(self) -> str: return self._name

    def call(self, model, prompt, max_tokens, **kwargs) -> ModelCall
        # If attempt in fail_times: return ModelCall(success=False, error="mock_failure")
        # If random() > success_rate: return ModelCall(success=False, error="random_failure")
        # Else: return ModelCall(success=True, response=canned_response, tokens_in=100, tokens_out=50)

    def calculate_cost(self, model, tokens_in, tokens_out) -> tuple[Decimal, Decimal]
        # Returns (Decimal("0.001"), Decimal("0.002")) — fixed mock pricing

    def check_available(self) -> bool: return True
```

### 8.3 Fixture Strategy (`tests/conftest.py`)

```python
@pytest.fixture
def sample_config() -> TierForgeConfig:
    return TierForgeConfig(
        tiers={
            "architect": TierConfig(model="glm-5.2", max_tokens=16000,
                use_for=["spec", "architecture"], provider="openai-compatible", priority=0),
            "workhorse": TierConfig(model="deepseek-v4-flash", max_tokens=8000,
                use_for=["code", "tests"], provider="openai-compatible", priority=1),
            "utility": TierConfig(model="omlx:qwen2.5-coder:7b", max_tokens=4000,
                use_for=["tickets"], provider="omlx", priority=2),
        },
    )

@pytest.fixture
def mock_adapters() -> dict[str, MockProviderAdapter]:
    return {
        "openai-compatible": MockProviderAdapter("openai-compatible", success_rate=1.0),
        "omlx": MockProviderAdapter("omlx", success_rate=1.0),
    }

@pytest.fixture
def router(sample_config, mock_adapters) -> TierRouter:
    return TierRouter(sample_config, mock_adapters)

@pytest.fixture
def failing_adapters() -> dict[str, MockProviderAdapter]:
    return {
        "openai-compatible": MockProviderAdapter("openai-compatible", success_rate=0.0),
        "omlx": MockProviderAdapter("omlx", success_rate=0.0),
    }
```

### 8.4 Coverage Targets

| Module | Target |
|---|---|
| `config.py` | 100% (config parsing + validation) |
| `router.py` | 95% (routing logic + edge cases) |
| `cost.py` | 95% (aggregation + concurrency) |
| `slo.py` | 90% (threshold, logging) |
| `budget.py` | 90% (enforcement, downgrade chain) |
| `omlx.py` | 80% (mocked HTTP — can't test real OMLX in CI) |
| `cli.py` | 80% (arg parsing + integration smoke tests) |
| `exceptions.py` | 100% (simple classes) |

---

## 9. Dependencies

### Runtime Dependencies

| Package | Version | Purpose |
|---|---|---|
| `pyyaml` | >=6.0 | YAML config parsing |
| `requests` | >=2.32 | HTTP calls to LLM providers (OpenAI-compatible + OMLX) |

**Total runtime dependencies: 2.** No `click` (use `argparse` from stdlib). No `httpx` (use `requests`).

### Dev Dependencies

| Package | Version | Purpose |
|---|---|---|
| `pytest` | >=8 | Testing |
| `pytest-cov` | >=5 | Coverage reporting |
| `ruff` | >=0.5 | Linting |
| `mypy` | >=1.10 | Type checking |

---

## 10. Cost Model

### 10.1 Pricing Table (v1 built-in, configurable)

```python
from decimal import Decimal

DEFAULT_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # model: (cost_per_token_in, cost_per_token_out)
    "glm-5.2":              (Decimal("0.000003"),    Decimal("0.000008")),
    "deepseek-v4-flash":    (Decimal("0.00000014"),  Decimal("0.00000028")),
    "deepseek-v4-pro":      (Decimal("0.0000015"),   Decimal("0.000004")),
    "gpt-4o":               (Decimal("0.0000025"),   Decimal("0.00001")),
    "gpt-4o-mini":          (Decimal("0.00000015"),  Decimal("0.0000006")),
    # Anthropic models listed for use via OpenAI-compatible proxies (e.g. LiteLLM, Portkey).
    # Native Anthropic API support is out of scope for v1 (see PRD §3.3).
    "claude-sonnet-4":      (Decimal("0.000003"),    Decimal("0.000015")),
    "claude-haiku-3.5":     (Decimal("0.00000025"),  Decimal("0.00000125")),
}
```

**Override mechanism:** Pass `pricing={...}` to `OpenAICompatAdapter.__init__()`. Future: YAML `pricing:` section (open question in PRD §11.2).

**Unknown model:** `calculate_cost()` raises `KeyError(f"no pricing for model '{model}'")`. Users must provide pricing for custom models.

### 10.2 Cost-Per-Task Calculation

```
task_cost = sum(call.cost_in + call.cost_out for call in task.calls)
```

That's it. Escalation overhead is already included — failed calls' costs are in `task.calls`. The `EscalationEvent.cost_before_escalation` field is for reporting/debugging only, not added separately.

**Example:**

```
Task "code_generation":
  Call 1: workhorse, failed, cost_in=0.001, cost_out=0     → $0.001
  Call 2: workhorse, failed, cost_in=0.001, cost_out=0     → $0.001
  Call 3: workhorse, failed, cost_in=0.001, cost_out=0     → $0.001
  Escalation: workhorse → architect (RETRY_EXCEEDED)
  Call 4: architect, success, cost_in=0.003, cost_out=0.008 → $0.011
  ─────────────────────────────────────────────────────────────
  total_cost = $0.014
  (not $0.011 — the 3 failed workhorse calls still cost money)
```

---

## 11. Build & CI

### 11.1 Development Commands

```bash
# Install (editable, with dev deps)
pip install -e ".[dev]"

# Test
pytest --cov=ai_tierforge --cov-report=term-missing

# Lint
ruff check src/ tests/

# Type check
mypy src/
```

### 11.2 CI (GitHub Actions)

- **Push / PR to main:** test (Python 3.11, 3.12), lint (ruff), type-check (mypy)
- **Tag `v*`:** publish to PyPI (trusted publishing)
- **Scheduled:** none (manual release for v0.x)

---

## 12. M1–M6 Build Order

| Step | Milestone | Files | Test Files |
|---|---|---|---|
| 1 | M1 scaffold | `types.py`, `exceptions.py`, `config.py`, `adapters/base.py`, `adapters/openai_compat.py` | `test_types.py`, `test_config.py`, `test_exceptions.py` |
| 2 | M1 router | `router.py`, `__init__.py` | `test_router.py` |
| 3 | M2 ledger | `cost.py` | `test_cost.py` |
| 4 | M3 SLO | `slo.py` | `test_slo.py` |
| 5 | M4 OMLX | `omlx.py` | `test_omlx.py` |
| 6 | M5 budget | `budget.py` | `test_budget.py` |
| 7 | M6 CLI | `cli.py` | `test_cli.py` |
| 8 | M6 packaging | `pyproject.toml` (update), CI workflow | — |
| 9 | Integration | `tests/integration/` | Integration test |
| 10 | Docs | README polish, docstrings | — |

---

## 13. YAML Config Schema

### 13.1 Full Schema

```yaml
# tiers.yaml — ai-tierforge configuration

tiers:
  architect:                      # tier name (any string)
    model: glm-5.2               # model identifier passed to provider
    max_tokens: 16000            # max output tokens for this tier
    use_for:                     # task types this tier handles
      - spec
      - architecture
      - review
      - escalation
    provider: openai-compatible  # adapter name
    endpoint: null               # optional: override provider endpoint
    # priority: 0               # optional: override positional priority (0=highest)

  workhorse:
    model: deepseek-v4-flash
    max_tokens: 8000
    use_for: [code, tests, refactor, drafts]
    provider: openai-compatible

  utility:
    model: omlx:qwen2.5-coder:7b
    max_tokens: 4000
    use_for: [tickets, summaries, status, admin]
    provider: omlx
    endpoint: http://localhost:11434

escalation:
  default_threshold: 0.30        # 30% — alert when escalation rate exceeds this
  max_retries: 3                 # retries per tier before escalating
  per_tier:                      # optional per-tier threshold overrides
    workhorse: 0.30
    utility: 0.50                # utility escalates more — expected

router:
  max_retries: 3                 # total attempts across all tiers before RouterExhaustedError

budgets:
  per_task:
    limit: 0.10                  # $0.10 per task
    on_exceed: downgrade         # warn | downgrade | hard_stop
  per_day:
    limit: 5.00                  # $5.00 per day
    on_exceed: warn
  # per_project:
  #   limit: 100.00
  #   on_exceed: hard_stop

logging:
  routing: true                  # log routing decisions
  failover: true                 # log failover events
  level: info                    # info | debug
  output: stdout                 # stdout | file path
```

### 13.2 Field Reference

| Section | Field | Type | Required | Default | Description |
|---|---|---|---|---|---|
| `tiers` | (tier name) | dict | yes | — | Tier name → config. Insertion order = escalation priority. |
| `tiers.*` | `model` | string | yes | — | Model name passed to provider adapter |
| `tiers.*` | `max_tokens` | int | yes | — | Max output tokens |
| `tiers.*` | `use_for` | list[string] | yes | — | Task types this tier handles |
| `tiers.*` | `provider` | string | yes | — | Adapter name (`openai-compatible`, `omlx`, or custom) |
| `tiers.*` | `endpoint` | string | no | adapter default | Override provider endpoint URL |
| `tiers.*` | `priority` | int | no | YAML position | Escalation priority (0=highest) |
| `escalation` | `default_threshold` | float | no | 0.30 | Escalation rate SLO threshold |
| `escalation` | `max_retries` | int | no | 3 | Retries per tier before escalating |
| `escalation` | `per_tier` | dict[str, float] | no | {} | Per-tier threshold overrides |
| `router` | `max_retries` | int | no | 3 | Total attempts before `RouterExhaustedError` |
| `budgets` | `per_task` | BudgetConfig | no | null | Per-task budget |
| `budgets` | `per_day` | BudgetConfig | no | null | Per-day budget |
| `budgets` | `per_project` | BudgetConfig | no | null | Per-project budget |
| `budgets.*` | `limit` | Decimal | yes | — | Budget limit in USD |
| `budgets.*` | `on_exceed` | enum | yes | — | `warn` / `downgrade` / `hard_stop` |
| `logging` | `routing` | bool | no | true | Log routing decisions |
| `logging` | `failover` | bool | no | true | Log failover events |
| `logging` | `level` | string | no | `info` | `info` or `debug` |
| `logging` | `output` | string | no | `stdout` | `stdout` or file path |

---

## 14. Public API (`__init__.py`)

```python
"""ai-tierforge: Multi-model LLM tier router with cost-per-task accounting."""

from ai_tierforge.router import TierRouter
from ai_tierforge.config import TierForgeConfigLoader
from ai_tierforge.cost import CostLedger
from ai_tierforge.slo import EscalationTracker, RoutingLogger
from ai_tierforge.budget import BudgetEnforcer
from ai_tierforge.omlx import OMLXAdapter
from ai_tierforge.adapters.openai_compat import OpenAICompatAdapter
from ai_tierforge.adapters.base import ProviderAdapter
from ai_tierforge.types import (
    TierForgeConfig,
    TierConfig,
    EscalationConfig,
    RouterConfig,
    BudgetsConfig,
    BudgetConfig,
    LoggingConfig,
    OnExceedAction,
    ModelCall,
    TaskCost,
    CostReport,
    EscalationEvent,
    EscalationCause,
    RouteLogEntry,
    RouteDecisionType,
    BudgetCheck,
)
from ai_tierforge.exceptions import (
    TierForgeError,
    ConfigError,
    NoTierMatchError,
    ProviderError,
    RouterExhaustedError,
    BudgetExceededError,
    ConcurrencyError,
)

__all__ = [
    # Core
    "TierRouter",
    "TierForgeConfigLoader",
    "CostLedger",
    "EscalationTracker",
    "RoutingLogger",
    "BudgetEnforcer",
    # Adapters
    "ProviderAdapter",
    "OpenAICompatAdapter",
    "OMLXAdapter",
    # Types
    "TierForgeConfig",
    "TierConfig",
    "EscalationConfig",
    "RouterConfig",
    "BudgetsConfig",
    "BudgetConfig",
    "LoggingConfig",
    "OnExceedAction",
    "ModelCall",
    "TaskCost",
    "CostReport",
    "EscalationEvent",
    "EscalationCause",
    "RouteLogEntry",
    "RouteDecisionType",
    "BudgetCheck",
    # Exceptions
    "TierForgeError",
    "ConfigError",
    "NoTierMatchError",
    "ProviderError",
    "RouterExhaustedError",
    "BudgetExceededError",
    "ConcurrencyError",
]

__version__ = "0.1.0"
```
