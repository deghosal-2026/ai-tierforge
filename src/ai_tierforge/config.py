"""
YAML config loader with schema validation for ai-tierforge.

This module is responsible for:
1. Reading ``tiers.yaml`` from disk (``from_yaml``)
2. Parsing the raw YAML dict into typed dataclasses (``from_dict``)
3. Validating the parsed config against the schema rules (``validate``)

The validation returns a *list* of error strings rather than raising
on the first error, so users can see all issues at once and fix them
in a single edit cycle.

Usage::

    config = TierForgeConfigLoader.from_yaml("tiers.yaml")
    errors = TierForgeConfigLoader.validate(config)
    if errors:
        for e in errors:
            print(f"ERROR: {e}")
        sys.exit(1)
"""

from decimal import Decimal
from pathlib import Path
from typing import Union

import yaml

from ai_tierforge.types import (
    BudgetConfig,
    BudgetsConfig,
    EscalationConfig,
    LoggingConfig,
    OnExceedAction,
    RouterConfig,
    TierConfig,
    TierForgeConfig,
)


class TierForgeConfigLoader:
    """Loads and validates ai-tierforge YAML configuration.

    All methods are static so the class acts as a namespace — no
    instance state is needed.
    """

    @staticmethod
    def from_yaml(path: Union[str, Path]) -> TierForgeConfig:
        """Load config from a YAML file path.

        Reads the file, parses it with ``yaml.safe_load``, and delegates
        to ``from_dict`` for dataclass conversion.

        Args:
            path: Path to the YAML config file.

        Returns:
            Parsed ``TierForgeConfig``.

        Raises:
            FileNotFoundError: If the file doesn't exist.
            ValueError: If the YAML root is not a mapping (dict).
            yaml.YAMLError: If the YAML is syntactically invalid.
        """
        with open(path, "r") as f:
            data = yaml.safe_load(f)
        # Guard against non-mapping YAML (e.g. a bare string or list)
        if not isinstance(data, dict):
            raise ValueError("config must be a YAML mapping")
        return TierForgeConfigLoader.from_dict(data)

    @staticmethod
    def from_dict(data: dict) -> TierForgeConfig:
        """Parse a raw dict (from YAML) into TierForgeConfig dataclasses.

        This is where untyped YAML data becomes typed Python objects.
        Each section of the YAML is parsed independently, with defaults
        applied for missing optional fields.

        Key behaviours:
        - Tier priority is auto-assigned from dict insertion order
          (first tier = priority 0 = architect).
        - ``on_exceed`` strings are converted to ``OnExceedAction`` enum.
        - Budget limits are converted to ``Decimal`` for precision.

        Args:
            data: Raw dict from ``yaml.safe_load``.

        Returns:
            Fully populated ``TierForgeConfig``.
        """

        # ── Parse tiers ──────────────────────────────────────────────
        # Python 3.7+ dicts preserve insertion order, and PyYAML
        # preserves YAML mapping order, so the order of tiers in the
        # YAML file directly determines escalation priority.
        raw_tiers = data.get("tiers", {})
        tiers: dict[str, TierConfig] = {}
        for i, (name, tdata) in enumerate(raw_tiers.items()):
            model = tdata.get("model")
            max_tokens = tdata.get("max_tokens")
            if model is None:
                raise ValueError(f"tier '{name}': missing required field 'model'")
            if max_tokens is None:
                raise ValueError(f"tier '{name}': missing required field 'max_tokens'")
            priority = tdata.get("priority", i)
            tiers[name] = TierConfig(
                model=model,
                max_tokens=int(max_tokens),
                use_for=tdata.get("use_for", []),
                provider=tdata.get("provider", "openai-compatible"),
                endpoint=tdata.get("endpoint"),
                api_key_env=tdata.get("api_key_env"),
                priority=priority,
            )

        # ── Parse escalation config ──────────────────────────────────
        esc_data = data.get("escalation", {})
        escalation = EscalationConfig(
            default_threshold=float(esc_data.get("default_threshold", 0.30)),
            per_tier=esc_data.get("per_tier", {}),
            max_retries=int(esc_data.get("max_retries", 3)),
        )

        # ── Parse router config ──────────────────────────────────────
        router_data = data.get("router", {})
        router = RouterConfig(
            max_retries=int(router_data.get("max_retries", 3)),
        )

        # ── Parse budgets ────────────────────────────────────────────
        # Each scope (per_task, per_day, per_project) is independently
        # optional.  _parse_budget_config returns None if the section
        # is absent from the YAML.
        budget_data = data.get("budgets", {})
        budgets = BudgetsConfig(
            per_task=_parse_budget_config(budget_data.get("per_task")),
            per_day=_parse_budget_config(budget_data.get("per_day")),
            per_project=_parse_budget_config(budget_data.get("per_project")),
        )

        # ── Parse logging config ─────────────────────────────────────
        log_data = data.get("logging", {})
        logging_config = LoggingConfig(
            routing=log_data.get("routing", True),
            failover=log_data.get("failover", True),
            level=log_data.get("level", "info"),
            output=log_data.get("output", "stdout"),
        )

        # ── Parse pricing (optional) ────────────────────────────────
        # Maps model name → (cost_in_per_token, cost_out_per_token)
        # Values are Decimals for precision.  Merged with the adapter's
        # built-in DEFAULT_PRICING at runtime.
        raw_pricing = data.get("pricing", {})
        pricing: dict[str, tuple[Decimal, Decimal]] = {}
        for model, pdata in raw_pricing.items():
            cost_in = Decimal(str(pdata.get("input", 0)))
            cost_out = Decimal(str(pdata.get("output", 0)))
            pricing[model] = (cost_in, cost_out)

        return TierForgeConfig(
            tiers=tiers,
            escalation=escalation,
            router=router,
            budgets=budgets,
            logging=logging_config,
            pricing=pricing,
        )

    @staticmethod
    def validate(config: TierForgeConfig) -> list[str]:
        """Validate a TierForgeConfig and return a list of error messages.

        Validation rules (from SPEC §4.1):
        - tiers must not be empty (error)
        - single tier produces a warning (not an error)
        - use_for must not be empty per tier (error)
        - max_tokens must be positive (error)
        - duplicate task_type across tiers (warning)
        - default_threshold must be in [0, 1] (error)
        - max_retries must be >= 1 (error, both router and escalation)
        - budget limit must be non-negative (error)
        - on_exceed must be a valid OnExceedAction (checked at parse time)

        Returns:
            List of error/warning strings.  Empty list = valid config.
        """
        errors: list[str] = []

        # ── Tier count checks ────────────────────────────────────────
        if not config.tiers:
            errors.append("config must define at least one tier")
            # Nothing else to validate if there are no tiers
            return errors

        if len(config.tiers) < 2:
            errors.append(
                "config should define at least 2 tiers for escalation to work"
            )

        # ── Per-tier checks ──────────────────────────────────────────
        # Track which tier first claimed each task_type so we can detect
        # duplicates and report which two tiers are conflicting.
        type_claims: dict[str, str] = {}

        for name, tier in config.tiers.items():
            # use_for must not be empty — a tier with no task types is useless
            if not tier.use_for:
                errors.append(f"tier '{name}': use_for must not be empty")

            # Check for duplicate task_type claims across tiers
            for tt in tier.use_for:
                if tt in type_claims:
                    errors.append(
                        f"task_type '{tt}' is claimed by tiers "
                        f"'{type_claims[tt]}' and '{name}' — first match wins"
                    )
                else:
                    type_claims[tt] = name

            # max_tokens must be positive — zero or negative makes no sense
            if tier.max_tokens <= 0:
                errors.append(f"tier '{name}': max_tokens must be positive")

            if tier.provider not in ("openai-compatible", "omlx"):
                errors.append(
                    f"tier '{name}': unknown provider '{tier.provider}' "
                    f"(known: openai-compatible, omlx)"
                )

        # ── Escalation validation ────────────────────────────────────
        thresh = config.escalation.default_threshold
        if not (0.0 <= thresh <= 1.0):
            errors.append(
                "escalation: default_threshold must be between 0.0 and 1.0"
            )

        if config.escalation.max_retries < 1:
            errors.append("escalation: max_retries must be >= 1")

        # ── Router validation ────────────────────────────────────────
        if config.router.max_retries < 1:
            errors.append("router: max_retries must be >= 1")

        # ── Budget validation ────────────────────────────────────────
        # Check each budget scope that is configured (non-None)
        for scope_name, bc in [
            ("per_task", config.budgets.per_task),
            ("per_day", config.budgets.per_day),
            ("per_project", config.budgets.per_project),
        ]:
            if bc is not None:
                if bc.limit < 0:
                    errors.append(
                        f"budget '{scope_name}': limit must be non-negative"
                    )
                # on_exceed is already validated at parse time (ValueError
                # from OnExceedAction constructor), but we check the type
                # here for safety.
                if not isinstance(bc.on_exceed, OnExceedAction):
                    errors.append(
                        f"budget '{scope_name}': on_exceed must be "
                        "'warn', 'downgrade', or 'hard_stop'"
                    )

        return errors


def _parse_budget_config(data) -> BudgetConfig | None:
    """Parse a single budget scope config from raw YAML data.

    Helper function used by ``from_dict`` to parse each budget scope
    (per_task, per_day, per_project).  Returns None if the section is
    absent, so the corresponding BudgetsConfig field stays None.

    Args:
        data: Raw dict for this budget scope, or None if absent.

    Returns:
        ``BudgetConfig`` if data is present, else ``None``.

    Raises:
        ValueError: If ``on_exceed`` is not a valid action string.
        KeyError: If ``limit`` is missing from the data.
    """
    if data is None:
        return None
    # Convert the on_exceed string to the OnExceedAction enum.
    # This will raise ValueError for invalid strings like "invalid_option",
    # which is the intended behaviour — fail fast on bad config.
    on_exceed_str = data.get("on_exceed", "warn")
    action = OnExceedAction(on_exceed_str)
    # Convert limit to Decimal via str to avoid float imprecision
    # (e.g. Decimal(0.1) != Decimal("0.1"))
    return BudgetConfig(
        limit=Decimal(str(data["limit"])),
        on_exceed=action,
    )
