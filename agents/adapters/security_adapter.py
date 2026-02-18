"""Security vulnerability scanning using Flawfinder (with regex fallback)."""

import csv
import logging
import re
import subprocess
import tempfile
import shutil
import os
from typing import Any, Dict, List, Optional, Tuple

from agents.adapters.base_adapter import BaseStaticAdapter


# ── Regex-based dangerous-function database ───────────────────────────────
# Format: (pattern, level 0-5, CWE, category, warning message)
_DANGEROUS_FUNCTIONS: List[Tuple[re.Pattern, int, str, str, str]] = [
    # Buffer overflow — high risk
    (re.compile(r'\bgets\s*\('), 5, "CWE-120", "buffer",
     "gets() is extremely dangerous — no bounds checking. Use fgets() instead."),
    (re.compile(r'\bstrcpy\s*\('), 4, "CWE-120", "buffer",
     "strcpy() does not check buffer bounds. Use strncpy() or strlcpy()."),
    (re.compile(r'\bstrcat\s*\('), 4, "CWE-120", "buffer",
     "strcat() does not check buffer bounds. Use strncat() or strlcat()."),
    (re.compile(r'\bsprintf\s*\('), 4, "CWE-120", "buffer",
     "sprintf() does not check buffer bounds. Use snprintf()."),
    (re.compile(r'\bvsprintf\s*\('), 4, "CWE-120", "buffer",
     "vsprintf() does not check buffer bounds. Use vsnprintf()."),
    (re.compile(r'\bwcscpy\s*\('), 4, "CWE-120", "buffer",
     "wcscpy() does not check buffer bounds. Use wcsncpy()."),
    (re.compile(r'\bwcscat\s*\('), 4, "CWE-120", "buffer",
     "wcscat() does not check buffer bounds. Use wcsncat()."),

    # Format string — high risk
    (re.compile(r'\bprintf\s*\(\s*[a-zA-Z_]\w*\s*\)'), 4, "CWE-134", "format",
     "printf() with variable format string — potential format string vulnerability."),
    (re.compile(r'\bfprintf\s*\([^,]+,\s*[a-zA-Z_]\w*\s*\)'), 4, "CWE-134", "format",
     "fprintf() with variable format string — potential format string vulnerability."),
    (re.compile(r'\bsyslog\s*\([^,]+,\s*[a-zA-Z_]\w*\s*\)'), 4, "CWE-134", "format",
     "syslog() with variable format string — potential format string vulnerability."),

    # Memory functions — medium risk
    (re.compile(r'\bmemcpy\s*\('), 2, "CWE-120", "buffer",
     "memcpy() — ensure size parameter is correctly bounded."),
    (re.compile(r'\bmemmove\s*\('), 2, "CWE-120", "buffer",
     "memmove() — ensure size parameter is correctly bounded."),

    # Dangerous input/output
    (re.compile(r'\bscanf\s*\('), 4, "CWE-120", "input",
     "scanf() can overflow buffers. Use width specifiers or fgets()+sscanf()."),
    (re.compile(r'\bfscanf\s*\('), 3, "CWE-120", "input",
     "fscanf() can overflow buffers. Use width specifiers."),
    (re.compile(r'\bsscanf\s*\('), 3, "CWE-120", "input",
     "sscanf() can overflow buffers. Use width specifiers."),

    # Temporary files — medium risk
    (re.compile(r'\bmktemp\s*\('), 4, "CWE-377", "tmpfile",
     "mktemp() is insecure (race condition). Use mkstemp()."),
    (re.compile(r'\btmpnam\s*\('), 3, "CWE-377", "tmpfile",
     "tmpnam() is insecure (race condition). Use mkstemp()."),
    (re.compile(r'\btempnam\s*\('), 3, "CWE-377", "tmpfile",
     "tempnam() is insecure (race condition). Use mkstemp()."),

    # Race conditions
    (re.compile(r'\baccess\s*\('), 4, "CWE-362", "race",
     "access() has TOCTOU race condition. Use faccessat() or open()+fstat()."),

    # Random number generator
    (re.compile(r'\brand\s*\('), 3, "CWE-338", "random",
     "rand() is not cryptographically secure. Use arc4random() or /dev/urandom."),
    (re.compile(r'\bsrand\s*\('), 2, "CWE-338", "random",
     "srand() seeds a weak PRNG. Use a cryptographic RNG for security purposes."),

    # Exec/system — medium risk
    (re.compile(r'\bsystem\s*\('), 4, "CWE-78", "shell",
     "system() — potential command injection. Validate/sanitize input."),
    (re.compile(r'\bpopen\s*\('), 4, "CWE-78", "shell",
     "popen() — potential command injection. Validate/sanitize input."),
    (re.compile(r'\bexecl\s*\('), 3, "CWE-78", "shell",
     "exec family — ensure arguments are not user-controlled."),
    (re.compile(r'\bexeclp\s*\('), 3, "CWE-78", "shell",
     "exec family — ensure arguments are not user-controlled."),
    (re.compile(r'\bexecvp\s*\('), 3, "CWE-78", "shell",
     "exec family — ensure arguments are not user-controlled."),

    # Integer overflow
    (re.compile(r'\batoi\s*\('), 2, "CWE-190", "integer",
     "atoi() does not detect overflow. Use strtol() with error checking."),
    (re.compile(r'\batol\s*\('), 2, "CWE-190", "integer",
     "atol() does not detect overflow. Use strtol() with error checking."),
]


class SecurityAdapter(BaseStaticAdapter):
    """
    Scans C/C++ code for security vulnerabilities using Flawfinder.

    Analyzes source code for common security issues, CWE violations,
    and dangerous function calls. Reports findings by severity and CWE.

    Falls back to regex-based scanning when Flawfinder is not installed.
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
                "Flawfinder not found — using regex fallback. "
                "For best results: pip install flawfinder"
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
        using_fallback = not self.flawfinder_available

        # Step 2: Filter to C/C++ files
        c_cpp_suffixes = (".c", ".cpp", ".cc", ".cxx", ".h", ".hpp", ".hxx")
        cpp_files = [
            entry
            for entry in file_cache
            if entry.get("file_relative_path", "").lower().endswith(c_cpp_suffixes)
        ]

        if not cpp_files:
            return self._empty_result("No C/C++ files to analyze")

        # Step 3: Scan each file
        all_findings = []
        files_scanned = 0

        for entry in cpp_files:
            file_path = entry.get("file_relative_path", "unknown")
            source_code = entry.get("source", "")

            if not source_code.strip():
                continue

            if self.flawfinder_available:
                # ── Flawfinder path ──
                tmp_path = None
                try:
                    with tempfile.NamedTemporaryFile(
                        mode="w", suffix=".cpp", delete=False, encoding='utf-8'
                    ) as tmp:
                        tmp.write(source_code)
                        tmp_path = tmp.name

                    cmd = [self.flawfinder_path, "--csv", "--columns", tmp_path]
                    result = subprocess.run(
                        cmd, capture_output=True, text=True, timeout=30,
                    )

                    if result.returncode != 0 and not result.stdout:
                        self.logger.error(f"Flawfinder failed on {file_path}: {result.stderr}")
                        continue

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
                    if tmp_path and os.path.exists(tmp_path):
                        try:
                            os.unlink(tmp_path)
                        except OSError:
                            pass
            else:
                # ── Regex fallback path ──
                try:
                    findings = self._regex_scan_file(file_path, source_code)
                    if findings:
                        all_findings.extend(findings)
                    files_scanned += 1
                except Exception as e:
                    self.logger.error(f"Error regex-scanning {file_path}: {e}")

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

    def _regex_scan_file(
        self, file_path: str, source_code: str
    ) -> List[Dict[str, Any]]:
        """Scan a single file for dangerous functions using regex patterns."""
        findings = []
        lines = source_code.splitlines()
        for line_idx, line in enumerate(lines, start=1):
            # Skip comments (simple heuristic)
            stripped = line.lstrip()
            if stripped.startswith("//") or stripped.startswith("/*") or stripped.startswith("*"):
                continue
            for pattern, level, cwe, category, warning in _DANGEROUS_FUNCTIONS:
                if pattern.search(line):
                    findings.append({
                        "file": file_path,
                        "line": line_idx,
                        "level": level,
                        "warning": warning,
                        "cwe": cwe,
                        "category": category,
                        "context": stripped[:120],
                        "suggestion": "",
                        "name": pattern.pattern.split(r'\b')[1] if r'\b' in pattern.pattern else "",
                    })
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