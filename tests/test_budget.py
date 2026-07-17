from decimal import Decimal

from ai_tierforge.budget import BudgetEnforcer
from ai_tierforge.types import (
    BudgetConfig,
    BudgetsConfig,
    OnExceedAction,
)


def _enforcer(**overrides):
    config = BudgetsConfig(
        per_task=BudgetConfig(limit=Decimal("10"), on_exceed=OnExceedAction.WARN),
        per_day=BudgetConfig(limit=Decimal("100"), on_exceed=OnExceedAction.DOWNGRADE),
        per_project=BudgetConfig(limit=Decimal("1000"), on_exceed=OnExceedAction.HARD_STOP),
    )
    return BudgetEnforcer(config, ["a", "w", "u"])


def test_check_allowed_within_budget():
    b = _enforcer()
    result = b.check("scope1")
    assert result.allowed is True


def test_check_hard_stop():
    b = _enforcer()
    b.record_spend("s1", Decimal("1000"))
    result = b.check("s1")
    assert result.action == OnExceedAction.HARD_STOP


def test_check_downgrade():
    b = _enforcer()
    b.record_spend("s2", Decimal("100"), tier="a")
    result = b.check("s2")
    assert result.action == OnExceedAction.DOWNGRADE
    assert result.new_tier is not None


def test_check_warn():
    budgets = BudgetsConfig(
        per_task=BudgetConfig(limit=Decimal("5"), on_exceed=OnExceedAction.WARN),
    )
    b = BudgetEnforcer(budgets, ["a", "w", "u"])
    b.record_spend("s", Decimal("10"))
    result = b.check("s")
    assert result.action == OnExceedAction.WARN
    assert result.allowed is True


def test_hard_stop_takes_priority():
    budgets = BudgetsConfig(
        per_task=BudgetConfig(limit=Decimal("5"), on_exceed=OnExceedAction.DOWNGRADE),
        per_day=BudgetConfig(limit=Decimal("10"), on_exceed=OnExceedAction.HARD_STOP),
    )
    b = BudgetEnforcer(budgets, ["a", "w", "u"])
    b.record_spend("s", Decimal("20"))
    result = b.check("s")
    assert result.action == OnExceedAction.HARD_STOP


def test_downgrade_takes_priority_over_warn():
    budgets = BudgetsConfig(
        per_task=BudgetConfig(limit=Decimal("5"), on_exceed=OnExceedAction.WARN),
        per_day=BudgetConfig(limit=Decimal("10"), on_exceed=OnExceedAction.DOWNGRADE),
    )
    b = BudgetEnforcer(budgets, ["a", "w", "u"])
    b.record_spend("s", Decimal("20"), tier="a")
    result = b.check("s")
    assert result.action == OnExceedAction.DOWNGRADE


def test_no_budgets_configured():
    b = BudgetEnforcer(BudgetsConfig(), ["a", "w", "u"])
    result = b.check("scope")
    assert result.allowed is True


def test_record_spend_accumulates():
    b = _enforcer()
    b.record_spend("s", Decimal("5"))
    assert b._spend["s"]["per_task"] == Decimal("5")
    assert b._spend["s"]["per_day"] == Decimal("5")
    assert b._spend["s"]["per_project"] == Decimal("5")


def test_record_spend_multiple_calls():
    b = _enforcer()
    b.record_spend("s", Decimal("3"))
    b.record_spend("s", Decimal("7"))
    assert b._spend["s"]["per_task"] == Decimal("10")


def test_record_spend_tracks_tier():
    b = _enforcer()
    b.record_spend("s", Decimal("1"), tier="w")
    assert b._current_tiers["s"] == "w"


def test_reset_period_clears_per_day():
    b = _enforcer()
    b.record_spend("s", Decimal("50"))
    b.reset_period("s")
    assert b._spend["s"]["per_day"] == Decimal("0")
    assert b._spend["s"]["per_task"] == Decimal("50")


def test_downgrade_tier_moves_down():
    b = _enforcer()
    assert b.downgrade_tier("a") == "w"
    assert b.downgrade_tier("w") == "u"


def test_downgrade_tier_stays_at_bottom():
    b = _enforcer()
    assert b.downgrade_tier("u") == "u"


def test_downgrade_tier_unknown_defaults_to_lowest():
    b = _enforcer()
    assert b.downgrade_tier("unknown") == "u"


def test_downgrade_tier_uses_current_tier_from_scope():
    budgets = BudgetsConfig(
        per_day=BudgetConfig(limit=Decimal("5"), on_exceed=OnExceedAction.DOWNGRADE),
    )
    b = BudgetEnforcer(budgets, ["a", "w", "u"])
    b.record_spend("s1", Decimal("10"), tier="w")
    result = b.check("s1")
    assert result.new_tier == "u"


def test_current_usage_returns_configured_scopes():
    budgets = BudgetsConfig(
        per_task=BudgetConfig(limit=Decimal("50"), on_exceed=OnExceedAction.WARN),
    )
    b = BudgetEnforcer(budgets, [])
    b.record_spend("s", Decimal("10"))
    usage = b.current_usage("s")
    assert usage["per_task_spend"] == Decimal("10")
    assert usage["per_task_limit"] == Decimal("50")
    assert usage["per_task_remaining"] == Decimal("40")
    assert "per_day_spend" not in usage
