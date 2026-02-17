"""
CURE — Codebase Update & Refactor Engine
Excel-to-Agent Directive Parser (Multi-Sheet + Source Tagging)

Parses a human-reviewed Excel report (``detailed_code_review.xlsx``) into
JSONL directives for :class:`CodebaseFixerAgent`.

Supports three sheet types:

* **Analysis** sheet → ``source_type="llm"``  (LLM code review findings)
* **static_*** sheets → ``source_type="static"``  (deep static adapter findings)
* **patch_*** sheets → ``source_type="patch"``  (patch analysis findings)

Each directive includes a ``source_type`` tag so the fixer agent (and
``fixer_workflow.py``) can filter by issue origin.

Usage::

    parser = ExcelToAgentParser("out/detailed_code_review.xlsx")

    # Generate all directives
    parser.generate_agent_directives("out/agent_directives.jsonl")

    # Generate only LLM-originated directives
    parser.generate_agent_directives("out/agent_directives.jsonl", fix_source="llm")
"""

import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Keywords that trigger SKIP
_SKIP_KEYWORDS = {"skip", "ignore", "false positive", "no fix", "false_positive"}

# Keywords that trigger NEEDS_REVIEW
_REVIEW_KEYWORDS = {"review", "check", "manual", "needs review", "needs_review"}


class ExcelToAgentParser:
    """Parse human-reviewed Excel into JSONL agent directives.

    Reads multiple sheet types and tags each directive with the
    originating ``source_type`` so downstream agents can filter.

    Parameters
    ----------
    excel_path : str
        Path to ``detailed_code_review.xlsx``.
    """

    def __init__(self, excel_path: str) -> None:
        self.excel_path = Path(excel_path).resolve()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_agent_directives(
        self,
        output_jsonl: str = "agent_directives.jsonl",
        fix_source: str = "all",
    ) -> int:
        """Convert Excel rows into JSONL directives.

        Parameters
        ----------
        output_jsonl : str
            Output path for the JSONL file.
        fix_source : str
            Which issue sources to include:
            ``"all"`` (default), ``"llm"``, ``"static"``, or ``"patch"``.

        Returns
        -------
        int
            Number of directives written.
        """
        if not self.excel_path.exists():
            logger.error("Excel file not found: %s", self.excel_path)
            print(f"[!] Error: File not found - {self.excel_path}")
            return 0

        logger.info("Reading human feedback from: %s", self.excel_path)
        print(f"[*] Reading human feedback from: {self.excel_path}")

        all_directives: List[Dict[str, Any]] = []
        stats: Dict[str, int] = {
            "FIX": 0, "SKIP": 0, "FIX_WITH_CONSTRAINTS": 0,
            "NEEDS_REVIEW": 0, "total_llm": 0, "total_static": 0,
            "total_patch": 0,
        }

        # -- Read sheets based on fix_source filter -------------------------
        if fix_source in ("all", "llm"):
            llm_directives = self._read_analysis_sheet()
            all_directives.extend(llm_directives)
            stats["total_llm"] = len(llm_directives)

        if fix_source in ("all", "static"):
            static_directives = self._read_static_sheets()
            all_directives.extend(static_directives)
            stats["total_static"] = len(static_directives)

        if fix_source in ("all", "patch"):
            patch_directives = self._read_patch_sheets()
            all_directives.extend(patch_directives)
            stats["total_patch"] = len(patch_directives)

        # -- Count actions --------------------------------------------------
        for d in all_directives:
            action = d.get("action", "FIX")
            if action in stats:
                stats[action] += 1
            else:
                stats[action] = stats.get(action, 0) + 1

        # -- Write JSONL ----------------------------------------------------
        try:
            output_path = Path(output_jsonl)
            output_path.parent.mkdir(parents=True, exist_ok=True)

            with open(output_path, "w", encoding="utf-8") as f:
                for d in all_directives:
                    f.write(json.dumps(d, default=str) + "\n")

            logger.info("Wrote %d directives to %s", len(all_directives), output_jsonl)
            print(f"[*] Successfully parsed {len(all_directives)} directives.")
            print(f"[*] Source breakdown: LLM={stats['total_llm']}, "
                  f"Static={stats['total_static']}, Patch={stats['total_patch']}")
            print(f"[*] Actions: FIX={stats.get('FIX', 0)}, "
                  f"SKIP={stats.get('SKIP', 0)}, "
                  f"FIX_WITH_CONSTRAINTS={stats.get('FIX_WITH_CONSTRAINTS', 0)}, "
                  f"NEEDS_REVIEW={stats.get('NEEDS_REVIEW', 0)}")
            print(f"[*] Output saved to: {output_jsonl}")

        except Exception as exc:
            logger.error("Failed to write JSONL: %s", exc)
            print(f"[!] Failed to write JSONL: {exc}")
            return 0

        return len(all_directives)

    # ------------------------------------------------------------------
    # Sheet readers
    # ------------------------------------------------------------------

    def _read_analysis_sheet(self) -> List[Dict[str, Any]]:
        """Read the ``Analysis`` sheet and tag as ``source_type="llm"``."""
        try:
            import pandas as pd
        except ImportError:
            logger.error("pandas is required for Excel parsing")
            return []

        try:
            df = pd.read_excel(
                str(self.excel_path), sheet_name="Analysis", header=0
            )
        except Exception as exc:
            logger.warning("Could not read 'Analysis' sheet: %s", exc)
            return []

        df.columns = [str(c).strip() for c in df.columns]

        directives: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            directive = self._row_to_directive(
                row, source_sheet="Analysis", source_type="llm"
            )
            if directive:
                directives.append(directive)

        logger.info("Analysis sheet: %d directives", len(directives))
        return directives

    def _read_static_sheets(self) -> List[Dict[str, Any]]:
        """Read all ``static_*`` sheets and tag as ``source_type="static"``."""
        sheets = self._discover_sheets("static_")
        directives: List[Dict[str, Any]] = []

        for sheet_name in sheets:
            sheet_directives = self._read_generic_sheet(
                sheet_name, source_type="static"
            )
            directives.extend(sheet_directives)
            logger.info("Sheet '%s': %d directives", sheet_name, len(sheet_directives))

        return directives

    def _read_patch_sheets(self) -> List[Dict[str, Any]]:
        """Read all ``patch_*`` sheets and tag as ``source_type="patch"``."""
        sheets = self._discover_sheets("patch_")
        directives: List[Dict[str, Any]] = []

        for sheet_name in sheets:
            sheet_directives = self._read_generic_sheet(
                sheet_name, source_type="patch"
            )
            directives.extend(sheet_directives)
            logger.info("Sheet '%s': %d directives", sheet_name, len(sheet_directives))

        return directives

    def _read_generic_sheet(
        self, sheet_name: str, source_type: str
    ) -> List[Dict[str, Any]]:
        """Read a single sheet and convert rows to directives."""
        try:
            import pandas as pd
        except ImportError:
            return []

        try:
            df = pd.read_excel(
                str(self.excel_path), sheet_name=sheet_name, header=0
            )
        except Exception as exc:
            logger.warning("Could not read sheet '%s': %s", sheet_name, exc)
            return []

        df.columns = [str(c).strip() for c in df.columns]

        directives: List[Dict[str, Any]] = []
        for _, row in df.iterrows():
            directive = self._row_to_directive(
                row, source_sheet=sheet_name, source_type=source_type
            )
            if directive:
                directives.append(directive)

        return directives

    # ------------------------------------------------------------------
    # Row conversion
    # ------------------------------------------------------------------

    def _row_to_directive(
        self,
        row: Any,
        source_sheet: str,
        source_type: str,
    ) -> Optional[Dict[str, Any]]:
        """Convert a single Excel row into a directive dict.

        Column mapping is flexible — supports both canonical and
        alternative column names.

        Returns *None* if the row has no valid file path.
        """
        import pandas as pd

        # -- File path (required) -------------------------------------------
        file_val = (
            row.get("File")
            or row.get("file")
            or row.get("file_path")
            or row.get("File_Path")
        )
        if file_val is None or (isinstance(file_val, float) and pd.isna(file_val)):
            return None
        file_val = str(file_val).strip()
        if not file_val or file_val.lower() == "nan":
            return None

        # -- Line number ----------------------------------------------------
        line_val = row.get("Line") or row.get("line") or row.get("line_number") or 0
        try:
            line_number = int(line_val) if not pd.isna(line_val) else 0
        except (TypeError, ValueError):
            line_number = 0

        # -- Severity -------------------------------------------------------
        severity = self._safe_str(
            row.get("Severity") or row.get("severity") or "medium"
        )

        # -- Issue type / category ------------------------------------------
        issue_type = self._safe_str(
            row.get("Issue_Type")
            or row.get("issue_type")
            or row.get("Category")
            or row.get("category")
            or ""
        )

        # -- Code snippet ---------------------------------------------------
        bad_code = self._safe_str(
            row.get("Code")
            or row.get("code")
            or row.get("Code_Before")
            or row.get("Description")
            or ""
        )

        # -- Suggested fix --------------------------------------------------
        suggested_fix = self._safe_str(
            row.get("Fixed_Code")
            or row.get("fixed_code")
            or row.get("Code_After")
            or ""
        )

        # -- Feedback and constraints ---------------------------------------
        feedback = self._safe_str(row.get("Feedback") or row.get("feedback") or "")
        constraints = self._safe_str(
            row.get("Constraints") or row.get("constraints") or ""
        )

        # -- Description (for patch/static sheets) --------------------------
        description = self._safe_str(
            row.get("Description") or row.get("description") or ""
        )

        # -- Determine action -----------------------------------------------
        action = self._infer_action(feedback, constraints)

        # -- Run ID ---------------------------------------------------------
        run_id = self._safe_str(
            row.get("Run_ID")
            or row.get("run_id")
            or row.get("S.No")
            or ""
        )

        return {
            "file_path": file_val,
            "line_number": line_number,
            "severity": severity,
            "issue_type": issue_type,
            "bad_code_snippet": bad_code,
            "suggested_fix": suggested_fix,
            "human_feedback": feedback,
            "human_constraints": constraints,
            "description": description,
            "action": action,
            "run_id": run_id,
            "source_type": source_type,
            "source_sheet": source_sheet,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _discover_sheets(self, prefix: str) -> List[str]:
        """Find all sheet names starting with *prefix*."""
        try:
            import openpyxl

            wb = openpyxl.load_workbook(str(self.excel_path), read_only=True)
            sheets = [s for s in wb.sheetnames if s.startswith(prefix)]
            wb.close()
            return sheets
        except Exception as exc:
            logger.warning("Could not inspect sheet names: %s", exc)
            return []

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
            import pandas as pd
            if pd.isna(val):
                return ""
        except (TypeError, ValueError, ImportError):
            pass
        result = str(val).strip()
        return "" if result.lower() == "nan" else result


# ==========================================
# CLI entry point
# ==========================================
if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Parse detailed_code_review.xlsx into agent directives (JSONL)",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--excel-file",
        default="out/detailed_code_review.xlsx",
        help="Path to the reviewed Excel file",
    )
    parser.add_argument(
        "--output-jsonl",
        default="out/human_guided_directives.jsonl",
        help="Output JSONL path",
    )
    parser.add_argument(
        "--fix-source",
        choices=["all", "llm", "static", "patch"],
        default="all",
        help="Process only issues from: all, llm, static, or patch sheets",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s - %(levelname)s - %(message)s",
    )

    p = ExcelToAgentParser(args.excel_file)
    count = p.generate_agent_directives(
        args.output_jsonl, fix_source=args.fix_source
    )
    print(f"\nTotal directives: {count}")
