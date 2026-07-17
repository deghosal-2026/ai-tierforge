"""
OMLX adapter — local model support via OMLX API.

OMLX (Open Model Local eXecution) runs at ``localhost:11434`` by
default and provides an OpenAI-compatible ``/v1/chat/completions``
endpoint.  This makes local models (Qwen, Llama, DeepSeek local) a
first-class utility tier in ai-tierforge — zero API cost, zero data
egress.

Key differences from the OpenAI-compatible adapter:
- **No authentication**: OMLX is local, so no Bearer token is sent.
- **Zero cost**: ``calculate_cost()`` always returns ``(0, 0)``.
- **Model name prefix**: Model names in YAML use the ``omlx:`` prefix
  (e.g. ``omlx:qwen2.5-coder:7b``), which is stripped before dispatch.
- **Health check**: ``check_available()`` hits ``/api/tags`` to verify
  OMLX is running — returns False if the connection is refused.

When OMLX is unavailable:
- ``check_available()`` returns False (logged as a warning on router init)
- ``call()`` returns a failed ModelCall with ``error="connection_refused"``
- The router treats this as a retryable error → retries → escalates up
- This is the intended behaviour: OMLX down = utility unavailable = escalate
"""

from decimal import Decimal
import time

import requests

from ai_tierforge.types import ModelCall


class OMLXAdapter:
    """Adapter for local OMLX models.

    Strips the ``omlx:`` prefix from model names before dispatching.
    All calls have zero cost since the model runs locally.

    Attributes:
        _endpoint: Base URL for the OMLX server (no trailing slash).
        _timeout:  Request timeout in seconds (default 60 — local
                   models can be slower than cloud APIs).
    """

    def __init__(
        self,
        endpoint: str = "http://localhost:11434",
        timeout: int = 60,
    ) -> None:
        """Initialise the OMLX adapter.

        Args:
            endpoint: OMLX server URL.  Defaults to localhost:11434.
                      Can be overridden for Docker/remote OMLX instances.
            timeout:  Request timeout in seconds.  Local models may
                      need more time than cloud APIs, so default is 60.
        """
        self._endpoint = endpoint.rstrip("/")
        self._timeout = timeout

    @property
    def name(self) -> str:
        """Adapter name — matches the ``provider: omlx`` field in YAML."""
        return "omlx"

    def call(
        self,
        model: str,
        prompt: str,
        max_tokens: int,
        **kwargs,
    ) -> ModelCall:
        """POST to the local OMLX chat endpoint.

        The model name has its ``omlx:`` prefix stripped before being
        sent to the OMLX server (e.g. ``omlx:qwen2.5-coder:7b`` →
        ``qwen2.5-coder:7b``).

        No authentication header is sent — OMLX is local and doesn't
        require an API key.

        On connection failure, returns a ModelCall with
        ``error="connection_refused"`` so the router can retry and
        escalate to the next tier.

        Args:
            model:      Model name with ``omlx:`` prefix.
            prompt:     Prompt text to send.
            max_tokens: Maximum output tokens.
            **kwargs:   Additional arguments for the request body.

        Returns:
            ``ModelCall`` with the response (or error details).
        """
        # Strip the "omlx:" prefix — OMLX expects the bare model name
        stripped_model = model.removeprefix("omlx:")
        url = f"{self._endpoint}/v1/chat/completions"
        # OpenAI-compatible request body — OMLX supports the same format
        body = {
            "model": stripped_model,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": max_tokens,
            "stream": False,  # v1 is synchronous only
            **kwargs,
        }

        start = time.time()
        try:
            resp = requests.post(
                url, json=body, timeout=self._timeout
            )
            duration_ms = int((time.time() - start) * 1000)

            if resp.status_code == 200:
                data = resp.json()
                usage = data.get("usage", {})
                choice = data["choices"][0]
                # OMLX is free — cost_in and cost_out are always 0
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
                )

            # Non-200 response — return a failed ModelCall
            return ModelCall(
                task_id="",
                task_type="",
                tier="",
                model=model,
                prompt=prompt,
                success=False,
                error=f"http_{resp.status_code}",
                duration_ms=duration_ms,
            )

        except requests.exceptions.ConnectionError:
            duration_ms = int((time.time() - start) * 1000)
            return ModelCall(
                task_id="",
                task_type="",
                tier="",
                model=model,
                prompt=prompt,
                success=False,
                error="connection_refused",
                duration_ms=duration_ms,
                attempt=0,
            )
        except requests.exceptions.Timeout:
            duration_ms = int((time.time() - start) * 1000)
            return ModelCall(
                task_id="",
                task_type="",
                tier="",
                model=model,
                prompt=prompt,
                success=False,
                error="timeout",
                duration_ms=duration_ms,
                attempt=0,
            )

    def calculate_cost(
        self,
        model: str,
        tokens_in: int,
        tokens_out: int,
    ) -> tuple[Decimal, Decimal]:
        """Calculate the cost of an OMLX call.

        OMLX runs locally, so there is no per-token cost.  This always
        returns ``(Decimal("0"), Decimal("0"))`` regardless of token
        counts.

        Args:
            model:      Model name (unused — all OMLX models are free).
            tokens_in:  Input token count (unused).
            tokens_out: Output token count (unused).

        Returns:
            ``(Decimal("0"), Decimal("0"))`` — zero cost.
        """
        return (Decimal("0"), Decimal("0"))

    def check_available(self) -> bool:
        """Check if OMLX is running via the /api/tags endpoint.

        Sends a GET request to ``{endpoint}/api/tags`` — OMLX responds
        with 200 and a list of available models if it's running.
        Returns False if the connection is refused or times out.

        This is called on router init to warn the user if the utility
        tier is unavailable.

        Returns:
            True if OMLX is reachable, False otherwise.
        """
        try:
            resp = requests.get(
                f"{self._endpoint}/api/tags", timeout=5
            )
            return resp.status_code == 200
        except (requests.exceptions.ConnectionError,
                requests.exceptions.Timeout):
            return False
