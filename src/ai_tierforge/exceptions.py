"""
Custom exceptions for ai-tierforge.

All exceptions inherit from ``TierForgeError`` so users can catch
ai-tierforge-specific errors with a single ``except`` clause::

    try:
        router.route("code", prompt)
    except TierForgeError as e:
        # Handles NoTierMatchError, BudgetExceededError, etc.
        handle_error(e)

Each exception carries structured fields (not just a message string)
so calling code can inspect the error programmatically.
"""


class TierForgeError(Exception):
    """Base exception for all ai-tierforge errors.

    All other exceptions in this module subclass this, so catching
    ``TierForgeError`` catches every ai-tierforge-specific error.
    """


class ConfigError(TierForgeError):
    """Invalid YAML config or schema validation failure.

    Raised by ``TierForgeConfigLoader`` when the YAML config has one
    or more validation errors.  The ``errors`` list contains every
    error message so users can fix all issues in one pass.

    Attributes:
        errors: List of human-readable error strings.
    """

    def __init__(self, errors: list[str]):
        self.errors = errors
        # Join all errors into a single message for the exception repr
        super().__init__(f"config errors: {', '.join(errors)}")


class NoTierMatchError(TierForgeError):
    """No tier handles the given task_type.

    Raised by ``TierRouter.tier_for_task()`` when no tier's ``use_for``
    list contains the requested task_type.  This usually means the
    YAML config is missing a tier for that task type.

    Attributes:
        task_type: The task_type that couldn't be matched.
    """

    def __init__(self, task_type: str):
        self.task_type = task_type
        super().__init__(f"no tier matches task_type '{task_type}'")


class ProviderError(TierForgeError):
    """Upstream provider failure after all retries.

    Raised when a provider adapter exhausts its retry attempts and
    the call still fails.  This is distinct from ``RouterExhaustedError``
    (which means *all tiers* were exhausted, not just one provider).

    Attributes:
        model: The model that failed.
        error: The error string from the provider.
    """

    def __init__(self, model: str, error: str):
        self.model = model
        self.error = error
        super().__init__(f"provider '{model}' failed: {error}")


class RouterExhaustedError(TierForgeError):
    """All retries and escalations exhausted — task could not be completed.

    Raised by ``TierRouter.route()`` when the total retry count across
    all tiers reaches ``RouterConfig.max_retries`` and no tier succeeded.
    The escalation trace shows the full path of tier-to-tier escalations
    attempted, which is useful for debugging why a task kept failing.

    Attributes:
        task_id:            The task that could not be completed.
        escalation_trace:   List of ``EscalationEvent`` objects showing
                            the escalation path (e.g. workhorse→architect).
    """

    def __init__(self, task_id: str, escalation_trace: list):
        self.task_id = task_id
        self.escalation_trace = escalation_trace
        # Format the trace as "workhorse→architect → architect→premium"
        # or "no escalations" if the trace is empty
        trace_summary = " → ".join(
            f"{e.from_tier}→{e.to_tier}" for e in escalation_trace
        ) or "no escalations"
        super().__init__(
            f"router exhausted for task '{task_id}': {trace_summary}"
        )


class BudgetExceededError(TierForgeError):
    """Hard stop triggered — budget limit exceeded.

    Raised by the router when ``BudgetEnforcer.check()`` returns
    ``HARD_STOP``.  The call is rejected entirely; no model call is
    made.  The scope and reason indicate which budget was breached.

    Attributes:
        scope:  The budget scope that exceeded its limit
                (e.g. "team:payments" or a task_id).
        reason: Human-readable explanation of which limit was hit.
    """

    def __init__(self, scope: str, reason: str):
        self.scope = scope
        self.reason = reason
        super().__init__(f"budget exceeded for scope '{scope}': {reason}")


class ConcurrencyError(TierForgeError):
    """Cost ledger lock timeout.

    Raised when a thread waits too long to acquire the per-task lock
    in the cost ledger.  This should be extremely rare in practice —
    it indicates severe lock contention or a deadlock.

    Attributes:
        task_id: The task that couldn't acquire the lock.
    """

    def __init__(self, task_id: str):
        self.task_id = task_id
        super().__init__(f"concurrency timeout on task '{task_id}'")
