from decimal import Decimal

from ai_tierforge.cost import CostLedger
from ai_tierforge.types import (
    EscalationCause,
    EscalationEvent,
    ModelCall,
)


def _make_call(task_id="t1", tier="w", cost_in=0, cost_out=0, success=True):
    return ModelCall(
        task_id=task_id, task_type="code", tier=tier,
        model="m", prompt="hello", response="ok" if success else None,
        tokens_in=100, tokens_out=50,
        cost_in=Decimal(str(cost_in)), cost_out=Decimal(str(cost_out)),
        success=success,
    )


def _make_escalation(task_id="t1", task_type="code", from_tier="w", to_tier="a"):
    return EscalationEvent(
        task_id=task_id, task_type=task_type,
        from_tier=from_tier, to_tier=to_tier,
        cause=EscalationCause.RETRY_EXCEEDED,
    )


def test_record_call_creates_task_cost():
    ledger = CostLedger()
    call = _make_call()
    ledger.record_call("t1", call)
    task = ledger._tasks["t1"]
    assert task.task_id == "t1"
    assert task.tier == "w"


def test_record_call_appends_calls():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call(cost_in=1))
    ledger.record_call("t1", _make_call(cost_in=2))
    assert len(ledger._tasks["t1"].calls) == 2


def test_record_call_different_tasks_independent():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call(task_id="t1"))
    ledger.record_call("t2", _make_call(task_id="t2"))
    assert len(ledger._tasks) == 2


def test_record_escalation_creates_task_if_missing():
    ledger = CostLedger()
    event = _make_escalation()
    ledger.record_escalation("t1", event)
    task = ledger._tasks["t1"]
    assert task.tier == "a"


def test_record_escalation_appends_event():
    ledger = CostLedger()
    event = _make_escalation()
    ledger.record_escalation("t1", event)
    assert len(ledger._tasks["t1"].escalations) == 1


def test_finalize_task_computes_total_cost():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call(cost_in=1, cost_out=2))
    ledger.record_call("t1", _make_call(cost_in=3, cost_out=4))
    task = ledger.finalize_task("t1", "code", "w")
    assert task.total_cost == Decimal("10")  # 1+2 + 3+4


def test_finalize_task_sets_finalized():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call())
    task = ledger.finalize_task("t1", "code", "w")
    assert task.finalized is True


def test_finalize_task_evicts_lock():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call())
    ledger.finalize_task("t1", "code", "w")
    assert "t1" not in ledger._locks


def test_finalize_task_returns_zero_for_no_calls():
    ledger = CostLedger()
    task = ledger.finalize_task("t1", "code", "a")
    assert task.total_cost == Decimal("0")
    assert task.finalized is True


def test_cost_per_task_returns_task():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call())
    task = ledger.cost_per_task("t1")
    assert task is not None
    assert task.task_id == "t1"


def test_cost_per_task_returns_none():
    ledger = CostLedger()
    assert ledger.cost_per_task("nonexistent") is None


def test_cost_report_only_finalized_tasks():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call(task_id="t1"))
    ledger.finalize_task("t1", "code", "w")
    ledger.record_call("t2", _make_call(task_id="t2"))
    report = ledger.cost_report()
    assert "t1" in report.per_task
    assert "t2" not in report.per_task


def test_cost_report_aggregates_by_tier():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call(task_id="t1", tier="w", cost_in=1))
    ledger.finalize_task("t1", "code", "w")
    ledger.record_call("t2", _make_call(task_id="t2", tier="a", cost_in=2))
    ledger.finalize_task("t2", "spec", "a")
    report = ledger.cost_report()
    assert report.per_tier["w"] == Decimal("1")
    assert report.per_tier["a"] == Decimal("2")


def test_cost_report_aggregates_by_type():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call(task_id="t1", cost_in=5))
    ledger.finalize_task("t1", "code", "w")
    ledger.record_call("t2", _make_call(task_id="t2", cost_in=10))
    ledger.finalize_task("t2", "spec", "a")
    report = ledger.cost_report()
    assert report.per_type["code"] == Decimal("5")
    assert report.per_type["spec"] == Decimal("10")


def test_reset_clears_all():
    ledger = CostLedger()
    ledger.record_call("t1", _make_call())
    ledger.reset()
    assert len(ledger._tasks) == 0
    assert len(ledger._locks) == 0
