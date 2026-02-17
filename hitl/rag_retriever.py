"""
CURE — Codebase Update & Refactor Engine
HITL RAG Retriever

Queries the :class:`FeedbackStore` for similar past decisions and
applicable constraint rules.  Uses keyword/metadata matching (no ML
models required) with optional semantic similarity upgrade path.
"""

import logging
from fnmatch import fnmatch
from typing import Any, List, Optional

from .config import HITLConfig
from .schemas import (
    ConstraintRule,
    FeedbackDecision,
    RAGRetrievalResult,
)

logger = logging.getLogger(__name__)


class RAGRetriever:
    """Query engine over the HITL feedback store.

    Retrieval strategy:

    1. **Exact issue-type match** — find decisions with the same ``issue_type``
    2. **File-level match** — find decisions for the same file
    3. **Constraint matching** — find rules whose ``rule_id`` appears in
       the issue type, or whose ``applies_to_patterns`` match the file path
    4. **Rank** by recency and action priority (SKIP > FIX_WITH_CONSTRAINTS > FIX)
    5. **Truncate** to ``rag_top_k``
    """

    # Action priority for ranking (higher = more relevant to surface)
    _ACTION_PRIORITY = {
        "SKIP": 4,
        "FIX_WITH_CONSTRAINTS": 3,
        "NEEDS_REVIEW": 2,
        "FIX": 1,
    }

    def __init__(
        self,
        store: Any,  # FeedbackStore
        config: Optional[HITLConfig] = None,
    ) -> None:
        self.store = store
        self.config = config or HITLConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def retrieve(
        self,
        issue_type: str,
        file_path: str,
        code_snippet: Optional[str] = None,
        severity: Optional[str] = None,
    ) -> RAGRetrievalResult:
        """Query the store for similar past decisions and constraints.

        Args:
            issue_type: Issue category (e.g., ``"QCT001"``, ``"security"``).
            file_path: Source file path being analysed.
            code_snippet: Optional code context for future semantic matching.
            severity: Optional severity filter.

        Returns:
            :class:`RAGRetrievalResult` with ranked decisions and constraints.
        """
        # 1. Gather candidate decisions
        candidates = self._gather_candidates(issue_type, file_path)

        # 2. Filter by severity if provided
        if severity:
            candidates = [d for d in candidates if d.severity == severity] or candidates

        # 3. Deduplicate by id
        seen_ids: set = set()
        unique: List[FeedbackDecision] = []
        for d in candidates:
            if d.id not in seen_ids:
                seen_ids.add(d.id)
                unique.append(d)
        candidates = unique

        # 4. Rank: action priority (desc), then recency (desc)
        candidates.sort(
            key=lambda d: (
                self._ACTION_PRIORITY.get(d.human_action, 0),
                d.timestamp.timestamp(),
            ),
            reverse=True,
        )

        # 5. Truncate
        top_k = self.config.rag_top_k
        similar_decisions = candidates[:top_k]

        # 6. Find relevant constraints
        relevant_constraints = self._find_constraints(issue_type, file_path)

        # 7. Score
        score = min(
            1.0,
            (len(similar_decisions) + len(relevant_constraints) * 2) / 10.0,
        )

        # 8. Explanation
        explanation = self._build_explanation(
            issue_type, similar_decisions, relevant_constraints
        )

        return RAGRetrievalResult(
            similar_decisions=similar_decisions,
            relevant_constraints=relevant_constraints,
            retrieval_score=score,
            retrieval_explanation=explanation,
        )

    def has_skip_history(self, issue_type: str, file_path: str) -> bool:
        """Fast check: has the human ever SKIPped this issue type + file?

        Uses the cached skip set from :meth:`FeedbackStore.get_skip_set`.
        Also checks for issue-type-only skips (file_path == "*").
        """
        skip_set = self.store.get_skip_set()

        # Exact match
        if (issue_type, file_path) in skip_set:
            return True

        # Issue-type-only match (any file)
        for it, fp in skip_set:
            if it == issue_type:
                return True

        return False

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _gather_candidates(
        self, issue_type: str, file_path: str
    ) -> List[FeedbackDecision]:
        """Collect candidate decisions from multiple query paths."""
        candidates: List[FeedbackDecision] = []

        # By issue type (exact match)
        if issue_type:
            candidates.extend(self.store.get_decisions_by_issue_type(issue_type))

        # By file path (substring match)
        if file_path:
            # Use just the filename for broader matching
            filename = file_path.rsplit("/", 1)[-1] if "/" in file_path else file_path
            candidates.extend(self.store.get_decisions_by_file(filename))

        return candidates

    def _find_constraints(
        self, issue_type: str, file_path: str
    ) -> List[ConstraintRule]:
        """Find constraint rules applicable to this issue/file."""
        all_rules = self.store.get_all_constraint_rules()
        relevant: List[ConstraintRule] = []

        for rule in all_rules:
            # Match by rule_id appearing in the issue_type
            if rule.rule_id and rule.rule_id.lower() in issue_type.lower():
                relevant.append(rule)
                continue

            # Match by description keywords in issue_type
            if rule.description and issue_type:
                desc_words = set(rule.description.lower().split())
                issue_words = set(issue_type.lower().split())
                if desc_words & issue_words:
                    relevant.append(rule)
                    continue

            # Match by file glob patterns
            if rule.applies_to_patterns:
                for pattern in rule.applies_to_patterns:
                    if fnmatch(file_path, pattern):
                        relevant.append(rule)
                        break

        return relevant

    @staticmethod
    def _build_explanation(
        issue_type: str,
        decisions: List[FeedbackDecision],
        rules: List[ConstraintRule],
    ) -> str:
        """Build a human-readable explanation of retrieval results."""
        parts: List[str] = []

        if decisions:
            parts.append(
                f"Found {len(decisions)} past decision(s) for '{issue_type}':"
            )
            for d in decisions[:3]:
                line_info = f":{d.line_number}" if d.line_number else ""
                feedback_info = (
                    f' — "{d.human_feedback_text}"'
                    if d.human_feedback_text
                    else ""
                )
                parts.append(
                    f"  • {d.file_path}{line_info} → {d.human_action}{feedback_info}"
                )

        if rules:
            parts.append(f"Found {len(rules)} applicable constraint(s):")
            for r in rules[:3]:
                parts.append(f"  • {r.rule_id}: {r.llm_action}")

        if not parts:
            return f"No prior feedback found for '{issue_type}'."

        return "\n".join(parts)
