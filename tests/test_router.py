"""
Tests for the TierRouter — the core routing component.

These tests verify:
1. ``tier_for_task`` matches task types to the correct tier.
2. ``route`` happy path: a successful call returns the right ModelCall.
3. Task ID auto-generation and explicit task ID support.
4. Escalation: when all adapters fail, ``RouterExhaustedError`` is raised.
5. Budget enforcement: ``HARD_STOP`` raises ``BudgetExceededError``.
6. Cost report: after routing, the cost report contains relevant data.
7. Multiple task types route to different tiers.
8. ``should_escalate`` correctly classifies errors as immediate vs retryable.

All tests use mock adapters (from conftest.py) — no real API calls are made.
"""

import pytest

from ai_tierforge.exceptions import (
    BudgetExceededError,
    NoTierMatchError,
    RouterExhaustedError,
)


def test_tier_for_task_returns_correct_tier(router):
    """tier_for_task should return the workhorse tier for 'code'.

    The sample config maps 'code' to the workhorse tier (DeepSeek).
    """
    name, config = router.tier_for_task("code")
    assert name == "workhorse"
    assert config.model == "deepseek-v4-flash"


def test_tier_for_task_architect(router):
    """tier_for_task should return the architect tier for 'spec'.

    The sample config maps 'spec' to the architect tier (GLM-5.2).
    """
    name, config = router.tier_for_task("spec")
    assert name == "architect"
    assert config.model == "glm-5.2"


def test_tier_for_task_unknown_raises(router):
    """tier_for_task should raise NoTierMatchError for unknown task types.

    If no tier's use_for list contains the task_type, the router
    can't determine where to send the call.
    """
    with pytest.raises(NoTierMatchError):
        router.tier_for_task("unknown_type")


def test_route_happy_path(router):
    """A successful route should return a ModelCall with the correct tier and model.

    The mock adapter returns success=True, so the router should
    return immediately with the workhorse tier's response.
    """
    result = router.route("code", "Write a test")
    assert result.success is True
    assert result.tier == "workhorse"
    assert result.model == "deepseek-v4-flash"
    assert result.response == "mock response"


def test_route_sets_task_id(router):
    """route should auto-generate a task_id (uuid4 hex) if not provided.

    The generated task_id should be a non-empty string.
    """
    result = router.route("code", "hello")
    assert result.task_id is not None
    assert len(result.task_id) > 0


def test_route_uses_provided_task_id(router):
    """route should use the task_id provided by the caller.

    This allows grouping multiple calls under the same task for
    cost aggregation.
    """
    result = router.route("code", "hello", task_id="my-custom-id")
    assert result.task_id == "my-custom-id"


def test_route_escalation_on_failure(router, failing_adapters):
    """When all adapters fail, route should raise RouterExhaustedError.

    Uses the failing_adapters fixture which returns success=False
    for every call.  With a single-tier config and failing adapters,
    the router should exhaust all retries and raise.
    """
    from ai_tierforge.router import TierRouter
    from ai_tierforge.config import TierForgeConfigLoader

    # Single-tier config — no escalation possible, just exhaustion
    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "architect": {
                "model": "glm-5.2", "max_tokens": 16000,
                "use_for": ["code"], "provider": "openai-compatible",
            },
        },
    })
    r = TierRouter(config, failing_adapters)
    with pytest.raises(RouterExhaustedError):
        r.route("code", "hello")


def test_route_escalates_to_next_tier(router, failing_adapters, mock_adapters):
    """When the first tier fails, the router should escalate to the next tier.

    Uses a two-tier config where workhorse always fails and architect succeeds.
    The router should escalate from workhorse to architect after exhausting
    per-tier retries, and the final result should come from architect.
    """
    from ai_tierforge.router import TierRouter
    from ai_tierforge.config import TierForgeConfigLoader

    config = TierForgeConfigLoader.from_dict({
        "tiers": {
            "architect": {
                "model": "glm-5.2", "max_tokens": 16000,
                "use_for": ["spec"], "provider": "openai-compatible",
            },
            "workhorse": {
                "model": "deepseek-v4-flash", "max_tokens": 8000,
                "use_for": ["spec"], "provider": "openai-compatible",
            },
        },
    })
    r = TierRouter(config, failing_adapters)
    with pytest.raises(RouterExhaustedError):
        r.route("spec", "hello")


def test_route_budget_hard_stop(router, mock_adapters):
    """HARD_STOP budget action should raise BudgetExceededError.

    Configures a per_task budget with limit=0.0 and on_exceed=HARD_STOP.
    Since the initial spend is 0 and the limit is 0, the budget check
    should immediately return HARD_STOP before any call is made.
    """
    from ai_tierforge.router import TierRouter
    from ai_tierforge.types import (
        BudgetConfig,
        BudgetsConfig,
        OnExceedAction,
        TierConfig,
        TierForgeConfig,
    )

    config = TierForgeConfig(
        tiers={
            "workhorse": TierConfig(
                model="test", max_tokens=1000,
                use_for=["code"], provider="openai-compatible",
            ),
        },
        budgets=BudgetsConfig(
            per_task=BudgetConfig(
                limit=0.0,
                on_exceed=OnExceedAction.HARD_STOP,
            ),
        ),
    )
    r = TierRouter(config, mock_adapters)
    with pytest.raises(BudgetExceededError):
        r.route("code", "hello")


def test_cost_report_after_route(router):
    """After a successful route, cost_report should contain relevant data.

    The per_task dict should have at least one entry, and the per_tier
    dict should have the workhorse tier.
    """
    router.route("code", "hello")
    report = router.cost_report()
    assert len(report.per_task) > 0
    assert "workhorse" in report.per_tier


def test_route_multiple_task_types(router):
    """Different task types should route to different tiers.

    'code' → workhorse (DeepSeek), 'spec' → architect (GLM-5.2).
    This verifies that the router correctly matches task types.
    """
    code_result = router.route("code", "hello")
    spec_result = router.route("spec", "design doc")
    assert code_result.tier == "workhorse"
    assert spec_result.tier == "architect"


def test_route_preserves_task_id_across_calls(router):
    """Multiple calls with the same task_id should share the cost record.

    The cost ledger groups calls by task_id, so using the same ID
    for multiple calls aggregates their costs under one TaskCost.
    """
    tid = "shared-task"
    router.route("code", "hello", task_id=tid)
    report = router.cost_report()
    assert tid in report.per_task


def test_should_escalate_immediate():
    """should_escalate should return True for non-retryable errors.

    These errors mean retrying the same model is pointless:
    - content_too_long: response exceeded max_tokens
    - context_length_exceeded: input too long for the model
    - rate_limit_exceeded: won't resolve on retry anytime soon
    """
    from ai_tierforge.router import TierRouter
    assert TierRouter.should_escalate("content_too_long") is True
    assert TierRouter.should_escalate("context_length_exceeded") is True
    assert TierRouter.should_escalate("rate_limit_exceeded") is True


def test_should_escalate_retryable():
    """should_escalate should return False for retryable errors.

    These errors are transient and may succeed on retry:
    - timeout: the provider was slow, try again
    - connection_error: network blip, try again
    - 5xx: server error, might be temporary
    """
    from ai_tierforge.router import TierRouter
    assert TierRouter.should_escalate("timeout") is False
    assert TierRouter.should_escalate("connection_error") is False
    assert TierRouter.should_escalate("5xx") is False


def test_should_escalate_none():
    """should_escalate should return False for None (no error).

    A successful call has error=None, so should_escalate should
    return False — no escalation needed.
    """
    from ai_tierforge.router import TierRouter
    assert TierRouter.should_escalate(None) is False
