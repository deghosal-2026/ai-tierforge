"""
OpenAI-compatible provider adapter.

Supports any OpenAI-compatible API endpoint (OpenAI, DeepSeek, vLLM,
LiteLLM proxy, Portkey gateway, etc.) via configurable endpoint + API key.

Key features:
- **Configurable endpoint**: Point at any OpenAI-compatible URL.
  This enables use with gateways (LiteLLM, Portkey) and self-hosted
  models (vLLM, text-generation-inference).
- **Environment-variable auth**: API keys are read from env vars, never
  hardcoded or stored in YAML config (per PRD §12 security requirements).
- **Built-in pricing table**: Common models (GLM, DeepSeek, GPT-4o,
  Claude) have default per-token pricing.  Custom models can be added
  via the ``pricing`` constructor argument.
- **Retry with backoff**: 3 attempts with exponential backoff (1s, 2s,
  4s) on 5xx, timeout, and rate-limit errors.  4xx errors (except 429)
  are non-retryable and return immediately.

Usage::

    # Default — points at OpenAI, reads OPENAI_API_KEY from env
    adapter = OpenAICompatAdapter()

    # Point at DeepSeek — reads DEEPSEEK_API_KEY from env
    adapter = OpenAICompatAdapter(
        endpoint="https://api.deepseek.com/v1",
        api_key_env="DEEPSEEK_API_KEY",
    )

    # Point at a LiteLLM gateway
    adapter = OpenAICompatAdapter(
        endpoint="http://localhost:4000/v1",
        api_key_env="LITELLM_API_KEY",
    )
"""

from decimal import Decimal
import os
import time
from typing import Optional

import requests

from ai_tierforge.types import ModelCall

# ─── Default Pricing Table ─────────────────────────────────────────────
# Per-token pricing for common models.  Values are (cost_per_token_in,
# cost_per_token_out) as Decimals for precision.
#
# Note: Anthropic models are listed for use via OpenAI-compatible proxies
# (LiteLLM, Portkey).  Native Anthropic API support is out of scope for v1
# (see PRD §3.3).  Users would configure the proxy endpoint and use these
# model names.
DEFAULT_PRICING: dict[str, tuple[Decimal, Decimal]] = {
    # model: (cost_per_token_in, cost_per_token_out)
    "glm-5.2":              (Decimal("0.000003"),   Decimal("0.000008")),
    "deepseek-v4-flash":    (Decimal("0.00000014"), Decimal("0.00000028")),
    "deepseek-v4-pro":      (Decimal("0.0000015"),  Decimal("0.000004")),
    "gpt-4o":               (Decimal("0.0000025"),  Decimal("0.00001")),
    "gpt-4o-mini":          (Decimal("0.00000015"), Decimal("0.0000006")),
    # Anthropic via OpenAI-compatible proxies (LiteLLM, Portkey)
    "claude-sonnet-4":      (Decimal("0.000003"),   Decimal("0.000015")),
    "claude-haiku-3.5":     (Decimal("0.00000025"), Decimal("0.00000125")),
}


class OpenAICompatAdapter:
    """Adapter for any OpenAI-compatible LLM API.

    Implements the ``ProviderAdapter`` protocol via structural typing
    — no explicit inheritance needed.

    Attributes:
        _endpoint:    Base URL for the API (no trailing slash).
        _api_key_env: Name of the environment variable holding the API key.
        _pricing:     Model → (cost_in, cost_out) pricing table.
        _timeout:     Request timeout in seconds.
    """

    def __init__(
        self,
        endpoint: str = "https://api.openai.com/v1",
        api_key_env: str = "OPENAI_API_KEY",
        pricing: Optional[dict[str, tuple[Decimal, Decimal]]] = None,
        timeout: int = 30,
    ):
        """Initialise the adapter.

        Args:
            endpoint:    Base URL for the OpenAI-compatible API.
                         Defaults to OpenAI.  Can point at DeepSeek,
                         vLLM, LiteLLM, Portkey, or any compatible gateway.
            api_key_env: Name of the environment variable to read the
                         API key from.  Never hardcoded — per security
                         requirements (PRD §12).
            pricing:     Optional custom pricing table.  If None, uses
                         ``DEFAULT_PRICING``.  Pass a dict to add or
                         override model pricing.
            timeout:     Request timeout in seconds (default 30).
        """
        self._endpoint = endpoint.rstrip("/")
        self._api_key_env = api_key_env
        self._pricing = (
            {**DEFAULT_PRICING, **pricing} if pricing is not None else DEFAULT_PRICING
        )
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Adapter name — matches the ``provider: openai-compatible`` field."""
        return "openai-compatible"

    def call(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        **kwargs,
    ) -> ModelCall:
        """POST to /chat/completions with Bearer auth and retry logic.

        Flow:
        1. Read API key from the configured environment variable.
           If missing, return a failed ModelCall immediately (no crash).
        2. Build the OpenAI-compatible request body.
        3. Retry loop (3 attempts, exponential backoff):
           - 200 → parse response, return successful ModelCall.
           - 4xx (except 429) → non-retryable, return failed ModelCall.
           - 5xx / timeout / connection error → retryable, backoff + retry.
        4. All retries exhausted → return failed ModelCall with last error.

        Args:
            model:      Model name from tier config.
            prompt:     Prompt text to send.
            max_tokens: Maximum output tokens.
            **kwargs:   Additional arguments for the request body
                        (e.g. temperature, top_p, stop).

        Returns:
            ``ModelCall`` with response, tokens, cost, success/error.
        """
        # ── Read API key from environment ────────────────────────────
        # Per security requirements: keys are never in YAML config.
        api_key = os.environ.get(self._api_key_env)
        if not api_key:
            # Return a failed call rather than crashing — the router
            # will handle the error (retry or escalate)
            return ModelCall(
                task_id="",
                task_type="",
                tier="",
                model=model,
                prompt=prompt,
                success=False,
                error="missing_api_key",
            )

        # ── Build request ────────────────────────────────────────────
        url = f"{self._endpoint}/chat/completions"
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        }
        body = {
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            **kwargs,  # Allow callers to pass temperature, top_p, etc.
        }

        # ── Retry loop (3 attempts, exponential backoff 1s, 2s, 4s) ──
        last_error: Optional[str] = None
        for attempt in range(3):
            start = time.time()
            try:
                resp = requests.post(
                    url, headers=headers, json=body, timeout=self._timeout
                )
                duration_ms = int((time.time() - start) * 1000)

                # ── Success: parse and return ────────────────────────
                if resp.status_code == 200:
                    data = resp.json()
                    usage = data.get("usage", {})
                    choice = data["choices"][0]
                    # Note: cost_in/cost_out are set to 0 here — the
                    # router calls calculate_cost() separately after
                    # the call returns.  This separation allows the
                    # adapter to be tested without pricing data.
                    return ModelCall(
                        task_id="",
                        task_type="",
                        tier="",
                        model=model,
                        prompt=prompt,
                        response=choice["message"]["content"],
                        tokens_in=usage.get("prompt_tokens", 0),
                        tokens_out=usage.get("completion_tokens", 0),
                        cost_in=Decimal("0"),
                        cost_out=Decimal("0"),
                        duration_ms=duration_ms,
                        success=True,
                        attempt=attempt,
                    )

                # ── Error response ───────────────────────────────────
                if resp.status_code == 429:
                    last_error = "rate_limit_exceeded"
                elif resp.status_code == 413:
                    last_error = "content_too_long"
                elif 400 <= resp.status_code < 500:
                    last_error = f"http_{resp.status_code}"
                    return ModelCall(
                        task_id="",
                        task_type="",
                        tier="",
                        model=model,
                        prompt=prompt,
                        success=False,
                        error=last_error,
                        attempt=attempt,
                    )
                else:
                    last_error = f"http_{resp.status_code}"
                # 5xx and 429 are retryable — fall through to backoff

            except requests.exceptions.Timeout:
                last_error = "timeout"
            except requests.exceptions.ConnectionError:
                last_error = "connection_error"
            except Exception as e:
                # Unexpected error — don't retry, return immediately
                last_error = str(e)
                return ModelCall(
                    task_id="",
                    task_type="",
                    tier="",
                    model=model,
                    prompt=prompt,
                    success=False,
                    error=last_error,
                    attempt=attempt,
                )

            # ── Exponential backoff before retry ─────────────────────
            if attempt < 2:
                time.sleep(2 ** attempt)

        # ── All retries exhausted ────────────────────────────────────
        return ModelCall(
            task_id="",
            task_type="",
            tier="",
            model=model,
            prompt=prompt,
            success=False,
            error=last_error,
            attempt=2,
        )

    def calculate_cost(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> tuple[Decimal, Decimal]:
        """Look up model pricing and compute per-token costs.

        Multiplies token counts by the per-token price from the
        pricing table.  Uses Decimal arithmetic for precision.

        Args:
            model:      Model name to look up in the pricing table.
            tokens_in:  Input token count.
            tokens_out: Output token count.

        Returns:
            Tuple of (cost_in, cost_out) as Decimals.

        Raises:
            KeyError: If the model is not in the pricing table.
                      Users must provide pricing for custom models
                      via the ``pricing`` constructor argument.
        """
        if model not in self._pricing:
            raise KeyError(f"no pricing for model '{model}'")
        price_in, price_out = self._pricing[model]
        # Decimal multiplication preserves precision
        cost_in = price_in * tokens_in
        cost_out = price_out * tokens_out
        return (cost_in, cost_out)

    def check_available(self) -> bool:
        """Health check — cloud providers are assumed available.

        For cloud providers (OpenAI, DeepSeek, etc.), we assume they're
        always available — the cloud is reliable enough for v1.  If a
        call fails, the retry logic in ``call()`` handles it.

        Returns:
            Always True for cloud providers.
        """
        return True
