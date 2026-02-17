"""
CURE — Codebase Update & Refactor Engine
HITL Configuration

Dataclass holding all HITL pipeline settings.  Can be built from
``GlobalConfig`` (YAML), CLI arguments, or direct instantiation.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class HITLConfig:
    """Configuration for the Human-in-the-Loop RAG pipeline."""

    # ── Storage ──────────────────────────────────────────────────────────
    store_db_path: Path = field(default_factory=lambda: Path("./out/hitl/feedback.db"))

    # ── RAG retrieval ────────────────────────────────────────────────────
    rag_top_k: int = 5
    rag_similarity_threshold: float = 0.6

    # ── Excel parsing ────────────────────────────────────────────────────
    excel_analysis_sheet: str = "Analysis"
    feedback_column: str = "Feedback"
    constraints_column: str = "Constraints"

    # ── Constraint file discovery ────────────────────────────────────────
    constraint_file_pattern: str = "**/*_constraints.md"
    constraint_table_marker: str = "| Rule ID"

    # ── Prompt injection ─────────────────────────────────────────────────
    enable_prompt_augmentation: bool = True
    rag_context_max_tokens: int = 2000

    # ── Lifecycle ────────────────────────────────────────────────────────
    auto_persist_feedback: bool = True
    cleanup_old_decisions_days: Optional[int] = None  # None = keep forever

    # ── Factory ──────────────────────────────────────────────────────────

    @classmethod
    def from_global_config(cls, global_config: Any) -> "HITLConfig":
        """Build from a :class:`GlobalConfig` instance using dot-path access.

        Falls back to defaults for any missing key.
        """
        get = getattr(global_config, "get", lambda k, d=None: d)
        get_int = getattr(global_config, "get_int", lambda k, d=0: d)
        get_bool = getattr(global_config, "get_bool", lambda k, d=False: d)

        raw_path = get("hitl.store_db_path", str(cls.store_db_path))
        return cls(
            store_db_path=Path(raw_path),
            rag_top_k=get_int("hitl.rag_top_k", cls.rag_top_k),
            rag_similarity_threshold=float(
                get("hitl.rag_similarity_threshold", cls.rag_similarity_threshold)
            ),
            excel_analysis_sheet=get(
                "hitl.excel_analysis_sheet", cls.excel_analysis_sheet
            ),
            feedback_column=get("hitl.feedback_column", cls.feedback_column),
            constraints_column=get("hitl.constraints_column", cls.constraints_column),
            constraint_file_pattern=get(
                "hitl.constraint_file_pattern", cls.constraint_file_pattern
            ),
            constraint_table_marker=get(
                "hitl.constraint_table_marker", cls.constraint_table_marker
            ),
            enable_prompt_augmentation=get_bool(
                "hitl.enable_prompt_augmentation", cls.enable_prompt_augmentation
            ),
            rag_context_max_tokens=get_int(
                "hitl.rag_context_max_tokens", cls.rag_context_max_tokens
            ),
            auto_persist_feedback=get_bool(
                "hitl.auto_persist_feedback", cls.auto_persist_feedback
            ),
        )

    @classmethod
    def from_cli_args(cls, args: Any) -> "HITLConfig":
        """Build from an ``argparse.Namespace``."""
        return cls(
            store_db_path=Path(
                getattr(args, "hitl_store_path", str(cls.store_db_path))
            ),
            rag_top_k=getattr(args, "hitl_rag_top_k", cls.rag_top_k),
        )
