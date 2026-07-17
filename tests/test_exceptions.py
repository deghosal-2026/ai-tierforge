"""
Tests for custom exceptions.

These tests verify:
1. All custom exceptions subclass ``TierForgeError`` (so users can catch
   all ai-tierforge errors with one except clause).
2. Each exception carries its SPEC-defined fields (task_type, model,
   scope, etc.) for programmatic inspection.
3. Error messages are human-readable and contain the relevant identifiers.

No mocking needed — these are pure exception instantiation tests.
"""

from ai_tierforge.exceptions import (
    BudgetExceededError,
    ConfigError,
    ConcurrencyError,
    NoTierMatchError,
    ProviderError,
    RouterExhaustedError,
    TierForgeError,
)


def test_tier_forge_error_is_base():
    """All custom exceptions should subclass TierForgeError.

    This allows users to catch all ai-tierforge errors with a single
    ``except TierForgeError`` clause.
    """
    assert issubclass(ConfigError, TierForgeError)
    assert issubclass(NoTierMatchError, TierForgeError)
    assert issubclass(ProviderError, TierForgeError)
    assert issubclass(RouterExhaustedError, TierForgeError)
    assert issubclass(BudgetExceededError, TierForgeError)
    assert issubclass(ConcurrencyError, TierForgeError)


def test_config_error_message():
    """ConfigError should format all errors into the message string.

    Multiple errors are joined with ", " for readability.
    """
    err = ConfigError(["empty tiers", "unknown provider"])
    assert "empty tiers" in str(err)
    assert "unknown provider" in str(err)


def test_config_error_stores_errors():
    """ConfigError should expose the errors list for programmatic access.

    Users can iterate over ``err.errors`` to display each error
    individually in a UI or CI pipeline.
    """
    errors = ["error1", "error2"]
    err = ConfigError(errors)
    assert err.errors == errors


def test_no_tier_match_error():
    """NoTierMatchError should store and display the task_type.

    This helps users understand which task_type wasn't matched —
    usually a typo or a missing tier in the YAML config.
    """
    err = NoTierMatchError("code")
    assert err.task_type == "code"
    assert "code" in str(err)


def test_provider_error():
    """ProviderError should store model and error strings.

    The message format is "provider '{model}' failed: {error}".
    """
    err = ProviderError("test-model", "timeout")
    assert err.model == "test-model"
    assert err.error == "timeout"
    assert "test-model" in str(err)


def test_router_exhausted_error_no_escalations():
    """RouterExhaustedError should handle an empty escalation trace.

    When no escalations occurred (all retries failed on the first tier),
    the message should say "no escalations".
    """
    err = RouterExhaustedError("task-123", [])
    assert err.task_id == "task-123"
    assert "no escalations" in str(err)


def test_router_exhausted_error_with_trace():
    """RouterExhaustedError should format the escalation trace.

    The trace is formatted as "from_tier→to_tier" for each event,
    joined with " → ".  This shows the full escalation path for debugging.
    """
    from ai_tierforge.types import EscalationEvent, EscalationCause
    trace = [
        EscalationEvent(
            task_id="task-123", task_type="code",
            from_tier="workhorse", to_tier="architect",
            cause=EscalationCause.RETRY_EXCEEDED,
        ),
    ]
    err = RouterExhaustedError("task-123", trace)
    assert "workhorse→architect" in str(err)


def test_budget_exceeded_error():
    """BudgetExceededError should store scope and reason.

    The scope identifies which budget was breached (e.g. "team:payments"
    or a task_id), and the reason explains which limit was hit.
    """
    err = BudgetExceededError("team:payments", "daily limit exceeded")
    assert err.scope == "team:payments"
    assert err.reason == "daily limit exceeded"
    assert "team:payments" in str(err)


def test_concurrency_error():
    """ConcurrencyError should store the task_id that couldn't acquire a lock.

    This indicates severe lock contention or a deadlock in the cost
    ledger — should be extremely rare in practice.
    """
    err = ConcurrencyError("task-456")
    assert err.task_id == "task-456"
    assert "task-456" in str(err)
