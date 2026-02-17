"""Security vulnerability scanning using Flawfinder."""

import csv
import logging
import subprocess
import tempfile
import shutil
import os
from typing import Any, Dict, List, Optional

from agents.adapters.base_adapter import BaseStaticAdapter

class SecurityAdapter(BaseStaticAdapter):
    """
    Scans C/C++ code for security vulnerabilities using Flawfinder.

    Analyzes source code for common security issues, CWE violations,
    and dangerous function calls. Reports findings by severity and CWE.
    """

    def __init__(self, debug: bool = False):
        """
        Initialize security adapter.

        Args:
            debug: Enable debug logging if True.
        """
        super().__init__("security", debug=debug)
        
        # Check for the CLI tool explicitly instead of checking for the python module
        self.flawfinder_path = shutil.which("flawfinder")
        self.flawfinder_available = self.flawfinder_path is not None
        
        if not self.flawfinder_available:
            self.logger.warning(
                "Flawfinder executable not found in PATH. Install with: pip install flawfinder"
            )
        else:
            self.logger.debug(f"Flawfinder found at: {self.flawfinder_path}")

    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        ccls_navigator: Optional[Any] = None,
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Analyze C/C++ files for security vulnerabilities.

        Args:
            file_cache: List of file entries with "file_relative_path" and "source" keys.
            ccls_navigator: Optional CCLS navigator (unused here).
            dependency_graph: Optional dependency graph (unused here).

        Returns:
            Standard analysis result dict with score, grade, metrics, issues, details.
        """
        # Step 1: Check tool availability
        if not self.flawfinder_available:
            return self._handle_tool_unavailable(
                "Flawfinder", "Install with: pip install flawfinder"
            )

        # Step 2: Filter to C/C++ files
        c_cpp_suffixes = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx")
        cpp_files = [
            entry
            for entry in file_cache
            if entry.get("file_relative_path", "").lower().endswith(c_cpp_suffixes)
        ]

        if not cpp_files:
            return self._empty_result("No C/C++ files to analyze")

        # Step 3: Scan each file with flawfinder
        all_findings = []
        files_scanned = 0

        for entry in cpp_files:
            file_path = entry.get("file_relative_path", "unknown")
            source_code = entry.get("source", "")
            
            if not source_code.strip():
                continue

            tmp_path = None
            try:
                # Write source to temporary file
                with tempfile.NamedTemporaryFile(
                    mode="w", suffix=".cpp", delete=False, encoding='utf-8'
                ) as tmp:
                    tmp.write(source_code)
                    tmp_path = tmp.name

                if self.debug:
                    self.logger.debug(f"Running flawfinder on {file_path}...")

                # Run flawfinder on the temp file
                # --csv: Output format
                # --columns: Show headers (crucial for DictReader)
                # --dataonly: Don't show headers (We DO want headers for DictReader, so don't use --dataonly if parsing via DictReader logic unless we handle it)
                # Flawfinder default with --csv includes headers.
                cmd = [self.flawfinder_path, "--csv", "--columns", tmp_path]
                
                result = subprocess.run(
                    cmd,
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if result.returncode != 0 and not result.stdout:
                    self.logger.error(f"Flawfinder failed on {file_path}: {result.stderr}")
                    continue

                # Parse CSV output
                findings = self._parse_flawfinder_csv(
                    result.stdout, file_path, source_code
                )
                
                if findings:
                    all_findings.extend(findings)
                
                files_scanned += 1

            except subprocess.TimeoutExpired:
                self.logger.error(f"Flawfinder timeout on {file_path}")
            except Exception as e:
                self.logger.error(f"Error scanning {file_path}: {e}")
            finally:
                # Clean up temp file
                if tmp_path and os.path.exists(tmp_path):
                    try:
                        os.unlink(tmp_path)
                    except OSError:
                        pass

        # Step 4: Handle "No Issues Found" case explicitly (FIX for 0 Score)
        if not all_findings:
            if self.debug:
                self.logger.info("Flawfinder scan completed: 0 issues found.")
            
            return {
                "score": 100.0,
                "grade": "A",
                "metrics": {
                    "tool_available": True,
                    "files_analyzed": files_scanned,
                    "total_findings": 0,
                    "critical_count": 0,
                    "high_count": 0,
                    "medium_count": 0,
                    "low_count": 0,
                    "cwe_breakdown": {},
                    "top_categories": {}
                },
                "issues": ["No security issues detected"],
                "details": [],
                "tool_available": True,
            }

        # Step 5: Categorize findings by severity
        critical_count = 0
        high_count = 0
        medium_count = 0
        low_count = 0
        cwe_breakdown = {}
        details = []

        for finding in all_findings:
            level = finding["level"]

            if level >= 5:
                critical_count += 1
                severity = "critical"
            elif level == 4:
                high_count += 1
                severity = "high"
            elif level == 3:
                medium_count += 1
                severity = "medium"
            else:  # 0-2
                low_count += 1
                severity = "low"

            # Track CWE breakdown
            cwe = finding.get("cwe", "")
            if cwe:
                cwe_breakdown[cwe] = cwe_breakdown.get(cwe, 0) + 1

            # Create detail entry
            description = finding["warning"]
            category = finding.get("category", "security")

            detail = self._make_detail(
                file=finding["file"],
                function=finding.get("context", ""),
                line=finding["line"],
                description=description,
                severity=severity,
                category=category,
                cwe=cwe,
            )
            details.append(detail)

        # Step 6: Calculate score
        # Start at 100 and deduct points based on severity
        score = 100.0
        score -= critical_count * 15  # Increased penalty for critical
        score -= high_count * 5
        score -= medium_count * 2
        score -= low_count * 0.5      # Reduced penalty for low noise
        score = max(0.0, min(100.0, score))

        # Step 7: Compute metrics
        top_categories = self._get_top_categories(details, top_n=5)

        metrics = {
            "tool_available": True,
            "files_analyzed": files_scanned,
            "total_findings": len(all_findings),
            "critical_count": critical_count,
            "high_count": high_count,
            "medium_count": medium_count,
            "low_count": low_count,
            "cwe_breakdown": cwe_breakdown,
            "top_categories": top_categories,
        }

        # Step 8: Build issues list
        issues = []
        if critical_count > 0:
            issues.append(f"Found {critical_count} critical security issue(s)")
        if high_count > 0:
            issues.append(f"Found {high_count} high-severity security issue(s)")
        if medium_count > 0:
            issues.append(f"Found {medium_count} medium-severity security issue(s)")
        if low_count > 0:
            issues.append(f"Found {low_count} low-severity security issue(s)")
        
        if not issues:
            issues = ["No significant security issues detected"]

        grade = self._score_to_grade(score)

        return {
            "score": score,
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
            "details": details,
            "tool_available": True,
        }

    def _parse_flawfinder_csv(
        self, csv_output: str, original_file_path: str, source_code: str
    ) -> List[Dict[str, Any]]:
        """
        Parse Flawfinder CSV output.

        Expected columns: File, Line, Column, Level, Category, Name, Warning, Suggestion, Note, CWEs, Context, Fingerprint
        """
        findings = []
        try:
            # Skip initial lines if they aren't headers (sometimes tool output has banners)
            lines = csv_output.strip().splitlines()
            if not lines:
                return []
                
            reader = csv.DictReader(lines)
            
            # Verify we have valid headers
            if not reader.fieldnames or "File" not in reader.fieldnames:
                self.logger.warning(f"Flawfinder CSV headers missing or invalid: {reader.fieldnames}")
                return []

            for row in reader:
                # Filter out rows that don't look like data
                if not row or not row.get("File"):
                    continue

                try:
                    line_num = int(row.get("Line", 0))
                    level = int(row.get("Level", 1))
                except ValueError:
                    continue

                # Extract CWE from CWEs column
                cwe_str = row.get("CWEs", "")
                primary_cwe = ""
                if cwe_str:
                    parts = cwe_str.split(",")
                    if parts:
                        primary_cwe = parts[0].strip()

                finding = {
                    "file": original_file_path,
                    "line": line_num,
                    "level": level,
                    "warning": row.get("Warning", "Unknown warning"),
                    "cwe": primary_cwe,
                    "category": row.get("Category", "security"),
                    "context": row.get("Context", ""),
                    "suggestion": row.get("Suggestion", ""),
                    "name": row.get("Name", ""),
                }
                findings.append(finding)

        except Exception as e:
            self.logger.error(f"Error parsing flawfinder CSV: {e}")

        return findings

    @staticmethod
    def _get_top_categories(
        details: List[Dict[str, Any]], top_n: int = 5
    ) -> Dict[str, int]:
        """Get top security issue categories."""
        category_counts = {}
        for detail in details:
            cat = detail.get("category", "unknown")
            category_counts[cat] = category_counts.get(cat, 0) + 1

        sorted_cats = sorted(
            category_counts.items(), key=lambda x: x[1], reverse=True
        )
        return dict(sorted_cats[:top_n])