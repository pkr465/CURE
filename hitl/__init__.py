"""
CURE — Codebase Update & Refactor Engine
HITL (Human-in-the-Loop) RAG Pipeline

Provides persistent memory and feedback-driven LLM guidance for
three CURE agents:

* **CodebaseLLMAgent** — filter issues based on human feedback
* **StaticAnalyzerAgent** — weight/suppress findings by past decisions
* **CodebaseFixerAgent** — apply constraints when fixing code

Architecture
~~~~~~~~~~~~

1. ``FeedbackStore`` — SQLite persistence for decisions and constraints
2. ``ExcelFeedbackParser`` — reads ``detailed_code_review.xlsx``
3. ``ConstraintParser`` — reads ``*_constraints.md`` files
4. ``RAGRetriever`` — queries similar past decisions
5. ``HITLContext`` — unified agent interface (inject this into agents)
6. ``HITLPromptTemplates`` — RAG-augmented prompt prefixes
"""

__version__ = "0.1.0"

# Graceful degradation: if any dependency is missing the module
# still imports but HITL_AVAILABLE is False.
try:
    from .config import HITLConfig
    from .constraint_parser import ConstraintParser
    from .excel_feedback_parser import ExcelFeedbackParser
    from .feedback_store import FeedbackStore
    from .hitl_context import HITLContext
    from .prompts import HITLPromptTemplates
    from .rag_retriever import RAGRetriever
    from .schemas import (
        ConstraintRule,
        FeedbackDecision,
        HITLAgentContext,
        RAGRetrievalResult,
    )

    HITL_AVAILABLE = True
except ImportError as _exc:  # pragma: no cover
    HITL_AVAILABLE = False

    import logging as _logging

    _logging.getLogger(__name__).warning(
        "HITL module not fully available: %s", _exc
    )

__all__ = [
    # Core
    "HITLContext",
    "HITLConfig",
    "HITL_AVAILABLE",
    # Store
    "FeedbackStore",
    # Parsers
    "ExcelFeedbackParser",
    "ConstraintParser",
    # Retrieval
    "RAGRetriever",
    # Prompts
    "HITLPromptTemplates",
    # Schemas
    "FeedbackDecision",
    "ConstraintRule",
    "RAGRetrievalResult",
    "HITLAgentContext",
]
