"""
CURE — Codebase Update & Refactor Engine
HITL Data Schemas

Dataclass models for the Human-in-the-Loop RAG pipeline.
All models are pure dataclasses (no external dependencies).
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional


# ---------------------------------------------------------------------------
# Human feedback decision (persisted to SQLite)
# ---------------------------------------------------------------------------

@dataclass
class FeedbackDecision:
    """Single human feedback decision captured from Excel or agent output.

    Represents one row of human review: which issue, in which file,
    and what the human decided (FIX / SKIP / FIX_WITH_CONSTRAINTS / NEEDS_REVIEW).
    """

    id: str
    timestamp: datetime
    source: str  # "excel", "constraint_file", "agent_decision"

    # Issue identification
    file_path: str
    line_number: Optional[int] = None
    code_snippet: Optional[str] = None
    issue_type: str = ""
    severity: str = "medium"  # critical / high / medium / low

    # Human decision
    human_action: str = "FIX"  # FIX | SKIP | FIX_WITH_CONSTRAINTS | NEEDS_REVIEW
    human_feedback_text: Optional[str] = None

    # Constraints (if any)
    applied_constraints: Optional[Dict[str, Any]] = None
    remediation_notes: Optional[str] = None

    # Metadata
    agent_that_flagged: Optional[str] = None  # "llm_agent", "static_analyzer", "fixer_agent"
    run_id: Optional[str] = None


# ---------------------------------------------------------------------------
# Constraint rule parsed from *_constraints.md
# ---------------------------------------------------------------------------

@dataclass
class ConstraintRule:
    """A rule parsed from a ``*_constraints.md`` markdown file.

    Format mirrors ``REMEDIATION_CONSTRAINTS.md``:

    | Rule ID | Description | Standard Remediation | LLM Action / Constraint |
    """

    rule_id: str
    description: str = ""
    standard_remediation: str = ""
    llm_action: str = ""  # IGNORE, RESTRICT, CONTEXT_AWARE, etc.
    reasoning: Optional[str] = None
    example_allowed: Optional[str] = None
    example_prohibited: Optional[str] = None
    applies_to_patterns: Optional[List[str]] = None  # glob patterns for file matching
    source_file: Optional[str] = None  # which constraints.md it came from


# ---------------------------------------------------------------------------
# RAG retrieval result
# ---------------------------------------------------------------------------

@dataclass
class RAGRetrievalResult:
    """Result from querying the feedback store for similar past decisions."""

    similar_decisions: List[FeedbackDecision] = field(default_factory=list)
    relevant_constraints: List[ConstraintRule] = field(default_factory=list)
    retrieval_score: float = 0.0  # confidence 0-1
    retrieval_explanation: str = ""


# ---------------------------------------------------------------------------
# Context object injected into agent LLM calls
# ---------------------------------------------------------------------------

@dataclass
class HITLAgentContext:
    """Context bundle passed to agents before their LLM calls.

    Contains relevant past feedback, applicable constraints,
    a ready-to-prepend prompt prefix, and a list of actionable suggestions.
    """

    relevant_feedback: List[FeedbackDecision] = field(default_factory=list)
    applicable_constraints: List[ConstraintRule] = field(default_factory=list)
    rag_augmented_prompt_prefix: str = ""
    suggestions_from_history: List[str] = field(default_factory=list)
