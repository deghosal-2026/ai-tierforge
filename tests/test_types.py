"""
Tests for data structures in types.py.

These tests verify that all dataclasses, enums, and type aliases
are correctly defined and have the expected default values.  They
also test the computed methods on CostReport (cost_per_task and
escalation_rate).

No mocking needed — these are pure data structure tests.
"""

from decimal import Decimal

from ai_tierforge.types import (
    BudgetCheck,
    BudgetConfig,
    BudgetsConfig,
    CostReport,
    EscalationCause,
    EscalationConfig,
    EscalationEvent,
    LoggingConfig,
    ModelCall,
    OnExceedAction,
    RouteDecisionType,
    RouteLogEntry,
    RouterConfig,
    TaskCost,
    TierConfig,
    TierForgeConfig,
)


def test_tier_config_defaults():
    """TierConfig should set default values for optional fields.

    The ``endpoint`` and ``priority`` fields are optional — endpoint
    defaults to None (use adapter default) and priority defaults to 0
    (highest, but usually overridden by YAML position).
    """
    tc = TierConfig(
        model="test-model",
        max_tokens=1000,
        use_for=["code"],
        provider="openai-compatible",
    )
    assert tc.endpoint is None
    assert tc.priority == 0


def test_escalation_config_defaults():
    """EscalationConfig should have sensible defaults.

    Default threshold is 30% (the industry-recommended escalation SLO),
    max_retries is 3, and per_tier overrides start empty.
    """
    ec = EscalationConfig()
    assert ec.default_threshold == 0.30
    assert ec.max_retries == 3
    assert ec.per_tier == {}


def test_router_config_defaults():
    """RouterConfig should have a default max_retries of 3."""
    rc = RouterConfig()
    assert rc.max_retries == 3


def test_on_exceed_action_values():
    """OnExceedAction enum should have all three values.

    These string values match what users put in YAML config.
    """
    assert OnExceedAction.WARN.value == "warn"
    assert OnExceedAction.DOWNGRADE.value == "downgrade"
    assert OnExceedAction.HARD_STOP.value == "hard_stop"


def test_model_call_defaults():
    """ModelCall should have sensible defaults for optional fields.

    Required fields (task_id, task_type, tier, model, prompt) must be
    provided.  Optional fields default to: no response, zero tokens,
    zero cost, success=True, no error, attempt=0.
    """
    mc = ModelCall(
        task_id="t1",
        task_type="code",
        tier="workhorse",
        model="deepseek-v4-flash",
        prompt="hello",
    )
    assert mc.response is None
    assert mc.tokens_in == 0
    assert mc.cost_in == Decimal("0")
    assert mc.success is True
    assert mc.attempt == 0


def test_escalation_cause_enum():
    """EscalationCause should have all five causes with auto() values.

    auto() assigns incremental values starting at 1.
    """
    assert EscalationCause.RETRY_EXCEEDED.value == 1
    assert EscalationCause.CONTENT_TOO_LONG.value == 2
    assert EscalationCause.TIMEOUT.value == 3
    assert EscalationCause.BUDGET_DOWNGRADE.value == 4
    assert EscalationCause.PROVIDER_ERROR.value == 5


def test_route_decision_type_enum():
    """RouteDecisionType should have ROUTE and FAILOVER values.

    These string values appear in JSON log entries.
    """
    assert RouteDecisionType.ROUTE.value == "route"
    assert RouteDecisionType.FAILOVER.value == "failover"


def test_route_log_entry_defaults():
    """RouteLogEntry should auto-generate a timestamp via time.time()."""
    rle = RouteLogEntry(
        task_id="t1",
        tier="workhorse",
        model="deepseek-v4-flash",
        decision=RouteDecisionType.ROUTE,
        reason="test",
    )
    assert rle.task_id == "t1"
    # timestamp should be a positive Unix time
    assert rle.timestamp > 0


def test_task_cost_defaults():
    """TaskCost should default to empty lists for calls and escalations."""
    tc = TaskCost(
        task_id="t1",
        tier="workhorse",
        task_type="code",
        total_cost=Decimal("0.05"),
    )
    assert tc.calls == []
    assert tc.escalations == []


def test_cost_report_cost_per_type():
    report = CostReport(
        per_task={
            "t1": TaskCost(
                task_id="t1", tier="workhorse", task_type="code",
                total_cost=Decimal("0.10"),
            ),
            "t2": TaskCost(
                task_id="t2", tier="architect", task_type="spec",
                total_cost=Decimal("0.20"),
            ),
        },
    )
    assert report.cost_per_type("code") == Decimal("0.10")
    assert report.cost_per_type("spec") == Decimal("0.20")
    assert report.cost_per_type("other") == Decimal("0")


def test_cost_report_escalation_rate():
    """escalation_rate should return the correct fraction.

    1 out of 2 "code" tasks escalated → rate = 0.5
    """
    report = CostReport(
        per_task={
            "t1": TaskCost(
                task_id="t1", tier="workhorse", task_type="code",
                total_cost=Decimal("0.10"),
                # This task had an escalation
                escalations=[EscalationEvent(
                    task_id="t1", task_type="code",
                    from_tier="workhorse", to_tier="architect",
                    cause=EscalationCause.RETRY_EXCEEDED,
                )],
            ),
            "t2": TaskCost(
                task_id="t2", tier="architect", task_type="code",
                total_cost=Decimal("0.05"),
                # This task had no escalations
            ),
        },
    )
    assert report.escalation_rate("code") == 0.5


def test_cost_report_escalation_rate_empty():
    """escalation_rate should return 0.0 when no tasks of that type exist."""
    report = CostReport()
    assert report.escalation_rate("code") == 0.0


def test_budget_check_defaults():
    """BudgetCheck should allow new_tier to be None.

    new_tier is only populated when action == DOWNGRADE.
    """
    bc = BudgetCheck(
        allowed=True,
        action=OnExceedAction.WARN,
        reason="within budget",
    )
    assert bc.new_tier is None


def test_tier_forge_config_defaults():
    """TierForgeConfig should apply defaults to all nested sub-configs.

    When only tiers is provided, escalation, router, budgets, and
    logging should all get their default values.
    """
    config = TierForgeConfig(
        tiers={
            "workhorse": TierConfig(
                model="test", max_tokens=1000,
                use_for=["code"], provider="test",
            ),
        },
    )
    # Escalation defaults
    assert config.escalation.default_threshold == 0.30
    # Router defaults
    assert config.router.max_retries == 3
    # Budgets default to None (no enforcement)
    assert config.budgets.per_task is None
    # Logging defaults
    assert config.logging.level == "info"


def test_budget_config_creation():
    """BudgetConfig should store limit as Decimal and action as enum."""
    bc = BudgetConfig(
        limit=Decimal("0.10"),
        on_exceed=OnExceedAction.DOWNGRADE,
    )
    assert bc.limit == Decimal("0.10")
    assert bc.on_exceed == OnExceedAction.DOWNGRADE


def test_budgets_config_optional():
    """BudgetsConfig should allow None for each scope independently."""
    bc = BudgetsConfig()
    assert bc.per_task is None
    assert bc.per_day is None
    assert bc.per_project is None


def test_logging_config_defaults():
    """LoggingConfig should enable routing and failover by default.

    Default level is "info" and output goes to stdout.
    """
    lc = LoggingConfig()
    assert lc.routing is True
    assert lc.failover is True
    assert lc.level == "info"
    assert lc.output == "stdout"
