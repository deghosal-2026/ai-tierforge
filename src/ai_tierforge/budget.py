"""
Budget enforcer — per-scope budget checks with auto-downgrade.

The budget enforcer tracks spend across three scopes (per_task,
per_day, per_project) and enforces configurable actions when limits
are exceeded:

- **WARN**: Log a warning but allow the call to proceed.
- **DOWNGRADE**: Automatically drop to a cheaper tier
  (architect → workhorse → utility).
- **HARD_STOP**: Reject the call entirely (raises BudgetExceededError).

When multiple budgets are configured, the enforcer returns the *most
restrictive* action found (HARD_STOP > DOWNGRADE > WARN > allowed).

Thread safety:
- Per-scope ``threading.Lock`` prevents race conditions when multiple
  agents record spend concurrently.
- A global lock protects the lock-creation path.

Downgrade chain
---------------
The downgrade direction is the opposite of escalation::

    tier_order = ["architect", "workhorse", "utility"]
                   ↑ index 0      ↑ index 1      ↑ index 2

- Escalation (up):   move to lower index (workhorse → architect)
- Downgrade (down):  move to higher index (architect → workhorse)
"""

from collections import defaultdict
from decimal import Decimal
import threading

from ai_tierforge.types import (
    BudgetCheck,
    BudgetsConfig,
    OnExceedAction,
    ScopeId,
    TierName,
)


class BudgetEnforcer:
    """Enforces per-scope budgets with configurable exceed actions.

    Supports per_task, per_day, and per_project budget scopes.
    Each scope can have a different ``on_exceed`` action.
    """

    def __init__(
        self,
        config: BudgetsConfig,
        tier_order: list[TierName],
    ) -> None:
        """Initialise the enforcer with budget config and tier ordering.

        Args:
            config:     Budget limits and actions for each scope.
            tier_order: Tier names ordered by priority (highest first).
                        Used to compute downgrade targets.
        """
        self._config = config
        self._tier_order = tier_order
        # scope → {period → spend} — defaultdict auto-creates the
        # inner dict with all periods zeroed on first access
        self._spend: dict[ScopeId, dict[str, Decimal]] = defaultdict(
            lambda: {"per_task": Decimal("0"), "per_day": Decimal("0"),
                      "per_project": Decimal("0")}
        )
        # scope → current tier name (for DOWNGRADE targeting)
        self._current_tiers: dict[ScopeId, TierName] = {}
        self._locks: dict[ScopeId, threading.Lock] = {}
        self._global_lock = threading.Lock()

    def _get_lock(self, scope: ScopeId) -> threading.Lock:
        """Get or create a per-scope lock.

        Uses the global lock to prevent a race condition where two
        threads create separate locks for the same scope simultaneously.

        Args:
            scope: The budget scope to get a lock for.

        Returns:
            A ``threading.Lock`` specific to this scope.
        """
        with self._global_lock:
            if scope not in self._locks:
                self._locks[scope] = threading.Lock()
            return self._locks[scope]

    def check(self, scope: ScopeId) -> BudgetCheck:
        """Check all budget limits for the given scope.

        Evaluates per_task, per_day, and per_project budgets (whichever
        are configured) and returns the most restrictive action found.

        Action priority: HARD_STOP > DOWNGRADE > WARN > allowed.

        If DOWNGRADE is the result, ``new_tier`` is populated with the
        downgrade target (one tier lower than the current tier for
        this scope).

        Args:
            scope: The budget scope to check (e.g. "team:payments"
                   or a task_id).

        Returns:
            ``BudgetCheck`` with the action to take and optional
            ``new_tier`` for DOWNGRADE.
        """
        with self._get_lock(scope):
            usage = self._spend[scope]
            # Start with "everything is fine"
            most_restrictive = BudgetCheck(
                allowed=True,
                action=OnExceedAction.WARN,
                reason="within budget",
            )

            # Check each configured budget scope
            checks = [
                ("per_task", self._config.per_task),
                ("per_day", self._config.per_day),
                ("per_project", self._config.per_project),
            ]

            for period, bc in checks:
                if bc is None:
                    continue
                # Check if spend has reached the limit
                if usage[period] >= bc.limit:
                    if bc.on_exceed == OnExceedAction.HARD_STOP:
                        # HARD_STOP is always the most restrictive —
                        # return immediately, no need to check further
                        return BudgetCheck(
                            allowed=False,
                            action=OnExceedAction.HARD_STOP,
                            reason=f"{period} limit ${bc.limit} exceeded",
                        )
                    elif bc.on_exceed == OnExceedAction.DOWNGRADE:
                        # DOWNGRADE is more restrictive than WARN —
                        # update most_restrictive
                        most_restrictive = BudgetCheck(
                            allowed=False,
                            action=OnExceedAction.DOWNGRADE,
                            reason=f"{period} limit ${bc.limit} exceeded",
                            new_tier=self.downgrade_tier(
                                self._current_tier_for_scope(scope)
                            ),
                        )
                    elif bc.on_exceed == OnExceedAction.WARN:
                        # WARN is the least restrictive — only update
                        # if we haven't already found a DOWNGRADE
                        if most_restrictive.action == OnExceedAction.WARN:
                            most_restrictive = BudgetCheck(
                                allowed=True,
                                action=OnExceedAction.WARN,
                                reason=f"{period} limit ${bc.limit} exceeded",
                            )

            return most_restrictive

    def record_spend(self, scope: ScopeId, amount: Decimal, tier: TierName = "") -> None:
        with self._get_lock(scope):
            usage = self._spend[scope]
            for period in ("per_task", "per_day", "per_project"):
                usage[period] += amount
            if tier:
                self._current_tiers[scope] = tier

    def reset_period(self, scope: ScopeId) -> None:
        """Reset per-day accumulators for a scope.

        Called by the CLI (``ai-tierforge budget reset``) or an
        external scheduler to reset daily budgets at midnight.

        Args:
            scope: The budget scope to reset.
        """
        with self._get_lock(scope):
            self._spend[scope]["per_day"] = Decimal("0")

    def downgrade_tier(self, current_tier: TierName) -> TierName:
        """Move to the next lower priority tier (higher index).

        Downgrade chain: architect → workhorse → utility → utility
        (stays at the lowest tier if already there).

        This is the opposite direction of escalation:
        - Escalation moves to a *lower* index (higher priority tier)
        - Downgrade moves to a *higher* index (lower priority tier)

        Args:
            current_tier: The tier to downgrade from.

        Returns:
            The next lower-priority tier, or the same tier if already
            at the lowest.
        """
        if current_tier not in self._tier_order:
            # Unknown tier — default to the lowest priority
            return self._tier_order[-1] if self._tier_order else current_tier
        idx = self._tier_order.index(current_tier)
        # If already at the last index (lowest priority), stay
        if idx >= len(self._tier_order) - 1:
            return current_tier
        # Move to the next index (lower priority)
        return self._tier_order[idx + 1]

    def current_usage(self, scope: ScopeId) -> dict:
        """Return current spend and limits for a scope.

        Used by the CLI (``ai-tierforge budget check``) to display
        budget status to the user.

        Args:
            scope: The budget scope to query.

        Returns:
            Dict with keys like ``per_task_spend``, ``per_task_limit``,
            ``per_task_remaining`` for each configured budget scope.
            Only includes scopes that have a configured limit.
        """
        with self._get_lock(scope):
            usage = self._spend[scope]
            result = {}
            for period, bc in [
                ("per_task", self._config.per_task),
                ("per_day", self._config.per_day),
                ("per_project", self._config.per_project),
            ]:
                if bc is not None:
                    result[f"{period}_spend"] = usage[period]
                    result[f"{period}_limit"] = bc.limit
                    result[f"{period}_remaining"] = bc.limit - usage[period]
            return result

    def _current_tier_for_scope(self, scope: ScopeId) -> TierName:
        return self._current_tiers.get(scope, "architect")
