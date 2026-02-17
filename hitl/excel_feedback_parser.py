"""
CURE — Codebase Update & Refactor Engine
HITL Excel Feedback Parser

Reads human feedback from ``detailed_code_review.xlsx`` and converts it
into :class:`FeedbackDecision` objects that are persisted to the
:class:`FeedbackStore`.

Expected Excel layout (``Analysis`` sheet):
    File | Line | Severity | Issue_Type | Code | Fixed_Code |
    Feedback | Constraints | Source_Agent | Run_ID | ...

The parser infers a ``human_action`` from the *Feedback* column:
    * SKIP — ``skip``, ``ignore``, ``false positive``, ``no fix``
    * FIX_WITH_CONSTRAINTS — non-empty Constraints column
    * NEEDS_REVIEW — ``review``, ``check``, ``manual``
    * FIX — everything else (default)
"""

import logging
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config import HITLConfig
from .schemas import FeedbackDecision

logger = logging.getLogger(__name__)

# Keywords that trigger SKIP
_SKIP_KEYWORDS = {"skip", "ignore", "false positive", "no fix", "false_positive"}

# Keywords that trigger NEEDS_REVIEW
_REVIEW_KEYWORDS = {"review", "check", "manual", "needs review", "needs_review"}


class ExcelFeedbackParser:
    """Parse human feedback from ``detailed_code_review.xlsx``.

    Reads the *Analysis* sheet and any ``static_*`` adapter sheets.
    Each row with a non-empty Feedback or Constraints cell becomes
    a :class:`FeedbackDecision`.
    """

    def __init__(
        self,
        excel_path: str,
        store: Any,  # FeedbackStore (forward ref to avoid circular)
        config: Optional[HITLConfig] = None,
    ) -> None:
        self.excel_path = Path(excel_path).resolve()
        self.store = store
        self.config = config or HITLConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def parse(self) -> List[FeedbackDecision]:
        """Parse the *Analysis* sheet and return feedback decisions.

        Decisions are auto-persisted to the store when
        ``config.auto_persist_feedback`` is True.
        """
        return self._parse_sheet(self.config.excel_analysis_sheet)

    def parse_adapter_sheets(self) -> List[FeedbackDecision]:
        """Parse all ``static_*`` sheets for adapter-specific feedback."""
        try:
            import openpyxl

            wb = openpyxl.load_workbook(str(self.excel_path), read_only=True)
            adapter_sheets = [s for s in wb.sheetnames if s.startswith("static_")]
            wb.close()
        except Exception as exc:
            logger.warning("Could not inspect sheets: %s", exc)
            return []

        all_decisions: List[FeedbackDecision] = []
        for sheet in adapter_sheets:
            decisions = self._parse_sheet(sheet)
            all_decisions.extend(decisions)
        return all_decisions

    def parse_all(self) -> List[FeedbackDecision]:
        """Parse both Analysis and static_* sheets."""
        decisions = self.parse()
        decisions.extend(self.parse_adapter_sheets())
        return decisions

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _parse_sheet(self, sheet_name: str) -> List[FeedbackDecision]:
        """Read a single sheet and extract feedback decisions."""
        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas is required for Excel parsing")
            return []

        if not self.excel_path.exists():
            logger.error("Excel file not found: %s", self.excel_path)
            return []

        try:
            df = pd.read_excel(
                str(self.excel_path), sheet_name=sheet_name, header=0
            )
        except Exception as exc:
            logger.warning("Could not read sheet '%s': %s", sheet_name, exc)
            return []

        # Normalise column names: strip whitespace
        df.columns = [str(c).strip() for c in df.columns]

        feedback_col = self.config.feedback_column
        constraints_col = self.config.constraints_column

        decisions: List[FeedbackDecision] = []
        import pandas as _pd  # for pd.isna

        for _, row in df.iterrows():
            # Skip rows with no file info
            file_val = row.get("File") or row.get("file") or row.get("file_path")
            if _pd.isna(file_val) if file_val is not None else True:
                continue

            feedback_text = self._safe_str(row.get(feedback_col))
            constraints_text = self._safe_str(row.get(constraints_col))

            # Only create a decision if there's SOME human input
            # or always create (so we capture the initial state too)
            human_action = self._infer_action(feedback_text, constraints_text)

            line_val = row.get("Line") or row.get("line") or row.get("line_number")
            issue_val = (
                row.get("Issue_Type")
                or row.get("issue_type")
                or row.get("Category")
                or row.get("category")
                or ""
            )
            severity_val = (
                row.get("Severity") or row.get("severity") or "medium"
            )
            code_val = self._safe_str(
                row.get("Code") or row.get("code") or row.get("Description")
            )

            decision = FeedbackDecision(
                id=str(uuid.uuid4()),
                timestamp=datetime.now(),
                source="excel",
                file_path=str(file_val),
                line_number=int(line_val) if not _pd.isna(line_val) else None,
                code_snippet=code_val if code_val else None,
                issue_type=str(issue_val),
                severity=str(severity_val),
                human_action=human_action,
                human_feedback_text=feedback_text if feedback_text else None,
                applied_constraints=(
                    {"text": constraints_text} if constraints_text else None
                ),
                remediation_notes=self._safe_str(
                    row.get("Remediation_Notes")
                    or row.get("remediation_notes")
                    or row.get("Fixed_Code")
                ),
                agent_that_flagged=self._safe_str(
                    row.get("Source_Agent") or row.get("source_agent")
                ),
                run_id=self._safe_str(row.get("Run_ID") or row.get("run_id")),
            )
            decisions.append(decision)

        # Persist
        if self.config.auto_persist_feedback and decisions:
            self.store.bulk_save_decisions(decisions)

        logger.info(
            "Parsed %d feedback decisions from '%s' sheet",
            len(decisions),
            sheet_name,
        )
        return decisions

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _infer_action(feedback_text: str, constraints_text: str) -> str:
        """Determine the human action from feedback and constraint text."""
        fb_lower = feedback_text.lower() if feedback_text else ""

        # Check SKIP keywords
        if any(kw in fb_lower for kw in _SKIP_KEYWORDS):
            return "SKIP"

        # Check FIX_WITH_CONSTRAINTS
        if constraints_text:
            return "FIX_WITH_CONSTRAINTS"

        # Check NEEDS_REVIEW
        if any(kw in fb_lower for kw in _REVIEW_KEYWORDS):
            return "NEEDS_REVIEW"

        # Default
        return "FIX"

    @staticmethod
    def _safe_str(val: Any) -> str:
        """Convert a cell value to string, handling NaN / None."""
        if val is None:
            return ""
        try:
            import pandas as _pd

            if _pd.isna(val):
                return ""
        except (TypeError, ValueError):
            pass
        return str(val).strip()
