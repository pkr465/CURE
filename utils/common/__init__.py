"""
utils.common - Standalone tool classes for codebase analysis.

Modules:
    llm_tools            - Provider router (auto-selects QGenie or Anthropic)
    llm_tools_qgenie     - QGenie LLM integration
    llm_tools_anthropic  - Anthropic Claude LLM integration
    email_reporter       - HTML email report generation and SMTP sending
    excel_writer         - Professional Excel report creation
    mmdtopdf             - Mermaid diagram to PNG/SVG/PDF conversion

All classes are standalone with no cross-dependencies.
Each module uses dataclass-based configuration with from_env() factory methods.
"""

# Router — re-exports the active provider's classes based on global_config.yaml
from utils.common.llm_tools import (
    LLMTools,
    LLMConfig,
    BaseLLMProvider,
    QGenieProvider,
    AnthropicProvider,
    create_provider,
    get_active_provider,
    LLMError,
    LLMProviderError,
    LLMResponseError,
    IntentExtractionError,
    ProviderNotAvailableError,
)
from utils.common.email_reporter import (
    EmailReporter,
)
from utils.common.excel_writer import (
    ExcelWriter,
    ExcelStyle,
)
from utils.common.mmdtopdf import (
    MermaidConverter,
    MermaidConfig,
    MermaidError,
    MermaidCLIError,
    MermaidNotFoundError,
    ConversionError,
)

__all__ = [
    # LLM (provider-routed)
    "LLMTools",
    "LLMConfig",
    "BaseLLMProvider",
    "QGenieProvider",
    "AnthropicProvider",
    "create_provider",
    "get_active_provider",
    "LLMError",
    "LLMProviderError",
    "LLMResponseError",
    "IntentExtractionError",
    "ProviderNotAvailableError",
    # Email
    "EmailReporter",
    # Excel
    "ExcelWriter",
    "ExcelStyle",
    # Mermaid
    "MermaidConverter",
    "MermaidConfig",
    "MermaidError",
    "MermaidCLIError",
    "MermaidNotFoundError",
    "ConversionError",
]
