"""
CURE — Codebase Update & Refactor Engine
Codebase Patch Agent

Analyses patches (unified diffs) against source files to identify issues
introduced by the patch. Uses :class:`CodebaseLLMAgent` and optionally
:class:`StaticAnalyzerAgent` to compare original vs. patched code, then
writes findings to a ``patch_<filename>`` tab in
``detailed_code_review.xlsx``.
"""

import logging
import os
import re
import tempfile
import shutil
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

# ---------------------------------------------------------------------------
# Graceful imports
# ---------------------------------------------------------------------------

try:
    from agents.codebase_llm_agent import CodebaseLLMAgent
    LLM_AGENT_AVAILABLE = True
except ImportError:
    LLM_AGENT_AVAILABLE = False
    CodebaseLLMAgent = None

try:
    from agents.codebase_static_agent import StaticAnalyzerAgent
    STATIC_AGENT_AVAILABLE = True
except ImportError:
    STATIC_AGENT_AVAILABLE = False
    StaticAnalyzerAgent = None

try:
    from agents.adapters import (
        ASTComplexityAdapter,
        SecurityAdapter,
        DeadCodeAdapter,
        CallGraphAdapter,
        FunctionMetricsAdapter,
    )
    ADAPTERS_AVAILABLE = True
except ImportError:
    ADAPTERS_AVAILABLE = False

try:
    from utils.common.excel_writer import ExcelWriter, ExcelStyle
    EXCEL_WRITER_AVAILABLE = True
except ImportError:
    EXCEL_WRITER_AVAILABLE = False
    ExcelWriter = None
    ExcelStyle = None

try:
    from utils.common.llm_tools import LLMTools
    LLM_TOOLS_AVAILABLE = True
except ImportError:
    LLM_TOOLS_AVAILABLE = False
    LLMTools = None

try:
    from utils.parsers.global_config_parser import GlobalConfig
    GLOBAL_CONFIG_AVAILABLE = True
except ImportError:
    GLOBAL_CONFIG_AVAILABLE = False
    GlobalConfig = None

try:
    from dependency_builder.config import DependencyBuilderConfig
    DEP_CONFIG_AVAILABLE = True
except ImportError:
    DEP_CONFIG_AVAILABLE = False
    DependencyBuilderConfig = None

# HITL support (optional)
try:
    from hitl import HITLContext, HITL_AVAILABLE
except ImportError:
    HITLContext = None
    HITL_AVAILABLE = False


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------

@dataclass
class PatchHunk:
    """Represents a single hunk from a unified diff."""

    orig_start: int
    orig_count: int
    new_start: int
    new_count: int
    header: str = ""
    removed_lines: List[str] = field(default_factory=list)
    added_lines: List[str] = field(default_factory=list)
    context_lines: List[str] = field(default_factory=list)
    raw_lines: List[str] = field(default_factory=list)


@dataclass
class PatchFinding:
    """A single issue found in the patched code."""

    file_path: str
    line_number: int
    severity: str
    category: str
    description: str
    code_before: str = ""
    code_after: str = ""
    introduced_by_patch: bool = True
    issue_source: str = "patch"


# ---------------------------------------------------------------------------
# Patch Agent
# ---------------------------------------------------------------------------

class CodebasePatchAgent:
    """Analyse patches against source files to identify introduced issues.

    Workflow:
    1. Read the original source file.
    2. Parse the unified diff via :meth:`_parse_patch`.
    3. Apply the patch to produce a patched source string.
    4. Run :class:`CodebaseLLMAgent` on **both** original and patched files.
       (Injects Issue Identification Rules from constraints)
    5. Optionally run static analysis adapters on the patched file.
    6. Diff findings to isolate issues **introduced** by the patch.
    7. Write a ``patch_<filename>`` tab to ``detailed_code_review.xlsx``.
    """

    def __init__(
        self,
        file_path: str,
        patch_file: str,
        output_dir: str = "./out",
        config: Optional[Any] = None,
        llm_tools: Optional[Any] = None,
        dep_config: Optional[Any] = None,
        hitl_context: Optional[Any] = None,
        enable_adapters: bool = False,
        verbose: bool = False,
        constraints_dir: str = "agents/constraints",
        exclude_dirs: Optional[List[str]] = None,
        exclude_globs: Optional[List[str]] = None,
        custom_constraints: Optional[List[str]] = None,
    ) -> None:
        self.file_path = Path(file_path).resolve()
        self.patch_file = Path(patch_file).resolve()
        self.output_dir = Path(output_dir).resolve()
        self.config = config
        self.llm_tools = llm_tools
        self.dep_config = dep_config
        self.hitl_context = hitl_context
        self.enable_adapters = enable_adapters
        self.verbose = verbose
        self.exclude_dirs = exclude_dirs or []
        self.exclude_globs = exclude_globs or []
        self.custom_constraints = custom_constraints or []

        # Derive useful names
        self.filename = self.file_path.name
        self.filename_stem = self.file_path.stem

        # Constraint Directory Setup
        self.constraints_dir = Path(constraints_dir)
        if not self.constraints_dir.is_absolute():
            # Attempt to resolve relative to CWD first, then fallback to script location
            if not self.constraints_dir.exists():
                # Fallback: check relative to this script's directory
                script_dir = Path(__file__).parent.resolve()
                potential_dir = script_dir / constraints_dir
                if potential_dir.exists():
                    self.constraints_dir = potential_dir

        # Ensure output dir exists
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Setup Logging — use module logger only (do NOT call basicConfig which
        # installs a StreamHandler on the root logger and floods the UI console)
        self.logger = logging.getLogger(__name__)

        # Temp directory for analysis artefacts
        self._temp_dir: Optional[Path] = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def run_analysis(
        self,
        excel_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute the full patch analysis pipeline.

        Args:
            excel_path: Path to ``detailed_code_review.xlsx`` to update.

        Returns:
            Dictionary with analysis results.
        """
        self.logger.info(f"Patch Agent: analysing {self.filename} with {self.patch_file.name}")

        # -- Validate inputs ------------------------------------------------
        if not self.file_path.exists():
            self.logger.error(f"Source file not found: {self.file_path}")
            return {"status": "error", "message": f"Source file not found: {self.file_path}"}

        if not self.patch_file.exists():
            self.logger.error(f"Patch file not found: {self.patch_file}")
            return {"status": "error", "message": f"Patch file not found: {self.patch_file}"}

        try:
            original_content = self.file_path.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            self.logger.error(f"Failed to read source file: {exc}")
            return {"status": "error", "message": str(exc)}

        try:
            patch_content = self.patch_file.read_text(encoding="utf-8", errors="replace")
        except Exception as exc:
            self.logger.error(f"Failed to read patch file: {exc}")
            return {"status": "error", "message": str(exc)}

        # -- Parse and apply patch ------------------------------------------
        hunks = self._parse_patch(patch_content)
        if not hunks:
            self.logger.warning("No hunks found in patch file")
            return {"status": "warning", "message": "No hunks found in patch", "findings": []}

        self.logger.info(f"  Parsed {len(hunks)} hunk(s) from patch")

        try:
            patched_content = self._apply_patch(original_content, hunks)
        except Exception as e:
            self.logger.error(f"Failed to apply patch: {e}")
            return {"status": "error", "message": f"Patch application failed: {e}"}

        # -- Setup temp directories -----------------------------------------
        try:
            self._temp_dir = Path(tempfile.mkdtemp(prefix="cure_patch_"))
            return self._run_pipeline(
                original_content, patched_content, hunks, excel_path
            )
        except Exception as e:
            self.logger.error(f"Pipeline execution failed: {e}")
            return {"status": "error", "message": str(e)}
        finally:
            # Cleanup temp dir safely
            if self._temp_dir and self._temp_dir.exists():
                try:
                    shutil.rmtree(str(self._temp_dir), ignore_errors=True)
                except Exception as e:
                    self.logger.warning(f"Failed to cleanup temp dir: {e}")

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _run_pipeline(
        self,
        original_content: str,
        patched_content: str,
        hunks: List[PatchHunk],
        excel_path: Optional[str],
    ) -> Dict[str, Any]:
        """Run the full analysis pipeline."""

        # Write temp files for analysis
        orig_dir = self._temp_dir / "original"
        patched_dir = self._temp_dir / "patched"
        orig_dir.mkdir(parents=True, exist_ok=True)
        patched_dir.mkdir(parents=True, exist_ok=True)

        orig_file = orig_dir / self.filename
        patched_file = patched_dir / self.filename
        orig_file.write_text(original_content, encoding="utf-8")
        patched_file.write_text(patched_content, encoding="utf-8")

        # -- Run LLM analysis on both versions ------------------------------
        original_issues: List[Dict] = []
        patched_issues: List[Dict] = []

        if LLM_AGENT_AVAILABLE:
            self.logger.info("  Running LLM analysis on original file...")
            original_issues = self._run_llm_analysis(
                str(orig_dir), self.filename, "original"
            )
            self.logger.info(f"  Original: {len(original_issues)} issue(s) found")

            self.logger.info("  Running LLM analysis on patched file...")
            patched_issues = self._run_llm_analysis(
                str(patched_dir), self.filename, "patched"
            )
            self.logger.info(f"  Patched: {len(patched_issues)} issue(s) found")
        else:
            self.logger.warning("  CodebaseLLMAgent not available — skipping LLM analysis")

        # -- Run static adapters on patched file (optional) -----------------
        static_issues: List[Dict] = []
        if self.enable_adapters and ADAPTERS_AVAILABLE:
            self.logger.info("  Running static adapters on patched file...")
            static_issues = self._run_static_analysis(str(patched_dir), self.filename)
            self.logger.info(f"  Static analysis: {len(static_issues)} issue(s) found")

        # Merge patched_issues + static_issues
        all_patched_issues = patched_issues + static_issues

        # -- Diff findings --------------------------------------------------
        new_findings = self._diff_findings(original_issues, all_patched_issues, hunks)
        self.logger.info(f"  New issues introduced by patch: {len(new_findings)}")

        # -- Write to Excel -------------------------------------------------
        final_excel = excel_path or str(self.output_dir / "detailed_code_review.xlsx")
        self._update_excel(final_excel, new_findings)

        return {
            "status": "success",
            "filename": self.filename,
            "patch_file": str(self.patch_file),
            "original_issue_count": len(original_issues),
            "patched_issue_count": len(all_patched_issues),
            "new_issue_count": len(new_findings),
            "findings": [self._finding_to_dict(f) for f in new_findings],
            "excel_path": final_excel,
            "hunks_parsed": len(hunks),
        }

    # ------------------------------------------------------------------
    # Patch parsing
    # ------------------------------------------------------------------

    _HUNK_RE = re.compile(
        r"^@@\s+-(\d+)(?:,(\d+))?\s+\+(\d+)(?:,(\d+))?\s+@@(.*)$"
    )

    def _parse_patch(self, patch_text: str) -> List[PatchHunk]:
        """Parse a unified diff into a list of :class:`PatchHunk` objects."""
        hunks: List[PatchHunk] = []
        current_hunk: Optional[PatchHunk] = None

        for line in patch_text.splitlines():
            m = self._HUNK_RE.match(line)
            if m:
                # Save the previous hunk
                if current_hunk is not None:
                    hunks.append(current_hunk)

                current_hunk = PatchHunk(
                    orig_start=int(m.group(1)),
                    orig_count=int(m.group(2) or 1),
                    new_start=int(m.group(3)),
                    new_count=int(m.group(4) or 1),
                    header=m.group(5).strip(),
                )
                continue

            if current_hunk is None:
                # Header lines (---, +++, diff --git, etc.) — skip
                continue

            current_hunk.raw_lines.append(line)

            if line.startswith("-"):
                current_hunk.removed_lines.append(line[1:])
            elif line.startswith("+"):
                current_hunk.added_lines.append(line[1:])
            elif line.startswith(" ") or line == "":
                current_hunk.context_lines.append(line[1:] if line.startswith(" ") else line)

        # Don't forget the last hunk
        if current_hunk is not None:
            hunks.append(current_hunk)

        return hunks

    # ------------------------------------------------------------------
    # Patch application
    # ------------------------------------------------------------------

    def _apply_patch(self, source: str, hunks: List[PatchHunk]) -> str:
        """Apply parsed hunks to the original source to reconstruct patched content.

        Uses a line-based approach: replaces each hunk's original region
        with the new region in order, adjusting offsets as we go.
        """
        lines = source.splitlines(keepends=True)
        offset = 0  # cumulative line offset from previous hunk applications

        for hunk in hunks:
            # Convert to 0-based indexing
            start = max(0, hunk.orig_start - 1 + offset)
            end = start + hunk.orig_count

            # Build replacement lines from the raw diff
            new_lines: List[str] = []
            for raw_line in hunk.raw_lines:
                if raw_line.startswith("+"):
                    new_lines.append(raw_line[1:] + "\n")
                elif raw_line.startswith(" ") or raw_line == "":
                    # Handle preserved lines
                    content = raw_line[1:] if raw_line.startswith(" ") else raw_line
                    new_lines.append(content + "\n")
                # Lines starting with "-" are removed (not added to new_lines)

            # Safety check: if start is beyond EOF, append (though likely a bad patch)
            if start > len(lines):
                 lines.extend(new_lines)
            else:
                 # Replace the region
                 lines[start:end] = new_lines

            # Update offset for next hunk
            offset += len(new_lines) - hunk.orig_count

        return "".join(lines)

    # ------------------------------------------------------------------
    # LLM analysis
    # ------------------------------------------------------------------

    def _run_llm_analysis(
        self, temp_dir: str, filename: str, label: str
    ) -> List[Dict]:
        """Run CodebaseLLMAgent on a single file in a temp directory.

        Returns a list of issue dicts extracted from the agent's results.
        """
        if not LLM_AGENT_AVAILABLE:
            return []

        try:
            analysis_out = os.path.join(self._temp_dir, f"llm_{label}")
            os.makedirs(analysis_out, exist_ok=True)

            # [CONSTRAINT UPDATE]: Pass the specific constraints directory to the LLM agent.
            # CodebaseLLMAgent uses 'Issue Identification Rules' by default.
            agent = CodebaseLLMAgent(
                codebase_path=temp_dir,
                output_dir=analysis_out,
                config=self.config,
                llm_tools=self.llm_tools,
                exclude_dirs=self.exclude_dirs,
                exclude_globs=self.exclude_globs,
                max_files=1,
                file_to_fix=filename,
                hitl_context=self.hitl_context,
                constraints_dir=str(self.constraints_dir),
                custom_constraints=self.custom_constraints,
            )

            output_filename = f"patch_{label}_{self.filename_stem}.xlsx"
            
            # [FIX] Handle return type variability (Dict vs Str)
            result = agent.run_analysis(output_filename=output_filename)
            
            report_path = ""
            if isinstance(result, dict):
                report_path = result.get("report_path") or result.get("excel_path") or ""
            elif isinstance(result, str):
                report_path = result
            
            if not report_path:
                 self.logger.warning(f"LLM analysis ({label}) returned no report path.")
                 return []

            # Extract issues from the generated Excel
            return self._extract_issues_from_excel(report_path, label)
        except Exception as exc:
            self.logger.warning(f"LLM analysis ({label}) failed: {exc}")
            if self.verbose:
                import traceback
                self.logger.error(traceback.format_exc())
            return []

    def _extract_issues_from_excel(
        self, excel_path: str, label: str
    ) -> List[Dict]:
        """Extract issues from a generated Excel report."""
        issues: List[Dict] = []

        if not excel_path or not Path(excel_path).exists():
            return issues

        try:
            import pandas as pd

            # Try reading the Analysis sheet
            try:
                df = pd.read_excel(excel_path, sheet_name="Analysis", header=0)
            except Exception:
                # Try first sheet if Analysis doesn't exist
                df = pd.read_excel(excel_path, header=0)

            # [FIX] Robust column normalization
            df.columns = [str(c).strip().lower() for c in df.columns]
            
            # Map standard keys to dataframe columns
            col_map = {
                "file": ["file", "file_path", "filename"],
                "line": ["line", "line_number", "linenumber"],
                "severity": ["severity", "level", "priority"],
                "category": ["category", "issue_type", "type", "rule"],
                "description": ["description", "message", "desc", "rationale"],
                "code": ["code", "snippet", "bad_code", "code_snippet"],
                "fixed_code": ["fixed_code", "suggestion", "fix"]
            }

            def get_val(row, keys, default=None):
                for k in keys:
                    if k in row:
                        val = row[k]
                        return val if pd.notna(val) else default
                return default

            for _, row in df.iterrows():
                file_val = get_val(row, col_map["file"])
                if not file_val:
                    continue

                line_val = get_val(row, col_map["line"], 0)
                try:
                    line_num = int(line_val)
                except (ValueError, TypeError):
                    line_num = 0

                issue = {
                    "file_path": str(file_val),
                    "line_number": line_num,
                    "severity": str(get_val(row, col_map["severity"], "medium")),
                    "category": str(get_val(row, col_map["category"], "")),
                    "description": str(get_val(row, col_map["description"], "")),
                    "code": str(get_val(row, col_map["code"], "")),
                    "fixed_code": str(get_val(row, col_map["fixed_code"], "")),
                    "source": label,
                }
                issues.append(issue)

        except ImportError:
            self.logger.warning("pandas not available — cannot extract issues from Excel")
        except Exception as exc:
            self.logger.warning(f"Failed to extract issues from {excel_path}: {exc}")

        return issues

    # ------------------------------------------------------------------
    # Static analysis
    # ------------------------------------------------------------------

    def _run_static_analysis(
        self, temp_dir: str, filename: str
    ) -> List[Dict]:
        """Run static analysis adapters on the patched file."""
        if not ADAPTERS_AVAILABLE:
            return []

        issues: List[Dict] = []
        try:
            # Build a minimal file cache for adapters
            from agents.core.file_processor import FileProcessor

            processor = FileProcessor(
                codebase_path=temp_dir,
                exclude_dirs=[],
            )
            file_cache = processor.process_files()

            adapters = [
                ("ast_complexity", ASTComplexityAdapter()),
                ("security", SecurityAdapter()),
            ]

            for name, adapter in adapters:
                try:
                    result = adapter.analyze(
                        file_cache, ccls_navigator=None, dependency_graph={}
                    )
                    if result.get("tool_available", False):
                        # Extract issues from adapter results
                        for finding in result.get("findings", result.get("issues", [])):
                            issues.append({
                                "file_path": finding.get("file", filename),
                                "line_number": finding.get("line", 0),
                                "severity": finding.get("severity", "medium"),
                                "category": f"static_{name}",
                                "description": finding.get("description", finding.get("message", "")),
                                "code": finding.get("code", ""),
                                "source": f"static_{name}",
                            })
                except Exception as exc:
                    self.logger.warning(f"Adapter {name} failed: {exc}")

        except ImportError:
            self.logger.warning("FileProcessor not available — skipping static analysis")
        except Exception as exc:
            self.logger.warning(f"Static analysis failed: {exc}")

        return issues

    # ------------------------------------------------------------------
    # Findings diff
    # ------------------------------------------------------------------

    @staticmethod
    def _fingerprint_issue(issue: Dict) -> str:
        """Create a fingerprint for an issue to enable deduplication.

        Uses: (filename, line_range_bucket, category, description_prefix).
        Line numbers are bucketed into ranges of 5 to handle minor drift.
        """
        filename = Path(issue.get("file_path", "")).name
        line = issue.get("line_number", 0)
        line_bucket = (line // 5) * 5  # bucket into groups of 5
        category = issue.get("category", "").lower().strip()
        desc = issue.get("description", "")[:80].lower().strip()

        return f"{filename}|{line_bucket}|{category}|{desc}"

    def _diff_findings(
        self,
        original_issues: List[Dict],
        patched_issues: List[Dict],
        hunks: List[PatchHunk],
    ) -> List[PatchFinding]:
        """Identify issues that are NEW in the patched version.

        An issue is considered 'new' if it:
        1. Was NOT present in the original (by fingerprint), OR
        2. Falls within or near a hunk's modified line range.
        """
        # Build fingerprint set from original
        orig_fingerprints = {self._fingerprint_issue(i) for i in original_issues}

        # Build a set of line ranges modified by hunks
        modified_ranges: List[Tuple[int, int]] = []
        for hunk in hunks:
            start = hunk.new_start
            end = start + hunk.new_count
            modified_ranges.append((start, end))

        def _in_modified_range(line: int) -> bool:
            """Check if a line falls within or near a modified hunk range."""
            for start, end in modified_ranges:
                if (start - 3) <= line <= (end + 3):
                    return True
            return False

        new_findings: List[PatchFinding] = []

        for issue in patched_issues:
            fp = self._fingerprint_issue(issue)
            line_num = issue.get("line_number", 0)

            # Issue is new if not in original OR in a modified range
            if fp not in orig_fingerprints or _in_modified_range(line_num):
                finding = PatchFinding(
                    file_path=issue.get("file_path", self.filename),
                    line_number=line_num,
                    severity=issue.get("severity", "medium"),
                    category=issue.get("category", ""),
                    description=issue.get("description", ""),
                    code_before=issue.get("code", ""),
                    code_after=issue.get("fixed_code", ""),
                    introduced_by_patch=True,
                    issue_source=issue.get("source", "patch"),
                )
                new_findings.append(finding)

        return new_findings

    # ------------------------------------------------------------------
    # Excel output
    # ------------------------------------------------------------------

    def _update_excel(
        self, excel_path: str, findings: List[PatchFinding]
    ) -> None:
        """Write patch findings to a ``patch_<filename>`` tab in the Excel file."""
        if not EXCEL_WRITER_AVAILABLE:
            self.logger.warning("ExcelWriter not available — writing findings as JSON instead")
            self._write_findings_json(findings)
            return

        sheet_name = f"patch_{self.filename_stem}"
        # Truncate sheet name to Excel's 31-char limit
        if len(sheet_name) > 31:
            sheet_name = sheet_name[:31]

        headers = [
            "File",
            "Line",
            "Severity",
            "Category",
            "Description",
            "Code_Before",
            "Code_After",
            "Introduced_By_Patch",
            "Issue_Source",
        ]

        data_rows: List[List[Any]] = []
        for f in findings:
            data_rows.append([
                f.file_path,
                f.line_number,
                f.severity,
                f.category,
                f.description,
                f.code_before,
                f.code_after,
                "YES" if f.introduced_by_patch else "NO",
                f.issue_source,
            ])

        try:
            # Try to open existing workbook and add a new sheet
            excel_file = Path(excel_path)

            if excel_file.exists():
                try:
                    import openpyxl
                    wb = openpyxl.load_workbook(str(excel_file))

                    # Remove existing sheet with the same name if present
                    if sheet_name in wb.sheetnames:
                        del wb[sheet_name]

                    ws = wb.create_sheet(title=sheet_name)

                    # Write header row
                    for col_idx, header in enumerate(headers, 1):
                        ws.cell(row=1, column=col_idx, value=header)

                    # Write data rows
                    for row_idx, row_data in enumerate(data_rows, 2):
                        for col_idx, value in enumerate(row_data, 1):
                            ws.cell(row=row_idx, column=col_idx, value=value)

                    # [FIX] Safer column width adjustment
                    for col_idx, header in enumerate(headers, 1):
                        max_len = len(header)
                        for row_data in data_rows:
                            # Ensure we don't index out of bounds if row_data is short
                            if (col_idx - 1) < len(row_data):
                                val_len = len(str(row_data[col_idx - 1]))
                                max_len = max(max_len, min(val_len, 60))
                        ws.column_dimensions[
                            openpyxl.utils.get_column_letter(col_idx)
                        ].width = max_len + 4

                    wb.save(str(excel_file))
                    self.logger.info(
                        f"Updated {excel_path} with '{sheet_name}' tab ({len(findings)} findings)"
                    )
                    return

                except PermissionError:
                    self.logger.error(f"Permission denied writing to {excel_path}. File may be open.")
                    # Fallback to JSON if file is locked
                    self._write_findings_json(findings)
                    return
                except Exception as exc:
                    self.logger.warning(f"Failed to update existing Excel: {exc} — attempting create new")

            # Create new workbook with ExcelWriter
            writer = ExcelWriter(str(excel_path))
            writer.add_table_sheet(
                headers=headers,
                data_rows=data_rows,
                sheet_name=sheet_name,
                status_column="Severity",
            )
            writer.save()
            self.logger.info(
                f"Created {excel_path} with '{sheet_name}' tab ({len(findings)} findings)"
            )

        except Exception as exc:
            self.logger.error(f"Failed to write Excel: {exc}")
            self._write_findings_json(findings)

    def _write_findings_json(self, findings: List[PatchFinding]) -> None:
        """Fallback: write findings as JSON."""
        import json

        json_path = self.output_dir / f"patch_{self.filename_stem}_findings.json"
        data = [self._finding_to_dict(f) for f in findings]
        try:
            with open(json_path, "w", encoding="utf-8") as fp:
                json.dump(data, fp, indent=2, default=str)
            self.logger.info(f"Findings written to {json_path}")
        except Exception as e:
            self.logger.error(f"Failed to write JSON fallback: {e}")

    @staticmethod
    def _finding_to_dict(finding: PatchFinding) -> Dict[str, Any]:
        """Convert a PatchFinding to a plain dict."""
        return {
            "file_path": finding.file_path,
            "line_number": finding.line_number,
            "severity": finding.severity,
            "category": finding.category,
            "description": finding.description,
            "code_before": finding.code_before,
            "code_after": finding.code_after,
            "introduced_by_patch": finding.introduced_by_patch,
            "issue_source": finding.issue_source,
        }

    # ------------------------------------------------------------------
    # Utilities
    # ------------------------------------------------------------------

    def get_patch_summary(self) -> Dict[str, Any]:
        """Parse the patch and return a summary without running full analysis."""
        if not self.patch_file.exists():
            return {"error": "Patch file not found"}

        try:
            patch_content = self.patch_file.read_text(encoding="utf-8", errors="replace")
            hunks = self._parse_patch(patch_content)

            total_added = sum(len(h.added_lines) for h in hunks)
            total_removed = sum(len(h.removed_lines) for h in hunks)

            return {
                "patch_file": str(self.patch_file),
                "target_file": str(self.file_path),
                "hunk_count": len(hunks),
                "lines_added": total_added,
                "lines_removed": total_removed,
                "net_change": total_added - total_removed,
                "hunks": [
                    {
                        "header": h.header,
                        "orig_range": f"{h.orig_start},{h.orig_count}",
                        "new_range": f"{h.new_start},{h.new_count}",
                        "added": len(h.added_lines),
                        "removed": len(h.removed_lines),
                    }
                    for h in hunks
                ],
            }
        except Exception as e:
             self.logger.error(f"Failed to generate patch summary: {e}")
             return {"error": str(e)}


# ---------------------------------------------------------------------------
# CLI entry point
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="CURE Codebase Patch Agent — Analyse patches for introduced issues",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )

    parser.add_argument(
        "--file-path",
        required=True,
        help="Path to the original source file",
    )
    parser.add_argument(
        "--patch-file",
        required=True,
        help="Path to the .patch/.diff file (unified diff format)",
    )
    parser.add_argument(
        "--excel-path",
        default=None,
        help="Path to detailed_code_review.xlsx to update",
    )
    parser.add_argument(
        "-d", "--out-dir",
        default="./out",
        help="Output directory",
    )
    parser.add_argument(
        "--config-file",
        default=None,
        help="Path to global_config.yaml",
    )
    parser.add_argument(
        "--llm-model",
        default=None,
        help="LLM model in provider::model format",
    )
    parser.add_argument(
        "--enable-adapters",
        action="store_true",
        default=False,
        help="Run deep static analysis adapters on patched file",
    )
    parser.add_argument(
        "--constraints-dir",
        default="agents/constraints",
        help="Directory containing constraint files",
    )
    parser.add_argument(
        "--exclude-dirs",
        default="",
        help="Comma-separated list of directories to exclude",
    )
    parser.add_argument(
        "--exclude-globs",
        default="",
        help="Comma-separated glob patterns to exclude",
    )
    parser.add_argument(
        "--include-custom-constraints",
        nargs="*",
        default=[],
        help="Additional constraint .md files to load",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        default=False,
        help="Enable verbose logging",
    )

    args = parser.parse_args()

    # Load config
    config = None
    if GLOBAL_CONFIG_AVAILABLE and args.config_file:
        try:
            config = GlobalConfig(args.config_file)
        except Exception as e:
            print(f"WARNING: Could not load config: {e}")

    # Setup LLM tools
    llm_tools = None
    if LLM_TOOLS_AVAILABLE:
        try:
            if args.llm_model:
                llm_tools = LLMTools(model=args.llm_model)
            elif config:
                model_str = config.get("llm.model")
                llm_tools = LLMTools(model=model_str) if model_str else LLMTools()
            else:
                llm_tools = LLMTools()
        except Exception as e:
            print(f"WARNING: Could not initialise LLMTools: {e}")

    # Run agent
    exclude_dirs = [d.strip() for d in args.exclude_dirs.split(",") if d.strip()]
    exclude_globs = [g.strip() for g in args.exclude_globs.split(",") if g.strip()]

    agent = CodebasePatchAgent(
        file_path=args.file_path,
        patch_file=args.patch_file,
        output_dir=args.out_dir,
        config=config,
        llm_tools=llm_tools,
        enable_adapters=args.enable_adapters,
        verbose=args.verbose,
        constraints_dir=args.constraints_dir,
        exclude_dirs=exclude_dirs,
        exclude_globs=exclude_globs,
        custom_constraints=args.include_custom_constraints,
    )

    result = agent.run_analysis(excel_path=args.excel_path)

    print(f"\n{'='*60}")
    print(f" Patch Analysis Results: {agent.filename}")
    print(f"{'='*60}")
    print(f"  Status:           {result.get('status')}")
    print(f"  Hunks parsed:     {result.get('hunks_parsed', 0)}")
    print(f"  Original issues:  {result.get('original_issue_count', 0)}")
    print(f"  Patched issues:   {result.get('patched_issue_count', 0)}")
    print(f"  NEW issues:       {result.get('new_issue_count', 0)}")
    print(f"  Excel output:     {result.get('excel_path', 'N/A')}")
    print(f"{'='*60}")

    if result.get("findings"):
        print(f"\n  Findings:")
        for i, f in enumerate(result["findings"], 1):
            print(f"    {i}. [{f['severity']}] {f['category']} — {f['description'][:80]}")
            print(f"       Line {f['line_number']} in {f['file_path']}")

    sys.exit(0 if result.get("status") == "success" else 1)