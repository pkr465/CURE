"""
CURE — Codebase Update & Refactor Engine
HITL Context — Unified Agent Interface

This is the **primary integration point** for the three CURE agents.
Each agent receives an optional ``HITLContext`` instance and calls its
methods to:

1. Check if an issue should be SKIPped  (``should_skip_issue``)
2. Get an augmented prompt prefix       (``get_augmented_context``)
3. Record its own decisions              (``record_agent_decision``)
4. Look up a specific constraint rule    (``get_constraint_for_rule``)
"""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import HITLConfig
from .constraint_parser import ConstraintParser
from .excel_feedback_parser import ExcelFeedbackParser
from .feedback_store import FeedbackStore
from .prompts import HITLPromptTemplates
from .rag_retriever import RAGRetriever
from .schemas import (
    ConstraintRule,
    FeedbackDecision,
    HITLAgentContext,
    RAGRetrievalResult,
)

logger = logging.getLogger(__name__)


class HITLContext:
    """Unified interface for agents to access HITL feedback, constraints,
    and RAG retrieval.

    Typical usage::

        ctx = HITLContext(
            config=hitl_config,
            feedback_excel_path="out/detailed_code_review.xlsx",
            constraints_dir="./constraints",
        )

        # In an agent:
        if ctx.should_skip_issue("QCT001", "src/module.cpp"):
            return  # human said skip

        aug = ctx.get_augmented_context("QCT001", "src/module.cpp")
        prompt = aug.rag_augmented_prompt_prefix + original_prompt
    """

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    def __init__(
        self,
        config: Optional[HITLConfig] = None,
        llm_tools: Optional[Any] = None,
        feedback_excel_path: Optional[str] = None,
        constraints_dir: Optional[str] = None,
    ) -> None:
        self.config = config or HITLConfig()
        self.llm_tools = llm_tools

        # Initialise persistent store (PostgreSQL)
        self.store = FeedbackStore(
            connection_string=self.config.postgres_connection,
            config=self.config,
        )

        # Initialise RAG retriever
        self.retriever = RAGRetriever(
            store=self.store,
            config=self.config,
        )

        # Load Excel feedback if provided
        self._feedback_decisions: List[FeedbackDecision] = []
        if feedback_excel_path:
            self._load_excel_feedback(feedback_excel_path)

        # Load constraint files if provided
        self._constraint_rules: List[ConstraintRule] = []
        if constraints_dir:
            self._load_constraints(constraints_dir)

        # Generate a run ID for this session
        self.run_id = str(uuid.uuid4())[:8]
        self.store.save_run_metadata(self.run_id)

        logger.info(
            "HITLContext initialised: %d decisions, %d constraints (run %s)",
            len(self._feedback_decisions),
            len(self._constraint_rules),
            self.run_id,
        )

    # ------------------------------------------------------------------
    # Agent API — quick checks
    # ------------------------------------------------------------------

    def should_skip_issue(self, issue_type: str, file_path: str) -> bool:
        """Check if past feedback suggests SKIPping this issue.

        Fast O(1) check using the cached skip set.

        Args:
            issue_type: e.g. ``"QCT001"`` or ``"dead_code"``.
            file_path: Source file path.

        Returns:
            ``True`` if the human has previously SKIPped a similar issue.
        """
        return self.retriever.has_skip_history(issue_type, file_path)

    # ------------------------------------------------------------------
    # Agent API — full context retrieval
    # ------------------------------------------------------------------

    def get_augmented_context(
        self,
        issue_type: str,
        file_path: str,
        code_snippet: Optional[str] = None,
        agent_type: str = "llm_agent",
    ) -> HITLAgentContext:
        """Retrieve RAG context and build an augmented context object.

        Args:
            issue_type: Issue category being analysed.
            file_path: Source file being analysed.
            code_snippet: Optional code context.
            agent_type: ``"llm_agent"``, ``"static_analyzer"``, or
                ``"fixer_agent"``.

        Returns:
            :class:`HITLAgentContext` with feedback, constraints, and
            a ready-to-use prompt prefix.
        """
        rag_result = self.retriever.retrieve(
            issue_type=issue_type,
            file_path=file_path,
            code_snippet=code_snippet,
        )

        # Build the prompt prefix
        prompt_prefix = self._build_prompt_prefix(rag_result, agent_type)

        # Extract actionable suggestions
        suggestions = self._extract_suggestions(rag_result)

        return HITLAgentContext(
            relevant_feedback=rag_result.similar_decisions,
            applicable_constraints=rag_result.relevant_constraints,
            rag_augmented_prompt_prefix=prompt_prefix,
            suggestions_from_history=suggestions,
        )

    # ------------------------------------------------------------------
    # Agent API — record decisions
    # ------------------------------------------------------------------

    def record_agent_decision(
        self,
        agent_name: str,
        issue_type: str,
        file_path: str,
        decision: str,
        code_snippet: Optional[str] = None,
        severity: str = "medium",
        applied_constraints: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Record an agent's decision for future RAG retrieval.

        Args:
            agent_name: Which agent made the decision.
            issue_type: Issue category.
            file_path: Source file path.
            decision: ``"FIX"``, ``"SKIP"``, etc.
            code_snippet: Optional code involved.
            severity: Issue severity.
            applied_constraints: Constraints that were applied.
        """
        fd = FeedbackDecision(
            id=str(uuid.uuid4()),
            timestamp=datetime.now(),
            source="agent_decision",
            file_path=file_path,
            code_snippet=code_snippet,
            issue_type=issue_type,
            severity=severity,
            human_action=decision,
            agent_that_flagged=agent_name,
            applied_constraints=applied_constraints,
            run_id=self.run_id,
        )
        self.store.save_decision(fd)
        logger.debug(
            "Recorded %s decision: %s → %s (%s)",
            agent_name,
            issue_type,
            decision,
            file_path,
        )

    # ------------------------------------------------------------------
    # Agent API — direct lookups
    # ------------------------------------------------------------------

    def get_constraint_for_rule(self, rule_id: str) -> Optional[ConstraintRule]:
        """Look up a specific constraint rule by ID."""
        return self.store.get_constraint_rule(rule_id)

    def get_statistics(self) -> Dict[str, Any]:
        """Return store statistics for reporting."""
        stats = self.store.get_statistics()
        stats["run_id"] = self.run_id
        return stats

    # ------------------------------------------------------------------
    # Prompt augmentation
    # ------------------------------------------------------------------

    def augment_prompt(
        self,
        original_prompt: str,
        issue_type: str,
        file_path: str,
        agent_type: str = "llm_agent",
    ) -> str:
        """Convenience: augment an agent prompt with HITL context.

        Combines :meth:`get_augmented_context` and prompt injection
        in one call.

        Args:
            original_prompt: The agent's original prompt.
            issue_type: Issue category.
            file_path: Source file.
            agent_type: Agent type for template selection.

        Returns:
            The prompt with HITL context prepended.
        """
        if not self.config.enable_prompt_augmentation:
            return original_prompt

        ctx = self.get_augmented_context(
            issue_type=issue_type,
            file_path=file_path,
            agent_type=agent_type,
        )

        if not ctx.rag_augmented_prompt_prefix:
            return original_prompt

        return HITLPromptTemplates.inject_hitl_context(
            original_prompt=original_prompt,
            hitl_context=ctx.rag_augmented_prompt_prefix,
            agent_type=agent_type,
        )

    # ------------------------------------------------------------------
    # Internal — loaders
    # ------------------------------------------------------------------

    def _load_excel_feedback(self, excel_path: str) -> None:
        """Load feedback from an Excel file."""
        try:
            parser = ExcelFeedbackParser(
                excel_path=excel_path,
                store=self.store,
                config=self.config,
            )
            self._feedback_decisions = parser.parse_all()
            logger.info(
                "Loaded %d decisions from Excel: %s",
                len(self._feedback_decisions),
                excel_path,
            )
        except Exception as exc:
            logger.warning("Failed to load Excel feedback: %s", exc)

    def _load_constraints(self, constraints_dir: str) -> None:
        """Load constraint rules from markdown files."""
        try:
            parser = ConstraintParser(
                store=self.store,
                config=self.config,
            )
            self._constraint_rules = parser.parse_all_constraint_files(
                constraints_dir
            )
            logger.info(
                "Loaded %d constraint rules from: %s",
                len(self._constraint_rules),
                constraints_dir,
            )
        except Exception as exc:
            logger.warning("Failed to load constraints: %s", exc)

    # ------------------------------------------------------------------
    # Internal — prompt building
    # ------------------------------------------------------------------

    def _build_prompt_prefix(
        self, rag_result: RAGRetrievalResult, agent_type: str
    ) -> str:
        """Build the context string that goes into the prompt template."""
        lines: List[str] = []

        if rag_result.similar_decisions:
            lines.append("## Similar Past Decisions:")
            for d in rag_result.similar_decisions:
                line_info = f":{d.line_number}" if d.line_number else ""
                lines.append(
                    f"- {d.file_path}{line_info} — Action: {d.human_action}"
                )
                if d.human_feedback_text:
                    lines.append(f'  Feedback: "{d.human_feedback_text}"')
                if d.applied_constraints:
                    constraint_text = d.applied_constraints.get("text", "")
                    if constraint_text:
                        lines.append(f"  Constraint: {constraint_text}")

        if rag_result.relevant_constraints:
            lines.append("\n## Applicable Constraints:")
            for c in rag_result.relevant_constraints:
                lines.append(f"- **{c.rule_id}**: {c.llm_action}")
                if c.reasoning:
                    lines.append(f"  Reasoning: {c.reasoning}")
                if c.example_prohibited:
                    lines.append(f"  Prohibited: {c.example_prohibited}")
                if c.example_allowed:
                    lines.append(f"  Allowed: {c.example_allowed}")

        if not lines:
            return ""

        # Truncate to configured max tokens (rough estimate: 4 chars/token)
        max_chars = self.config.rag_context_max_tokens * 4
        context = "\n".join(lines)
        if len(context) > max_chars:
            context = context[:max_chars] + "\n... (truncated)"

        return context

    @staticmethod
    def _extract_suggestions(rag_result: RAGRetrievalResult) -> List[str]:
        """Extract actionable suggestions from RAG results."""
        suggestions: List[str] = []

        for d in rag_result.similar_decisions:
            if d.human_action == "SKIP":
                suggestions.append(
                    f"Skip issues of type '{d.issue_type}' "
                    f"(previously marked false positive)"
                )
            elif d.remediation_notes:
                suggestions.append(d.remediation_notes)

        for c in rag_result.relevant_constraints:
            suggestions.append(f"Rule {c.rule_id}: {c.llm_action}")

        # Deduplicate
        seen: set = set()
        unique: List[str] = []
        for s in suggestions:
            if s not in seen:
                seen.add(s)
                unique.append(s)

        return unique[:10]  # Cap at 10
