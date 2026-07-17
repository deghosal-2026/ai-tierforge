"""
Test fixtures for ai-tierforge.

This file is automatically loaded by pytest.  It provides:
1. Inline YAML config strings (VALID_TIERS_YAML, INVALID_TIERS_YAML,
   CUSTOM_TIERS_YAML) for tests that need to write temp config files.
2. Pytest fixtures: ``sample_config``, ``mock_adapters``, ``router``,
   ``failing_adapters`` — used across all test modules.

The mock adapters implement the ``ProviderAdapter`` protocol without
making any network calls, so tests run fast and don't need API keys.
"""

import pytest

from ai_tierforge.types import (
    TierConfig,
    TierForgeConfig,
)

# ─── Inline YAML Configs ───────────────────────────────────────────────
# These strings match the YAML fixtures in tests/fixtures/ and are
# available for tests that need to write temporary config files.

# Valid 3-tier config matching SPEC §13.1 — architect, workhorse, utility
VALID_TIERS_YAML = """
tiers:
  architect:
    model: glm-5.2
    max_tokens: 16000
    use_for:
      - spec
      - architecture
      - review
      - escalation
    provider: openai-compatible
  workhorse:
    model: deepseek-v4-flash
    max_tokens: 8000
    use_for: [code, tests, refactor, drafts]
    provider: openai-compatible
  utility:
    model: omlx:qwen2.5-coder:7b
    max_tokens: 4000
    use_for: [tickets, summaries, status, admin]
    provider: omlx
    endpoint: http://localhost:11434

escalation:
  default_threshold: 0.30
  max_retries: 3

router:
  max_retries: 3

budgets:
  per_task:
    limit: 0.10
    on_exceed: downgrade
  per_day:
    limit: 5.00
    on_exceed: warn

logging:
  routing: true
  failover: true
  level: info
  output: stdout
"""

# Invalid config for validation tests — has multiple errors:
# - max_tokens is 0 (must be positive)
# - use_for is empty (must not be empty)
# - provider is "unknown_provider" (not a built-in adapter name)
INVALID_TIERS_YAML = """
tiers:
  empty_tier:
    model: test-model
    max_tokens: 0
    use_for: []
    provider: unknown_provider
"""

# Custom tier names for ordering tests — verifies that tier priority
# is derived from YAML position, not from hardcoded "architect/workhorse/utility"
CUSTOM_TIERS_YAML = """
tiers:
  premium:
    model: glm-5.2
    max_tokens: 16000
    use_for: [spec, architecture]
    provider: openai-compatible
  standard:
    model: deepseek-v4-flash
    max_tokens: 8000
    use_for: [code, tests]
    provider: openai-compatible
  economy:
    model: omlx:qwen2.5-coder:7b
    max_tokens: 4000
    use_for: [tickets]
    provider: omlx
"""


@pytest.fixture
def sample_config() -> TierForgeConfig:
    """Return a standard 3-tier config for testing.

    This config has three tiers:
    - architect (GLM-5.2, handles spec/architecture)
    - workhorse (DeepSeek, handles code/tests)
    - utility (OMLX Qwen, handles tickets)

    Uses default escalation, router, and logging settings.
    """
    return TierForgeConfig(
        tiers={
            "architect": TierConfig(
                model="glm-5.2",
                max_tokens=16000,
                use_for=["spec", "architecture"],
                provider="openai-compatible",
                priority=0,
            ),
            "workhorse": TierConfig(
                model="deepseek-v4-flash",
                max_tokens=8000,
                use_for=["code", "tests"],
                provider="openai-compatible",
                priority=1,
            ),
            "utility": TierConfig(
                model="omlx:qwen2.5-coder:7b",
                max_tokens=4000,
                use_for=["tickets"],
                provider="omlx",
                priority=2,
            ),
        },
    )


@pytest.fixture
def mock_adapters():
    """Return mock adapters for testing the router.

    The mock adapters implement the ``ProviderAdapter`` protocol but
    return canned responses without making any network calls.  This
    allows tests to run fast and without API keys.

    The MockAdapter supports:
    - ``success_rate``: probability of success per call (default 1.0)
    - ``fail_times``: list of attempt numbers that should fail
      (overrides success_rate for those attempts)

    Returns a dict with "openai-compatible" and "omlx" keys.
    """
    from ai_tierforge.types import ModelCall
    from decimal import Decimal

    class MockAdapter:
        """Test-only adapter with configurable success/failure.

        Implements the ProviderAdapter protocol structurally.
        """

        def __init__(self, name, success_rate=1.0, fail_times=None):
            self._name = name
            self._success_rate = success_rate
            self._fail_times = fail_times or []
            self._call_count = 0

        @property
        def name(self):
            return self._name

        def call(self, model, prompt, max_tokens, **kwargs):
            """Return a canned response, optionally failing on specific attempts."""
            self._call_count += 1
            # Check if this attempt should fail
            attempt = self._call_count - 1
            if self._call_count in self._fail_times:
                return ModelCall(
                    task_id="", task_type="", tier="", model=model,
                    prompt=prompt, success=False, error="mock_failure",
                    tokens_in=50, tokens_out=0,
                    attempt=attempt,
                )
            return ModelCall(
                task_id="", task_type="", tier="", model=model,
                prompt=prompt, response="mock response",
                tokens_in=100, tokens_out=50,
                cost_in=Decimal("0"), cost_out=Decimal("0"),
                success=True,
                attempt=attempt,
            )

        def calculate_cost(self, model, tokens_in, tokens_out):
            """Return fixed mock pricing for testing."""
            return (Decimal("0.001"), Decimal("0.002"))

        def check_available(self):
            return True

    return {
        "openai-compatible": MockAdapter("openai-compatible", success_rate=1.0),
        "omlx": MockAdapter("omlx", success_rate=1.0),
    }


@pytest.fixture
def router(sample_config, mock_adapters):
    """Return a TierRouter with mock adapters for testing.

    Combines the ``sample_config`` and ``mock_adapters`` fixtures into
    a ready-to-use router instance.  All calls will use mock adapters
    — no network calls are made.
    """
    from ai_tierforge.router import TierRouter
    return TierRouter(sample_config, mock_adapters)


@pytest.fixture
def failing_adapters():
    """Return mock adapters that always fail.

    Used to test retry, escalation, and RouterExhaustedError paths.
    Every call returns ``success=False`` with ``error="mock_failure"``.
    """
    from ai_tierforge.types import ModelCall
    from decimal import Decimal

    class FailingAdapter:
        """Test-only adapter that always returns failures."""

        @property
        def name(self):
            return "failing"

        def call(self, model, prompt, max_tokens, **kwargs):
            return ModelCall(
                task_id="", task_type="", tier="", model=model,
                prompt=prompt, success=False, error="mock_failure",
            )

        def calculate_cost(self, model, tokens_in, tokens_out):
            return (Decimal("0.001"), Decimal("0.002"))

        def check_available(self):
            return False

    return {
        "openai-compatible": FailingAdapter(),
        "omlx": FailingAdapter(),
    }
