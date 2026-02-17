"""
utils.common - Standalone tool classes for codebase analysis.

Modules:
    llm_tools       - Multi-provider LLM integration (Claude, QGenie, VertexAI, Azure)
    email_reporter  - HTML email report generation and SMTP sending
    excel_writer    - Professional Excel report creation
    mmdtopdf        - Mermaid diagram to PNG/SVG/PDF conversion

All classes are standalone with no cross-dependencies.
Each module uses dataclass-based configuration with from_env() factory methods.
"""

from utils.common.llm_tools import (
    LLMTools,
    LLMConfig,
    BaseLLMProvider,
    QGenieProvider,
    create_provider,
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
    # LLM (multi-provider)
    "LLMTools",
    "LLMConfig",
    "BaseLLMProvider",
    "QGenieProvider",
    "PROVIDER_REGISTRY",
    "create_provider",
    "parse_provider_model",
    "LLMError",
    "LLMProviderError",
    "LLMResponseError",
    "IntentExtractionError",
    "ProviderNotAvailableError",
    # Email
    "EmailReporter",
    "HTMLReportGenerator",
    "EmailError",
    "EmailSendError",
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
