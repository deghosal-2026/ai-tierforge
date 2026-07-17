"""
Provider adapter protocol for ai-tierforge.

All LLM providers must implement this Protocol to be usable as a tier
backend.  The router dispatches calls through whichever adapter is
configured for the matched tier in the YAML config.

The Protocol is ``@runtime_checkable``, so you can verify compliance
with ``isinstance(my_adapter, ProviderAdapter)`` at runtime — useful
for custom adapter validation.

To create a custom adapter::

    from ai_tierforge import ProviderAdapter, ModelCall
    from decimal import Decimal

    class MyCustomAdapter:
        @property
        def name(self) -> str:
            return "my-custom"

        def call(self, model, prompt, max_tokens, **kwargs) -> ModelCall:
            # ... dispatch to your provider ...
            return ModelCall(...)

        def calculate_cost(self, model, tokens_in, tokens_out) -> tuple[Decimal, Decimal]:
            # ... look up pricing ...
            return (cost_in, cost_out)

        def check_available(self) -> bool:
            # ... health check ...
            return True

    # Register it with the router:
    router = TierRouter(config, adapters={"my-custom": MyCustomAdapter()})
"""

from typing import Protocol, runtime_checkable
from decimal import Decimal

from ai_tierforge.types import ModelCall


@runtime_checkable
class ProviderAdapter(Protocol):
    """Interface that all provider adapters must implement.

    A Protocol (structural typing) — any class that has these methods
    with compatible signatures satisfies the protocol, without needing
    to explicitly inherit from it.

    Required members:
        name:           Property returning the adapter's unique name.
        call():         Dispatch a model call and return a ModelCall.
        calculate_cost(): Compute per-token costs for a model.
        check_available(): Health check — is the provider reachable?
    """

    @property
    def name(self) -> str:
        """Unique adapter name matching the YAML ``provider`` field.

        This must match the key used in the ``adapters`` dict passed
        to ``TierRouter``, and the ``provider:`` field in the YAML
        config for each tier.
        """

    def call(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        **kwargs,
    ) -> ModelCall:
        """Dispatch a model call and return the result.

        The adapter is responsible for:
        - Formatting the request for the specific provider
        - Handling HTTP transport (using ``requests`` or similar)
        - Retrying transient failures (3 attempts, exponential backoff)
        - Parsing the response into a ``ModelCall``

        On failure, return a ``ModelCall`` with ``success=False`` and
        ``error`` set to a descriptive string — do NOT raise an
        exception.  The router handles escalation/retry logic based
        on the error string.

        Args:
            model:      Model name from the tier config.
            prompt:     Prompt text to send.
            max_tokens: Maximum output tokens.
            **kwargs:   Additional provider-specific arguments.

        Returns:
            ``ModelCall`` with response, tokens, cost, and success/error.
        """

    def calculate_cost(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> tuple[Decimal, Decimal]:
        """Calculate input and output costs for a model call.

        Looks up the model in a pricing table and multiplies by token
        counts.  Returns Decimal for precision (never float).

        Args:
            model:      Model name to look up pricing for.
            tokens_in:  Input token count.
            tokens_out: Output token count.

        Returns:
            Tuple of (cost_in, cost_out) as Decimals.

        Raises:
            KeyError: If the model is not in the pricing table.
        """

    def check_available(self) -> bool:
        """Health check — returns True if the provider is reachable.

        For cloud providers (OpenAI, DeepSeek): always True (assume
        available — the cloud is reliable enough for v1).

        For local providers (OMLX): GET /api/tags, return False if
        connection refused or timeout.

        Returns:
            True if the provider is available, False otherwise.
        """
