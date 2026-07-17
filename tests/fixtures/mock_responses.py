"""
Mock provider response data for tests.

These helper functions return pre-built ModelCall objects for use
in tests that need specific response shapes without calling a real
provider.  They're used by integration tests and other test modules
that need realistic ModelCall data.

Usage::

    from tests.fixtures.mock_responses import mock_success_response

    call = mock_success_response(model="glm-5.2")
    assert call.success is True
    assert call.tokens_in == 100
"""

from ai_tierforge.types import ModelCall
from decimal import Decimal


def mock_success_response(model: str = "deepseek-v4-flash") -> ModelCall:
    """Return a successful mock ModelCall with realistic token counts.

    Args:
        model: Model name to use (default: deepseek-v4-flash).

    Returns:
        A ModelCall with success=True, 100 input tokens, 50 output tokens,
        and small non-zero costs for testing cost aggregation.
    """
    return ModelCall(
        task_id="mock-task-id",
        task_type="code",
        tier="workhorse",
        model=model,
        prompt="test prompt",
        response="mock successful response",
        tokens_in=100,
        tokens_out=50,
        # Small costs for testing cost aggregation
        cost_in=Decimal("0.000014"),
        cost_out=Decimal("0.000014"),
        duration_ms=500,
        success=True,
    )


def mock_failure_response(
    error: str = "timeout",
    model: str = "deepseek-v4-flash",
) -> ModelCall:
    """Return a failed mock ModelCall for testing retry/escalation paths.

    Args:
        error: Error string to set (default: "timeout").
        model: Model name to use (default: deepseek-v4-flash).

    Returns:
        A ModelCall with success=False, the specified error, and
        partial input token cost (input tokens were consumed before
        the failure).
    """
    return ModelCall(
        task_id="mock-task-id",
        task_type="code",
        tier="workhorse",
        model=model,
        prompt="test prompt",
        response=None,
        # Input tokens were consumed before the failure
        tokens_in=50,
        tokens_out=0,
        cost_in=Decimal("0.000007"),
        cost_out=Decimal("0"),
        duration_ms=3000,
        success=False,
        error=error,
    )
