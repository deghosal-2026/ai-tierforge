"""
ai-tierforge: Multi-model LLM tier router with cost-per-task accounting.

ai-tierforge routes LLM calls across three tiers (architect, workhorse,
utility), tracks the real cost of a completed task — including retries,
escalations, and failed loops — and alerts when escalation rate exceeds
a configurable threshold.

Public API
----------
The following classes and types are exported for user consumption::

    from ai_tierforge import (
        # Core components
        TierRouter,              # Main entry point — route calls, get reports
        TierForgeConfigLoader,   # Load + validate YAML config
        CostLedger,              # Thread-safe cost tracking
        EscalationTracker,       # Escalation rate SLO
        RoutingLogger,           # Route vs failover logging
        BudgetEnforcer,          # Per-scope budget enforcement

        # Adapters
        ProviderAdapter,         # Protocol — implement for custom providers
        OpenAICompatAdapter,     # Built-in: OpenAI / DeepSeek / vLLM / etc.
        OMLXAdapter,             # Built-in: local OMLX models

        # Config types
        TierForgeConfig, TierConfig, EscalationConfig, RouterConfig,
        BudgetsConfig, BudgetConfig, LoggingConfig, OnExceedAction,

        # Runtime types
        ModelCall, TaskCost, CostReport,
        EscalationEvent, EscalationCause,
        RouteLogEntry, RouteDecisionType, BudgetCheck,

        # Exceptions
        TierForgeError, ConfigError, NoTierMatchError,
        ProviderError, RouterExhaustedError,
        BudgetExceededError, ConcurrencyError,
    )

Quick start::

    from ai_tierforge import TierRouter

    router = TierRouter.from_yaml("tiers.yaml")
    response = router.route("code", "Write a unit test for auth")
    print(router.cost_report().cost_per_task("code"))
"""

from ai_tierforge.router import TierRouter
from ai_tierforge.config import TierForgeConfigLoader
from ai_tierforge.cost import CostLedger
from ai_tierforge.slo import EscalationTracker, RoutingLogger
from ai_tierforge.budget import BudgetEnforcer
from ai_tierforge.omlx import OMLXAdapter
from ai_tierforge.adapters.openai_compat import OpenAICompatAdapter
from ai_tierforge.adapters.base import ProviderAdapter

# Config and runtime types
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

# Exception hierarchy
from ai_tierforge.exceptions import (
    TierForgeError,
    ConfigError,
    NoTierMatchError,
    ProviderError,
    RouterExhaustedError,
    BudgetExceededError,
    ConcurrencyError,
)

# __all__ defines the public API surface — everything listed here is
# importable with `from ai_tierforge import *` and shows up in docs.
__all__ = [
    # Core components
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
    # Config types
    "TierForgeConfig",
    "TierConfig",
    "EscalationConfig",
    "RouterConfig",
    "BudgetsConfig",
    "BudgetConfig",
    "LoggingConfig",
    "OnExceedAction",
    # Runtime types
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
