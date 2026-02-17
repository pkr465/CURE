"""
C/C++ code quality analysis (enhanced)
"""

import os
import re
from typing import Dict, List, Any, Tuple
from collections import Counter


class QualityAnalyzer:
    """
    Analyzes C/C++ code quality focusing on:
    - ScanBan-aligned banned function detection (BAxxx)
    - Non-standard reimplementation identification (RExxx)
    - Security vulnerability patterns and misuse heuristics (HExxx)
    - Code style consistency (line length, TODO/FIXME/HACK, tabs, trailing whitespace)
    - Best practices adherence for string/memory and formatting APIs
    """

    # C/C++ file extensions
    C_EXTS = {".c", ".cpp", ".cc", ".cxx", ".c++"}
    H_EXTS = {".h", ".hpp", ".hh", ".hxx", ".h++"}

    def __init__(self, codebase_path: str = None, project_root: str = None):
        """Initialize quality analyzer."""
        self.codebase_path = codebase_path or os.getcwd()
        self.project_root = project_root or os.getcwd()
        self._file_cache: List[Dict[str, Any]] = []

    def analyze(self, file_cache: List[Dict[str, Any]]) -> Dict[str, Any]:
        """
        Analyze C/C++ code quality with ScanBan-aligned rules.

        Args:
            file_cache: List of processed C/C++ file entries with at least:
                - "suffix": file extension
                - "source": file contents
                - one of: "rel_path", "path", "file_relative_path", "file_name"

        Returns:
            Quality analysis results with score, grade, metrics, issues, and violations.
        """
        self._file_cache = file_cache or []
        return self._calculate_quality_score()

    def _calculate_quality_score(self) -> Dict[str, Any]:
        """
        Calculate code quality score based on static analysis for C/C++ (ScanBan-aligned).

        - Detects banned APIs (BAxxx), non-standard reimplementations (RExxx),
          and security-focused heuristics (HExxx).
        - Adds basic style checks (line length, TODO/FIXME/HACK, tabs, trailing whitespace).
        - Each violation includes relative file path, line, column, rule code, severity, and snippet.
        - Score is severity-weighted and normalized by number of files.
        """
        if not self._file_cache:
            return {
                "score": 0.0,
                "grade": "F",
                "issues": ["No files cached"],
                "metrics": {
                    "total_violations": 0,
                    "violations_per_file": 0.0,
                    "files_analyzed": 0,
                    "rule_counts": {},
                    "severity_counts": {},
                },
                "violations": [],
            }

        # Helper: relative path
        def _rel_path(entry: Dict[str, Any]) -> str:
            p = (
                entry.get("rel_path")
                or entry.get("path")
                or entry.get("file_relative_path")
                or entry.get("file_name")
                or ""
            )
            root = (
                getattr(self, "project_root", None)
                or getattr(self, "root_dir", None)
                or str(self.codebase_path)
            )
            try:
                return os.path.relpath(p, root) if p else ""
            except Exception:
                return p or ""

        # Helper: map absolute offset to (line, column)
        def _line_col_from_abs(lines: List[str], abs_pos: int) -> Tuple[int, int]:
            running = 0
            for i, line in enumerate(lines):
                next_running = running + len(line) + 1  # assume '\n'
                if next_running > abs_pos:
                    col = abs_pos - running + 1
                    return i, max(1, col)
                running = next_running
            return max(0, len(lines) - 1), 1

        # Filter C/C++ files
        all_exts = {ext.lower() for ext in (self.C_EXTS | self.H_EXTS)}
        c_cpp_files: List[Dict[str, Any]] = []
        for f in self._file_cache:
            suffix = (f.get("suffix") or "").lower()
            if suffix in all_exts:
                c_cpp_files.append(f)

        print(
            f"DEBUG quality: Found {len(c_cpp_files)} C/C++ files out of "
            f"{len(self._file_cache)} total"
        )
        print(
            f"DEBUG quality: Extensions being checked: {sorted(self.C_EXTS | self.H_EXTS)}"
        )
        if c_cpp_files:
            print("DEBUG quality: Sample C/C++ files:")
            for f in c_cpp_files[:3]:
                print(
                    f" - {f.get('file_relative_path', 'unknown')} "
                    f"({f.get('suffix', 'unknown')})"
                )

        if not c_cpp_files:
            return {
                "score": 100.0,
                "grade": "A",
                "metrics": {
                    "total_violations": 0,
                    "violations_per_file": 0.0,
                    "files_analyzed": 0,
                    "rule_counts": {},
                    "severity_counts": {},
                },
                "issues": [
                    "No C/C++ files found for quality analysis. "
                    f"Extensions checked: {sorted(self.C_EXTS | self.H_EXTS)}"
                ],
                "violations": [],
            }

        # Rules: banned APIs (ScanBan/QPSI-aligned)
        banned_patterns = [
            # Copying strings (BA001..)
            (r"\bstrcpy\s*\(", "BA001", "Use of strcpy() - banned; use strlcpy()", "high"),
            (r"\bstrncpy\s*\(", "BA003", "Use of strncpy() - banned; use strlcpy()", "high"),
            (r"\bstrcat\s*\(", "BA002", "Use of strcat() - banned; use strlcat()", "high"),
            (r"\bstrncat\s*\(", "BA004", "Use of strncat() - banned; use strlcat()", "high"),
            (r"\bwstrcpy\s*\(", "BA005", "Use of wstrcpy() - banned; use wstrlcpy()", "high"),
            (r"\bwstrncpy\s*\(", "BA007", "Use of wstrncpy() - banned; use wstrlcpy()", "high"),
            (r"\bwstrcat\s*\(", "BA006", "Use of wstrcat() - banned; use wstrlcat()", "high"),
            (r"\bwstrncat\s*\(", "BA008", "Use of wstrncat() - banned; use wstrlcat()", "high"),
            (r"\bwcscpy\s*\(", "BA009", "Use of wcscpy() - banned; use wcslcpy()", "high"),
            (r"\bwcsncpy\s*\(", "BA011", "Use of wcsncpy() - banned; use wcslcpy()", "high"),
            (r"\bwcscat\s*\(", "BA010", "Use of wcscat() - banned; use wcslcat()", "high"),
            (r"\bwcsncat\s*\(", "BA012", "Use of wcsncat() - banned; use wcslcat()", "high"),
            # Formatted to string (BA013..)
            (r"\bsprintf\s*\(", "BA013", "Use of sprintf() - banned; use snprintf() (kernel: scnprintf())", "high"),
            (r"\bvsprintf\s*\(", "BA014", "Use of vsprintf() - banned; use vsnprintf() (kernel: vscnprintf())", "high"),
            (r"\bwsprintf\s*\(", "BA015", "Use of wsprintf() - banned; use wsnprintf()", "high"),
            # Other string manipulation (BA016..)
            (r"\bgets\s*\(", "BA016", "Use of gets() - banned; use fgets()", "critical"),
            (r"\bscanf\s*\(", "BA017", "Use of scanf() - banned; prefer fgets()+strtol/strtoul", "high"),
            (r"\bstrtok\s*\(", "BA018", "Use of strtok() - banned; use strtok_r()", "high"),
            # Memory (treated as disallowed / to-be-banned)
            (r"\bmemcpy\s*\(", "BA019", "Use of memcpy() - use memscpy() for binary or strlcpy for strings", "high"),
            (r"\bmemmove\s*\(", "BA020", "Use of memmove() - use memsmove() for binary", "high"),
        ]

        # Non-standard reimplementations
        reimpl_patterns = [
            (r"\bOSCRTLSTRNCAT\b", "RE001", "Non-standard strncat reimplementation - replace with approved strlcat()", "medium"),
            (r"\bOSCRTLSTRNCPY_S\b", "RE002", "Non-standard strncpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\bOSCRTLSTRCPY\b", "RE003", "Non-standard strcpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\brtxStrcat\b", "RE004", "Non-standard strcat reimplementation - replace with approved strlcat()", "medium"),
            (r"\brtxStrncat\b", "RE005", "Non-standard strncat reimplementation - replace with approved strlcat()", "medium"),
            (r"\brtxStrcpy\b", "RE006", "Non-standard strcpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\brtxStrncpy\b", "RE007", "Non-standard strncpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\brtxUTF8Strcpy\b", "RE008", "Non-standard UTF8 strcpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\brtxUTF8Strncpy\b", "RE009", "Non-standard UTF8 strncpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\bpbm_wstrncpy\b", "RE010", "Non-standard wide strncpy reimplementation - replace with approved wstrlcpy()", "medium"),
            (r"\bpbm_wstrncat\b", "RE011", "Non-standard wide strncat reimplementation - replace with approved wstrlcat()", "medium"),
            (r"\bsmd_strncpy\b", "RE012", "Non-standard strncpy reimplementation - replace with approved strlcpy()", "medium"),
            (r"\b_f_strcpytolower\b", "RE013", "Non-standard strcpy variant - use strlcpy()+tolower", "medium"),
            (r"\bstd_vstrlprintf\b", "RE014", "Non-standard vstrlprintf - replace with approved CoreBSP libstd", "low"),
            (r"\bstd_strlprintf\b", "RE015", "Non-standard strlprintf - replace with approved CoreBSP libstd", "low"),
            (r"\bstd_strlcpy\b", "RE016", "Non-standard strlcpy - replace with approved CoreBSP libstd", "low"),
            (r"\bstd_strlcat\b", "RE017", "Non-standard strlcat - replace with approved CoreBSP libstd", "low"),
            (r"\bstd_snprintf\b", "RE018", "Non-standard snprintf - replace with approved CoreBSP/libstd", "low"),
            (r"\bfs_strlcpy\b", "RE019", "Non-standard strlcpy - replace with approved CoreBSP libstd", "low"),
            (r"\bfs_strlcat\b", "RE020", "Non-standard strlcat - replace with approved CoreBSP libstd", "low"),
            (r"\bgllc_strlcat\b", "RE021", "Non-standard strlcat - replace with approved CoreBSP libstd", "low"),
            (r"\bw_char_strlcpy\b", "RE022", "Non-standard wide strlcpy - replace with approved CoreBSP libstd", "low"),
        ]

        # Heuristic/warning patterns (HExxx)
        heuristic_patterns = [
            # strlen() used as size for "safe" APIs
            (r"\bstrl(?:cpy|cat)\s*\([^,]+,\s*[^,]+,\s*strlen\s*\(", "HX001", "strlcpy/strlcat size uses strlen() - prefer sizeof(dest)", "medium"),
            (r"\b(?:snprintf|vsnprintf|wsnprintf)\s*\([^,]+,\s*strlen\s*\(", "HX002", "snprintf size uses strlen() - prefer sizeof(dest)", "medium"),
            # memscpy/memsmove likely misused to copy strings
            (r"\bmems(?:cpy|move)\s*\([^,]+,\s*[^,]+,\s*[^,]+,\s*strlen\s*\(", "HX003", "memscpy/memsmove with strlen() - likely string copy; use strlcpy/strlcat", "medium"),
            # memcpy with strlen (unsafe)
            (r"\bmemcpy\s*\([^,]+,\s*[^,]+,\s*strlen\s*\(", "HX004", "memcpy with strlen() - unsafe; use strlcpy or memscpy", "high"),
            # memscpy/memsmove sizes identical
            (r"\bmems(?:cpy|move)\s*\([^,]+,\s*([^,()]+)\s*,\s*[^,]+,\s*\1\s*\)", "HX005", "mems* src and dst sizes identical expressions - verify they are actual src/dst sizes", "medium"),
            # Variable/tainted format strings
            (r"\bprintf\s*\(\s*[A-Za-z_]\w*", "HX006", "printf with variable format - potential format-string vulnerability", "high"),
            (r"\bfprintf\s*\(\s*[^,]+,\s*[A-Za-z_]\w*", "HX007", "fprintf with variable format - potential format-string vulnerability", "high"),
            (r"\bsyslog\s*\(\s*[^,]+,\s*[A-Za-z_]\w*", "HX008", "syslog with variable format - potential format-string vulnerability", "high"),
            # scanf family: %s without width specifier
            (
                r'\b(?:scanf|sscanf|fscanf)\s*\(\s*"(?:[^"%]|%%)*%s(?:[^"%]|%%)*"',
                "HX009",
                "scanf/sscanf with %s and no width - buffer overflow risk",
                "high",
            ),
            # Return value ignored
            (r"^[ \t]*strl(?:cpy|cat)\s*\([^;]*\);\s*$", "HX010", "strlcpy/strlcat return value not checked (possible truncation)", "low"),
            (r"^[ \t]*(?:snprintf|vsnprintf|wsnprintf)\s*\([^;]*\);\s*$", "HX011", "snprintf family return value not checked (truncation/encoding)", "low"),
            (r"^[ \t]*mems(?:cpy|move)\s*\([^;]*\);\s*$", "HX012", "memscpy/memsmove return value not checked (truncation)", "low"),
        ]

        # Style rules
        STYLE_LINE_LEN_RULE = "STYLE-LINELENGTH"
        STYLE_TODO_RULE = "STYLE-TODO"
        STYLE_TABS_RULE = "STYLE-TABS"
        STYLE_TRAILWS_RULE = "STYLE-TRAILWS"

        # Compile regexes
        compiled_banned = [
            (re.compile(p), code, msg, sev) for p, code, msg, sev in banned_patterns
        ]
        compiled_reimpl = [
            (re.compile(p), code, msg, sev) for p, code, msg, sev in reimpl_patterns
        ]
        compiled_heur = [
            (re.compile(p, re.MULTILINE), code, msg, sev)
            for p, code, msg, sev in heuristic_patterns
        ]

        violations: List[Dict[str, Any]] = []
        issues: List[str] = []

        # Severity weights for scoring
        severity_weights = {
            "critical": 5.0,
            "high": 4.0,
            "medium": 2.0,
            "low": 1.0,
        }

        # Aggregation counters
        rule_counts: Counter = Counter()
        severity_counts: Counter = Counter()
        severity_weight_sum = 0.0

        print(f"DEBUG quality: Starting analysis of {len(c_cpp_files)} files")

        for entry in c_cpp_files:
            source = entry.get("source", "") or ""
            if not source.strip():
                print(
                    f"DEBUG quality: Skipping empty file: "
                    f"{entry.get('file_relative_path', 'unknown')}"
                )
                continue

            relpath = _rel_path(entry)
            lines = source.splitlines()
            print(
                f"DEBUG quality: Analyzing {relpath} "
                f"({len(lines)} lines, {len(source)} chars)"
            )

            # Style: long lines (>120 chars)
            long_line_count = 0
            tab_count = 0
            trail_ws_count = 0
            todo_count = 0

            for i, line in enumerate(lines, start=1):
                if len(line) > 120:
                    violations.append(
                        {
                            "rule": STYLE_LINE_LEN_RULE,
                            "severity": "low",
                            "message": f"Line exceeds 120 chars ({len(line)})",
                            "file": relpath,
                            "line": i,
                            "column": 1,
                            "snippet": line[:200],
                        }
                    )
                    rule_counts[STYLE_LINE_LEN_RULE] += 1
                    severity_counts["low"] += 1
                    severity_weight_sum += severity_weights["low"]
                    long_line_count += 1

                if "\t" in line:
                    violations.append(
                        {
                            "rule": STYLE_TABS_RULE,
                            "severity": "low",
                            "message": "Tab character used; prefer spaces per coding standard",
                            "file": relpath,
                            "line": i,
                            "column": line.find("\t") + 1,
                            "snippet": line[:200],
                        }
                    )
                    rule_counts[STYLE_TABS_RULE] += 1
                    severity_counts["low"] += 1
                    severity_weight_sum += severity_weights["low"]
                    tab_count += 1

                if line.rstrip() != line:
                    violations.append(
                        {
                            "rule": STYLE_TRAILWS_RULE,
                            "severity": "low",
                            "message": "Trailing whitespace",
                            "file": relpath,
                            "line": i,
                            "column": len(line),
                            "snippet": line[:200],
                        }
                    )
                    rule_counts[STYLE_TRAILWS_RULE] += 1
                    severity_counts["low"] += 1
                    severity_weight_sum += severity_weights["low"]
                    trail_ws_count += 1

                if re.search(
                    r"(?:\/\/|#).*(TODO|FIXME|HACK)|\/\*.*(TODO|FIXME|HACK).*?\*\/",
                    line,
                    re.IGNORECASE,
                ):
                    violations.append(
                        {
                            "rule": STYLE_TODO_RULE,
                            "severity": "low",
                            "message": "TODO/FIXME/HACK comment",
                            "file": relpath,
                            "line": i,
                            "column": 1,
                            "snippet": line.strip()[:200],
                        }
                    )
                    rule_counts[STYLE_TODO_RULE] += 1
                    severity_counts["low"] += 1
                    severity_weight_sum += severity_weights["low"]
                    todo_count += 1

            # Banned APIs
            banned_count = 0
            for cre, code, msg, sev in compiled_banned:
                for m in cre.finditer(source):
                    li, col = _line_col_from_abs(lines, m.start())
                    violations.append(
                        {
                            "rule": code,
                            "severity": sev,
                            "message": msg,
                            "file": relpath,
                            "line": li + 1,
                            "column": col,
                            "snippet": lines[li].strip()[:200]
                            if li < len(lines)
                            else "",
                        }
                    )
                    rule_counts[code] += 1
                    severity_counts[sev] += 1
                    severity_weight_sum += severity_weights.get(sev, 1.0)
                    banned_count += 1

            # Non-standard reimplementations
            reimpl_count = 0
            for cre, code, msg, sev in compiled_reimpl:
                for m in cre.finditer(source):
                    li, col = _line_col_from_abs(lines, m.start())
                    violations.append(
                        {
                            "rule": code,
                            "severity": sev,
                            "message": msg,
                            "file": relpath,
                            "line": li + 1,
                            "column": col,
                            "snippet": lines[li].strip()[:200]
                            if li < len(lines)
                            else "",
                        }
                    )
                    rule_counts[code] += 1
                    severity_counts[sev] += 1
                    severity_weight_sum += severity_weights.get(sev, 1.0)
                    reimpl_count += 1

            # Heuristic violations
            heur_count = 0
            for cre, code, msg, sev in compiled_heur:
                for m in cre.finditer(source):
                    li, col = _line_col_from_abs(lines, m.start())
                    violations.append(
                        {
                            "rule": code,
                            "severity": sev,
                            "message": msg,
                            "file": relpath,
                            "line": li + 1,
                            "column": col,
                            "snippet": lines[li].strip()[:200]
                            if li < len(lines)
                            else "",
                        }
                    )
                    rule_counts[code] += 1
                    severity_counts[sev] += 1
                    severity_weight_sum += severity_weights.get(sev, 1.0)
                    heur_count += 1

            print(
                f"DEBUG quality: File {relpath} - Long lines: {long_line_count}, "
                f"TABS: {tab_count}, TrailingWS: {trail_ws_count}, TODOs: {todo_count}, "
                f"Banned: {banned_count}, Reimpl: {reimpl_count}, Heuristics: {heur_count}"
            )

        total_violations = sum(rule_counts.values())
        analyzed_files = len(c_cpp_files)
        violations_per_file = total_violations / max(1, analyzed_files)

        print(
            f"DEBUG quality: Total violations entries: {total_violations}, "
            f"severity-weight sum: {severity_weight_sum:.1f}"
        )

        # Scoring:
        # - Base 100
        # - Penalize based on average severity weight per file
        avg_severity_weight_per_file = severity_weight_sum / max(1, analyzed_files)
        # Scale: each "unit" of average severity weight reduces score by 2 points
        score = 100.0 - 2.0 * avg_severity_weight_per_file
        score = max(0.0, min(100.0, score))

        if violations_per_file > 10:
            issues.append(
                f"High violation density: {violations_per_file:.1f} issues per C/C++ file"
            )
        if severity_counts.get("critical", 0) > 0:
            issues.append(
                f"{severity_counts['critical']} critical banned API or security issue(s) detected"
            )
        if severity_counts.get("high", 0) > 0:
            issues.append(
                f"{severity_counts['high']} high severity issues detected (banned APIs or security-sensitive patterns)"
            )
        if total_violations == 0 and analyzed_files > 0:
            issues.append("No quality violations detected - excellent code quality!")

        grade = self._score_to_grade(score)

        metrics = {
            "total_violations": total_violations,
            "violations_per_file": round(violations_per_file, 2),
            "files_analyzed": analyzed_files,
            "rule_counts": dict(rule_counts),
            "severity_counts": dict(severity_counts),
            "avg_severity_weight_per_file": round(avg_severity_weight_per_file, 2),
        }

        return {
            "score": round(score, 1),
            "grade": grade,
            "metrics": metrics,
            "issues": issues,
            "violations": violations,
        }

    @staticmethod
    def _score_to_grade(score: float) -> str:
        """Convert numeric score to letter grade."""
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"