import json
import tempfile


from ai_tierforge.slo import EscalationTracker, RoutingLogger
from ai_tierforge.types import (
    EscalationCause,
    EscalationConfig,
    EscalationEvent,
    LoggingConfig,
    RouteDecisionType,
    RouteLogEntry,
)

# ─── Helpers ────────────────────────────────────────────────────────────────


def _event(task_id="t1", task_type="code", from_tier="w", to_tier="a"):
    return EscalationEvent(
        task_id=task_id, task_type=task_type,
        from_tier=from_tier, to_tier=to_tier,
        cause=EscalationCause.RETRY_EXCEEDED,
    )


def _entry(task_id="t1", tier="w", model="m", decision=RouteDecisionType.ROUTE,
           reason="test"):
    return RouteLogEntry(
        task_id=task_id, tier=tier, model=model,
        decision=decision, reason=reason,
    )


# ─── EscalationTracker ──────────────────────────────────────────────────────


def test_record_task_tracks_by_type():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record_task("t1", "code", "w")
    assert t._total_tasks_by_type["code"] == {"t1"}


def test_record_task_tracks_by_tier():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record_task("t1", "code", "w")
    assert t._total_tasks_by_tier["w"] == {"t1"}


def test_record_appends_event():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record(_event())
    assert len(t._events) == 1


def test_escalation_rate_by_task_type():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record_task("t1", "code", "w")
    t.record_task("t2", "code", "w")
    t.record(_event(task_id="t1"))
    assert t.escalation_rate("code") == 0.5


def test_escalation_rate_no_tasks():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record(_event(task_id="t1"))
    assert t.escalation_rate("code") == 0.0


def test_escalation_rate_by_tier_fallback():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record_task("t1", "code", "w")
    t.record(_event(task_id="t1", from_tier="w"))
    assert t.escalation_rate("w") == 1.0


def test_escalation_rate_unknown_key():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    assert t.escalation_rate("unknown") == 0.0


def test_threshold_breached_true():
    t = EscalationTracker(EscalationConfig(default_threshold=0.10), ["a", "w"])
    t.record_task("t1", "code", "w")
    t.record(_event(task_id="t1"))
    assert t.threshold_breached("code") is True


def test_threshold_breached_false():
    t = EscalationTracker(EscalationConfig(default_threshold=0.99), ["a", "w"])
    t.record_task("t1", "code", "w")
    t.record_task("t2", "code", "w")
    t.record(_event(task_id="t1"))
    # rate=1/2=0.5, threshold=0.99, 0.5 > 0.99 → False → not breached
    assert t.threshold_breached("code") is False


def test_threshold_breached_uses_per_tier_override():
    t = EscalationTracker(
        EscalationConfig(default_threshold=0.10, per_tier={"w": 0.99}),
        ["a", "w"],
    )
    t.record_task("t1", "code", "w")
    t.record(_event(task_id="t1", from_tier="w"))
    # key="code" uses default 0.10, rate=1.0 > 0.10 → True
    assert t.threshold_breached("code") is True
    # key="w" uses per_tier 0.99, rate=1.0 > 0.99 → True
    assert t.threshold_breached("w") is True


def test_next_tier_moves_up():
    t = EscalationTracker(EscalationConfig(), ["a", "w", "u"])
    assert t.next_tier("w") == "a"
    assert t.next_tier("u") == "w"


def test_next_tier_stays_at_top():
    t = EscalationTracker(EscalationConfig(), ["a", "w", "u"])
    assert t.next_tier("a") == "a"


def test_next_tier_raises_on_unknown():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    try:
        t.next_tier("unknown")
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_trace_returns_events_for_task():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    t.record(_event(task_id="t1"))
    t.record(_event(task_id="t2"))
    t.record(_event(task_id="t1"))
    trace = t.trace("t1")
    assert len(trace) == 2


def test_trace_empty_when_no_events():
    t = EscalationTracker(EscalationConfig(), ["a", "w"])
    assert t.trace("t1") == []


# ─── RoutingLogger ──────────────────────────────────────────────────────────


def test_log_route_appends_to_buffer():
    rl = RoutingLogger(LoggingConfig(routing=True))
    rl.log_route(_entry())
    assert len(rl._routes) == 1


def test_log_route_buffers_even_when_disabled():
    rl = RoutingLogger(LoggingConfig(routing=False))
    rl.log_route(_entry())
    assert len(rl._routes) == 1


def test_log_failover_appends_to_buffer():
    rl = RoutingLogger(LoggingConfig(failover=True))
    rl.log_failover(_entry(decision=RouteDecisionType.FAILOVER))
    assert len(rl._failovers) == 1


def test_log_failover_buffers_even_when_disabled():
    rl = RoutingLogger(LoggingConfig(failover=False))
    rl.log_failover(_entry(decision=RouteDecisionType.FAILOVER))
    assert len(rl._failovers) == 1


def test_recent_routes_returns_last_n():
    rl = RoutingLogger(LoggingConfig(routing=True))
    for i in range(10):
        rl.log_route(_entry(task_id=f"t{i}"))
    recent = rl.recent_routes(3)
    assert len(recent) == 3
    assert recent[-1].task_id == "t9"


def test_recent_failovers_returns_last_n():
    rl = RoutingLogger(LoggingConfig(failover=True))
    for i in range(5):
        rl.log_failover(_entry(task_id=f"t{i}", decision=RouteDecisionType.FAILOVER))
    assert len(rl.recent_failovers(2)) == 2


def test_summary_counts():
    rl = RoutingLogger(LoggingConfig(routing=True, failover=True))
    rl.log_route(_entry())
    rl.log_route(_entry())
    rl.log_failover(_entry(decision=RouteDecisionType.FAILOVER))
    s = rl.summary()
    assert s["total_routes"] == 2
    assert s["total_failovers"] == 1
    assert s["failover_rate"] == 1 / 3


def test_summary_empty():
    rl = RoutingLogger(LoggingConfig())
    s = rl.summary()
    assert s["total_routes"] == 0
    assert s["failover_rate"] == 0.0


def test_emit_info_to_stdout(capsys):
    rl = RoutingLogger(LoggingConfig(routing=True, level="info"))
    entry = _entry(reason="hello")
    rl.log_route(entry)
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["level"] == "info"
    assert data["reason"] == "hello"
    assert "prompt" not in data or data["prompt"] is None


def test_emit_debug_includes_extra_fields(capsys):
    rl = RoutingLogger(LoggingConfig(routing=True, level="debug"))
    entry = _entry(reason="debug-test")
    entry.prompt = "hello"
    entry.tokens_in = 100
    rl.log_route(entry)
    captured = capsys.readouterr()
    data = json.loads(captured.out.strip())
    assert data["level"] == "debug"
    assert data["prompt"] == "hello"
    assert data["tokens_in"] == 100


def test_emit_to_file():
    with tempfile.NamedTemporaryFile(mode="r", suffix=".jsonl", delete=False) as f:
        path = f.name
    rl = RoutingLogger(LoggingConfig(routing=True, output=path))
    rl.log_route(_entry(reason="file-test"))
    with open(path) as f:
        data = json.loads(f.read().strip())
    assert data["reason"] == "file-test"
