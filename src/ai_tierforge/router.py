"""
TierRouter — core routing, retry, escalation, and cost tracking.

This is the central component of ai-tierforge.  It sits between the
user's agent code and the LLM provider, deciding which tier to send
each call to, handling retries and escalations, enforcing budgets,
and recording costs.

Data flow (simplified)::

    agent → router.route("code", prompt)
                ↓
         tier_for_task("code") → workhorse
                ↓
         budget_enforcer.check(scope) → allowed
                ↓
         adapter.call(model, prompt) → ModelCall
                ↓
         cost_ledger.record_call() + budget_enforcer.record_spend()
                ↓
         success? → finalize_task() → return ModelCall
         failure? → should_escalate()? → escalate or retry
                ↓
         all retries exhausted → RouterExhaustedError
"""

from decimal import Decimal
import logging
import uuid
from pathlib import Path
from typing import Optional, Union

from ai_tierforge.adapters.base import ProviderAdapter
from ai_tierforge.config import TierForgeConfigLoader
from ai_tierforge.cost import CostLedger
from ai_tierforge.slo import EscalationTracker, RoutingLogger
from ai_tierforge.budget import BudgetEnforcer
from ai_tierforge.adapters.openai_compat import OpenAICompatAdapter
from ai_tierforge.omlx import OMLXAdapter
from ai_tierforge.exceptions import (
    BudgetExceededError,
    NoTierMatchError,
    RouterExhaustedError,
)
from ai_tierforge.types import (
    CostReport,
    EscalationCause,
    EscalationEvent,
    ModelCall,
    OnExceedAction,
    RouteDecisionType,
    RouteLogEntry,
    ScopeId,
    TaskId,
    TierConfig,
    TierForgeConfig,
    TierName,
)

logger = logging.getLogger(__name__)


class TierRouter:
    """Routes LLM calls to the appropriate tier based on task type.

    The router is the main entry point for users.  It orchestrates:
    - **Tier matching**: finds the first tier whose ``use_for`` list
      contains the requested task_type.
    - **Budget enforcement**: checks per-scope budgets before each call
      and takes action (warn / downgrade / hard stop).
    - **Retries**: retries within a tier up to ``escalation.max_retries``
      before escalating.
    - **Escalation**: moves to a higher-priority tier when the current
      tier fails repeatedly or hits a non-retryable error.
    - **Cost tracking**: records every call and escalation in the
      cost ledger for per-task cost accounting.
    - **Logging**: emits structured JSON log entries for routing
      decisions and failover events.

    The router itself is stateless — all mutable state lives in the
    cost ledger, budget enforcer, escalation tracker, and routing
    logger components.
    """

    def __init__(
        self,
        config: TierForgeConfig,
        adapters: Optional[dict[str, ProviderAdapter]] = None,
    ) -> None:
        """Initialise the router with config and provider adapters.

        Args:
            config:   Parsed ``TierForgeConfig`` from YAML.
            adapters: Dict mapping provider names to adapter instances.
                      If None, defaults to built-in OpenAI-compatible
                      and OMLX adapters.  Users can pass custom adapters
                      for non-standard providers.

        Note:
            The ``tier_order`` (derived from config.tiers insertion
            order) is shared by the escalation tracker and budget
            enforcer so they agree on tier priority.
        """
        self._config = config
        tier_order = list(config.tiers.keys())

        # Default adapters if none provided — users can override to
        # inject custom adapters or mock adapters for testing.
        # If tiers specify endpoint/api_key_env, use them to configure
        # the openai-compatible adapter (so CLI users can point at Zen
        # or any non-OpenAI gateway via YAML config — BUG-035).
        if adapters is None:
            # Collect endpoint/api_key_env from the first tier that
            # uses the openai-compatible provider
            oai_endpoint = "https://api.openai.com/v1"
            oai_api_key_env = "OPENAI_API_KEY"
            for tier in config.tiers.values():
                if tier.provider == "openai-compatible":
                    if tier.endpoint:
                        oai_endpoint = tier.endpoint
                    if tier.api_key_env:
                        oai_api_key_env = tier.api_key_env
                    break
            adapters = {
                "openai-compatible": OpenAICompatAdapter(  # type: ignore[type-abstract]
                    endpoint=oai_endpoint,
                    api_key_env=oai_api_key_env,
                    pricing=config.pricing or None,
                ),
                "omlx": OMLXAdapter(),  # type: ignore[type-abstract]
            }
        self._adapters: dict[str, ProviderAdapter] = adapters

        # Internal components — each handles one concern
        self._cost_ledger = CostLedger()
        self._budget_enforcer = BudgetEnforcer(config.budgets, tier_order)
        self._escalation_tracker = EscalationTracker(
            config.escalation, tier_order
        )
        self._routing_logger = RoutingLogger(config.logging)

        for name, adapter in self._adapters.items():
            if not adapter.check_available():
                logger.warning("adapter '%s' is not available", name)

    @classmethod
    def from_yaml(
        cls,
        config_path: Union[str, Path],
        adapters: Optional[dict[str, ProviderAdapter]] = None,
    ) -> "TierRouter":
        """Create a TierRouter from a YAML config file.

        Convenience class method that loads and parses the YAML in
        one step.  Equivalent to::

            config = TierForgeConfigLoader.from_yaml(path)
            router = TierRouter(config, adapters)

        Args:
            config_path: Path to the YAML config file.
            adapters:    Optional adapter overrides (see __init__).
        """
        config = TierForgeConfigLoader.from_yaml(config_path)
        return cls(config, adapters)

    def tier_for_task(
        self, task_type: str
    ) -> tuple[TierName, TierConfig]:
        """Find the first tier whose use_for list contains task_type.

        Iterates tiers in YAML insertion order and returns the first
        match.  If multiple tiers claim the same task_type, the first
        one wins (a warning is emitted at config validation time).

        Args:
            task_type: The task type label to match (e.g. "code").

        Returns:
            Tuple of (tier_name, TierConfig).

        Raises:
            NoTierMatchError: If no tier handles this task_type.
        """
        for name, tier in self._config.tiers.items():
            if task_type in tier.use_for:
                return (name, tier)
        raise NoTierMatchError(task_type)

    def route(
        self,
        task_type: str,
        prompt: str,
        task_id: Optional[TaskId] = None,
        scope: Optional[ScopeId] = None,
        **kwargs,
    ) -> ModelCall:
        """Route a prompt to the appropriate tier and return the result.

        This is the main call path.  It:
        1. Generates a task_id if not provided.
        2. Matches the tier via ``tier_for_task``.
        3. Enters a retry/escalation loop:
           a. Checks budget before each call (HARD_STOP → raise,
              DOWNGRADE → switch tier).
           b. Dispatches to the provider adapter.
           c. Records the call cost in the ledger and budget enforcer.
           d. On success: finalises the task and returns.
           e. On failure: checks ``should_escalate`` or per-tier retry
              limit, then escalates to a higher tier or retries.
        4. If all retries across all tiers are exhausted, raises
           ``RouterExhaustedError``.

        Args:
            task_type: Task type label (e.g. "code", "spec").
            prompt:    The prompt text to send to the model.
            task_id:   Optional task identifier.  Auto-generated (uuid4)
                       if not provided.  Pass explicitly to group
                       multiple calls under the same task for cost
                       aggregation.
            scope:     Optional budget scope (e.g. "team:payments").
                       If not provided, defaults to the task_id.
            **kwargs:  Additional arguments passed through to the
                       provider adapter's ``call()`` method (e.g.
                       temperature, top_p).

        Returns:
            ``ModelCall`` with the response and cost data.

        Raises:
            NoTierMatchError:      If no tier matches task_type.
            BudgetExceededError:   If budget enforcement returns HARD_STOP.
            RouterExhaustedError:  If all retries and escalations are
                                   exhausted without success.
        """
        # Generate a unique task ID if the caller didn't provide one
        task_id = task_id or uuid.uuid4().hex
        tier_name, tier_config = self.tier_for_task(task_type)

        # Log the initial routing decision — this is an intentional
        # ROUTE, not a FAILOVER
        self._routing_logger.log_route(RouteLogEntry(
            task_id=task_id,
            tier=tier_name,
            model=tier_config.model,
            decision=RouteDecisionType.ROUTE,
            reason=f"matched task_type '{task_type}'",
        ))

        self._escalation_tracker.record_task(task_id, task_type, tier_name)

        current_tier = tier_name
        current_config = tier_config
        tier_retries = 0
        total_attempts = 0
        max_total = self._config.router.max_retries
        downgraded = False

        while total_attempts < max_total:
            total_attempts += 1
            # ── Budget check before each call ────────────────────────
            # The scope key is the provided scope, or the task_id if
            # no scope was given (per-task budget enforcement).
            # Skip budget check if we already downgraded for this scope
            # — otherwise DOWNGRADE fires on every loop iteration and
            # cycles through all tiers until exhausted (BUG-037).
            scope_key = scope or task_id
            if not downgraded:
                budget_check = self._budget_enforcer.check(scope_key)

                # HARD_STOP: reject immediately, no model call is made
                if budget_check.action == OnExceedAction.HARD_STOP:
                    raise BudgetExceededError(scope_key, budget_check.reason)

                # DOWNGRADE: switch to a cheaper tier before calling
                if budget_check.action == OnExceedAction.DOWNGRADE:
                    new_tier = budget_check.new_tier
                    if new_tier and new_tier in self._config.tiers:
                        # Log as a FAILOVER since it's forced by budget
                        self._routing_logger.log_failover(RouteLogEntry(
                            task_id=task_id,
                            tier=new_tier,
                            model=self._config.tiers[new_tier].model,
                            decision=RouteDecisionType.FAILOVER,
                            reason=f"budget: {budget_check.reason}",
                        ))
                        current_tier = new_tier
                        current_config = self._config.tiers[new_tier]
                        downgraded = True

            # ── Dispatch to the provider adapter ─────────────────────
            adapter = self._adapters.get(current_config.provider)
            if adapter is None:
                raise ValueError(
                    f"no adapter registered for provider "
                    f"'{current_config.provider}'"
                )

            # Make the actual LLM call through the adapter
            call_result = adapter.call(
                model=current_config.model,
                prompt=prompt,
                max_tokens=current_config.max_tokens,
                **kwargs,
            )

            # Enrich the ModelCall with routing metadata — the adapter
            # doesn't know the task_id, tier, or attempt number, so we
            # set them here after the call returns.
            call_result.task_id = task_id
            call_result.task_type = task_type
            call_result.tier = current_tier
            call_result.attempt = total_attempts - 1

            # ── Calculate real cost via the adapter ──────────────────
            # The adapter's call() returns cost_in/cost_out=0.  We
            # compute the actual cost from token counts and pricing.
            cost_in, cost_out = adapter.calculate_cost(
                model=current_config.model,
                tokens_in=call_result.tokens_in,
                tokens_out=call_result.tokens_out,
            )
            call_result.cost_in = cost_in
            call_result.cost_out = cost_out

            # ── Record cost ──────────────────────────────────────────
            # Every call (success or failure) costs money, so we record
            # it in both the cost ledger and the budget enforcer.
            self._cost_ledger.record_call(task_id, call_result)
            call_cost = call_result.cost_in + call_result.cost_out
            self._budget_enforcer.record_spend(scope_key, call_cost, current_tier)

            # ── Success: finalise and return ─────────────────────────
            if call_result.success:
                self._cost_ledger.finalize_task(
                    task_id, task_type, current_tier
                )
                # Log the successful completion as a ROUTE entry
                self._routing_logger.log_route(RouteLogEntry(
                    task_id=task_id,
                    tier=current_tier,
                    model=current_config.model,
                    decision=RouteDecisionType.ROUTE,
                    reason="task completed",
                ))
                return call_result

            # ── Failure: decide whether to retry or escalate ─────────
            tier_retries += 1

            # Escalate if:
            # - The error is non-retryable (should_escalate returns True)
            # - OR we've exhausted per-tier retries (tier_retries >= max_retries)
            if (
                TierRouter.should_escalate(call_result.error)
                or tier_retries >= self._config.escalation.max_retries
            ):
                # Find the next higher-priority tier (lower priority number)
                next_tier = self._escalation_tracker.next_tier(current_tier)

                # If we're already at the highest tier, we can't escalate
                if next_tier == current_tier:
                    break  # exit loop → RouterExhaustedError

                # Map error to escalation cause
                cause = self._map_cause(call_result.error, tier_retries)

                # Compute actual cost before escalation from the ledger
                task_cost = self._cost_ledger.cost_per_task(task_id)
                cost_before = Decimal("0")
                if task_cost is not None:
                    cost_before = sum(
                        (c.cost_in + c.cost_out for c in task_cost.calls),
                        Decimal("0"),
                    )

                event = EscalationEvent(
                    task_id=task_id,
                    task_type=task_type,
                    from_tier=current_tier,
                    to_tier=next_tier,
                    cause=cause,
                    failure_count=tier_retries,
                    cost_before_escalation=cost_before,
                )

                # Record the escalation in both the tracker (for SLO
                # rate calculations) and the cost ledger (for per-task
                # history)
                self._escalation_tracker.record(event)
                self._cost_ledger.record_escalation(task_id, event)

                # Log the escalation as a FAILOVER event
                self._routing_logger.log_failover(RouteLogEntry(
                    task_id=task_id,
                    tier=next_tier,
                    model=self._config.tiers[next_tier].model,
                    decision=RouteDecisionType.FAILOVER,
                    reason=f"escalation: {call_result.error}",
                ))

                # Switch to the new tier and reset both retry counters
                # — the new tier gets its own full retry budget (BUG-036:
                # previously total_attempts was not reset, so the escalated
                # tier inherited the exhausted global budget and never
                # got to make a single call).
                current_tier = next_tier
                current_config = self._config.tiers[current_tier]
                tier_retries = 0
                total_attempts = 0

        # ── All retries exhausted ────────────────────────────────────
        # If we reach here, every tier has been tried and none succeeded.
        # Raise with the full escalation trace for debugging.
        raise RouterExhaustedError(
            task_id,
            self._escalation_tracker.trace(task_id),
        )

    def budget_check(self, scope: str) -> dict:
        return self._budget_enforcer.current_usage(scope)

    def budget_reset(self, scope: str) -> None:
        self._budget_enforcer.reset_period(scope)

    def cost_report(self) -> CostReport:
        """Return the current cost report from the ledger.

        The report aggregates costs by task, tier, and task type,
        and supports computing escalation rates per task type.

        Returns:
            ``CostReport`` with per_task, per_tier, and per_type dicts.
        """
        return self._cost_ledger.cost_report()

    @staticmethod
    def should_escalate(error: Optional[str]) -> bool:
        """Determine if an error warrants immediate escalation vs retry.

        Some errors are non-retryable — retrying the same model won't
        help, so we should escalate to a higher tier immediately.
        Other errors are transient (timeouts, 5xx) and may succeed
        on retry, so we retry first and escalate only after the
        per-tier retry limit is hit.

        Immediate-escalate errors:
        - ``content_too_long``: response exceeded max_tokens
        - ``context_length_exceeded``: input too long for the model
        - ``rate_limit_exceeded``: won't resolve on retry anytime soon

        Retryable errors (return False — retry first, escalate later):
        - ``timeout``, ``connection_error``, ``5xx``, ``internal_error``

        Args:
            error: Error string from the ModelCall, or None.

        Returns:
            True if the router should escalate immediately,
            False if it should retry first.
        """
        if error is None:
            return False
        immediate_escalate = [
            "content_too_long",
            "context_length_exceeded",
            "rate_limit_exceeded",
        ]
        lower = error.lower()
        return any(s in lower for s in immediate_escalate)

    @staticmethod
    def _map_cause(error: Optional[str], tier_retries: int) -> EscalationCause:
        if tier_retries >= 3:
            return EscalationCause.RETRY_EXCEEDED
        if error is None:
            return EscalationCause.PROVIDER_ERROR
        lower = error.lower()
        if "timeout" in lower:
            return EscalationCause.TIMEOUT
        if "content_too_long" in lower:
            return EscalationCause.CONTENT_TOO_LONG
        if "rate_limit" in lower:
            return EscalationCause.PROVIDER_ERROR
        return EscalationCause.PROVIDER_ERROR
