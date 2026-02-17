"""
feedback_helpers.py

CURE — Codebase Update & Refactor Engine
Helpers for converting between DataFrames, JSONL directives, Excel reports,
and QA traceability reports.

Author: Pavan R
"""

import io
import json
import logging
import os
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════════
#  Results → DataFrame
# ═══════════════════════════════════════════════════════════════════════════════

def results_to_dataframe(results: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    Convert CodebaseLLMAgent analysis results to a DataFrame
    with added editable feedback columns.

    Args:
        results: List of issue dicts from the agent, each containing:
            File, Title, Severity, Confidence, Category, Line,
            Description, Suggestion, Code, Fixed_Code

    Returns:
        DataFrame with original columns plus Action and Notes columns.
    """
    if not results:
        return pd.DataFrame(columns=[
            "File", "Title", "Severity", "Confidence", "Category",
            "Line", "Description", "Suggestion", "Code", "Fixed_Code",
            "Action", "Notes",
        ])

    df = pd.DataFrame(results)

    # Ensure expected columns exist
    expected = [
        "File", "Title", "Severity", "Confidence", "Category",
        "Line", "Description", "Suggestion", "Code", "Fixed_Code",
    ]
    for col in expected:
        if col not in df.columns:
            df[col] = ""

    # Add editable feedback columns
    if "Action" not in df.columns:
        df["Action"] = "Auto-fix"
    if "Notes" not in df.columns:
        df["Notes"] = ""

    # Reorder columns: feedback columns at the end
    ordered = [c for c in expected if c in df.columns] + ["Action", "Notes"]
    extra = [c for c in df.columns if c not in ordered]
    df = df[ordered + extra]

    return df


# ═══════════════════════════════════════════════════════════════════════════════
#  DataFrame → JSONL Directives
# ═══════════════════════════════════════════════════════════════════════════════

ACTION_MAP = {
    "Auto-fix": "FIX",
    "Skip": "SKIP",
    "Review": "FIX_WITH_CONSTRAINTS",
    "Manual Review": "FIX_WITH_CONSTRAINTS",
}


def dataframe_to_directives(
    df: pd.DataFrame,
    output_path: str,
) -> str:
    """
    Convert an edited feedback DataFrame to JSONL directives
    for CodebaseFixerAgent.

    Args:
        df: DataFrame with columns including File, Line, Category,
            Severity, Suggestion, Fixed_Code, Action, Notes
        output_path: Path to write the JSONL file

    Returns:
        Path to the written JSONL file.
    """
    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    directives = []
    for _, row in df.iterrows():
        action_label = str(row.get("Action", "Auto-fix")).strip()
        action = ACTION_MAP.get(action_label, "FIX")

        # Skip issues explicitly marked as Skip
        if action == "SKIP":
            continue

        directive = {
            "file_path": str(row.get("File", "")),
            "line_number": int(row.get("Line", 0)) if pd.notna(row.get("Line")) else 0,
            "issue_type": str(row.get("Category", "")),
            "severity": str(row.get("Severity", "Medium")),
            "title": str(row.get("Title", "")),
            "description": str(row.get("Description", "")),
            "suggested_fix": str(row.get("Suggestion", "")),
            "fixed_code": str(row.get("Fixed_Code", "")),
            "action": action,
            "source_type": "llm",
        }

        # Include user notes as constraints if provided
        notes = str(row.get("Notes", "")).strip()
        if notes:
            directive["constraints"] = notes

        directives.append(directive)

    with open(output_path, "w", encoding="utf-8") as f:
        for d in directives:
            f.write(json.dumps(d, default=str) + "\n")

    logger.info(
        "Wrote %d directives to %s (%d skipped)",
        len(directives),
        output_path,
        len(df) - len(directives),
    )
    return output_path


# ═══════════════════════════════════════════════════════════════════════════════
#  DataFrame → Excel bytes (for download)
# ═══════════════════════════════════════════════════════════════════════════════

def export_to_excel_bytes(df: pd.DataFrame, sheet_name: str = "Analysis") -> bytes:
    """
    Export a DataFrame to styled Excel bytes suitable for st.download_button.

    Uses the CURE ExcelWriter if available, falls back to plain openpyxl.

    Returns:
        Bytes of the Excel workbook.
    """
    try:
        from utils.common.excel_writer import ExcelWriter, ExcelStyle

        buffer = io.BytesIO()
        temp_path = buffer  # ExcelWriter needs a path, use temp file
        import tempfile
        with tempfile.NamedTemporaryFile(suffix=".xlsx", delete=False) as tmp:
            tmp_path = tmp.name

        writer = ExcelWriter(file_path=tmp_path)
        headers = list(df.columns)
        # Sanitize values: convert non-primitive types to str for openpyxl
        data_rows = [
            [str(v) if v is not None and not isinstance(v, (str, int, float, bool)) else v
             for v in row]
            for row in df.values.tolist()
        ]

        writer.add_table_sheet(
            headers=headers,
            data_rows=data_rows,
            sheet_name=sheet_name,
            status_column="Severity" if "Severity" in headers else None,
            autofit=True,
        )
        writer.save()

        with open(tmp_path, "rb") as f:
            excel_bytes = f.read()

        os.unlink(tmp_path)
        return excel_bytes

    except ImportError:
        # Fallback: plain pandas to_excel
        buffer = io.BytesIO()
        with pd.ExcelWriter(buffer, engine="openpyxl") as w:
            df.to_excel(w, sheet_name=sheet_name, index=False)
        return buffer.getvalue()


# ═══════════════════════════════════════════════════════════════════════════════
#  QA Traceability Report
# ═══════════════════════════════════════════════════════════════════════════════

def build_qa_traceability_report(
    feedback_df: pd.DataFrame,
    fixer_results: Optional[Dict[str, Any]] = None,
    audit_report_path: Optional[str] = None,
) -> pd.DataFrame:
    """
    Build a traceability report mapping human feedback decisions
    to final code modifications.

    Args:
        feedback_df: The reviewed DataFrame with Action/Notes columns.
        fixer_results: Results dict from CodebaseFixerAgent.run_agent().
        audit_report_path: Path to final_execution_audit.xlsx (optional).

    Returns:
        DataFrame with columns: File, Issue, Human_Action, Human_Notes,
        Fixer_Status, Lines_Changed, Validation_Status.
    """
    rows = []

    # Load audit report if available
    audit_data = {}
    if audit_report_path and os.path.exists(audit_report_path):
        try:
            audit_df = pd.read_excel(audit_report_path, sheet_name=0)
            for _, arow in audit_df.iterrows():
                key = (
                    str(arow.get("File", arow.get("file_path", ""))),
                    str(arow.get("Title", arow.get("issue_type", ""))),
                )
                audit_data[key] = {
                    "status": str(arow.get("Status", arow.get("status", "UNKNOWN"))),
                    "lines_changed": arow.get("Lines_Changed", arow.get("lines_changed", 0)),
                }
        except Exception as e:
            logger.warning("Could not load audit report: %s", e)

    # Also check fixer_results dict for per-file status
    fixer_file_status = {}
    if fixer_results and isinstance(fixer_results, dict):
        for item in fixer_results.get("results", []):
            if isinstance(item, dict):
                key = (str(item.get("file_path", "")), str(item.get("issue_type", "")))
                fixer_file_status[key] = item.get("status", "UNKNOWN")

    for _, row in feedback_df.iterrows():
        file_name = str(row.get("File", ""))
        issue_title = str(row.get("Title", row.get("Category", "")))
        action = str(row.get("Action", "Auto-fix"))
        notes = str(row.get("Notes", ""))

        # Look up fixer result
        lookup_key = (file_name, issue_title)
        audit_info = audit_data.get(lookup_key, {})
        fixer_status = (
            audit_info.get("status")
            or fixer_file_status.get(lookup_key, "PENDING")
        )

        rows.append({
            "File": file_name,
            "Issue": issue_title,
            "Severity": str(row.get("Severity", "")),
            "Human_Action": action,
            "Human_Notes": notes,
            "Fixer_Status": fixer_status,
            "Lines_Changed": audit_info.get("lines_changed", 0),
        })

    if not rows:
        return pd.DataFrame(columns=[
            "File", "Issue", "Severity", "Human_Action",
            "Human_Notes", "Fixer_Status", "Lines_Changed",
        ])

    return pd.DataFrame(rows)


# ═══════════════════════════════════════════════════════════════════════════════
#  Summary statistics
# ═══════════════════════════════════════════════════════════════════════════════

def compute_summary_stats(df: pd.DataFrame) -> Dict[str, Any]:
    """Compute summary statistics from an analysis results DataFrame."""
    total = len(df)

    # Normalize severity to Title Case for consistent matching
    severity_counts: Dict[str, int] = {}
    if "Severity" in df.columns:
        normalised = df["Severity"].astype(str).str.strip().str.title()
        severity_counts = normalised.value_counts().to_dict()

    action_counts: Dict[str, int] = {}
    if "Action" in df.columns:
        action_counts = df["Action"].value_counts().to_dict()

    to_fix = action_counts.get("Auto-fix", 0) + action_counts.get("Review", 0)
    to_skip = action_counts.get("Skip", 0)

    category_counts: Dict[str, int] = {}
    if "Category" in df.columns:
        category_counts = df["Category"].value_counts().to_dict()

    return {
        "total": total,
        "critical": severity_counts.get("Critical", 0),
        "high": severity_counts.get("High", 0),
        "medium": severity_counts.get("Medium", 0),
        "low": severity_counts.get("Low", 0),
        "to_fix": to_fix,
        "to_skip": to_skip,
        "to_review": action_counts.get("Review", 0) + action_counts.get("Manual Review", 0),
        "categories": category_counts,
        "unique_files": df["File"].nunique() if "File" in df.columns else 0,
    }
