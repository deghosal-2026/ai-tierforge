"""
Init for the adapters package.

This package contains provider adapters that implement the
``ProviderAdapter`` protocol from ``base.py``.

Built-in adapters:
- ``openai_compat.py``: OpenAI-compatible API adapter (OpenAI, DeepSeek, vLLM)
- ``omlx.py``:           Local OMLX model adapter (in parent package)

Custom adapters can be placed here or in a separate package — the
router accepts any dict of ``{name: adapter_instance}``.
"""
