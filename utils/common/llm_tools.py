# llm_tools.py
"""
CURE — LLM Tools Router

Thin wrapper that reads ``llm.llm_provider`` from global_config.yaml and
re-exports the matching provider module.  All existing imports like::

    from utils.common.llm_tools import LLMTools, LLMConfig

continue to work unchanged — they simply resolve to the provider chosen
in the config file.

Supported providers:
    "qgenie"    → utils.common.llm_tools_qgenie    (QGenie SDK)
    "anthropic" → utils.common.llm_tools_anthropic  (Anthropic SDK)

Both modules expose an identical public API so every agent, worker, and
utility that calls ``llm_tools.llm_call(prompt)`` works without changes.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# ── Determine the active provider ────────────────────────────────────────────

_PROVIDER = "qgenie"  # safe default

try:
    from utils.parsers.global_config_parser import GlobalConfig
    _gc = GlobalConfig()
    _PROVIDER = (_gc.get("llm.llm_provider") or "qgenie").strip().lower()
except Exception:
    # Fall back to env var, then default
    _PROVIDER = os.environ.get("LLM_PROVIDER", "qgenie").strip().lower()

logger.info("llm_tools router: active provider = %s", _PROVIDER)

# ── Import everything from the chosen backend ────────────────────────────────
#
# We re-export every public name so callers can do:
#   from utils.common.llm_tools import LLMTools, LLMConfig, LLMError, ...

if _PROVIDER == "anthropic":
    from utils.common.llm_tools_anthropic import (       # noqa: F401
        LLMConfig,
        LLMTools,
        BaseLLMProvider,
        AnthropicProvider,
        create_provider,
        LLMError,
        LLMProviderError,
        LLMResponseError,
        IntentExtractionError,
        ProviderNotAvailableError,
    )
    # Alias so code that references QGenieProvider doesn't break at import time
    QGenieProvider = None  # type: ignore[assignment]

else:
    from utils.common.llm_tools_qgenie import (          # noqa: F401
        LLMConfig,
        LLMTools,
        BaseLLMProvider,
        QGenieProvider,
        create_provider,
        LLMError,
        LLMProviderError,
        LLMResponseError,
        IntentExtractionError,
        ProviderNotAvailableError,
    )
    # Alias so code that references AnthropicProvider doesn't break
    AnthropicProvider = None  # type: ignore[assignment]


def get_active_provider() -> str:
    """Return the name of the currently active LLM provider."""
    return _PROVIDER


__all__ = [
    # Core classes (always available regardless of provider)
    "LLMTools",
    "LLMConfig",
    "BaseLLMProvider",
    "create_provider",
    # Provider-specific (one will be the real class, the other None)
    "QGenieProvider",
    "AnthropicProvider",
    # Exceptions
    "LLMError",
    "LLMProviderError",
    "LLMResponseError",
    "IntentExtractionError",
    "ProviderNotAvailableError",
    # Helper
    "get_active_provider",
]
