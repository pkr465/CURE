import re
import math
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


class MaintainabilityAnalyzer:
    """
    MaintainabilityAnalyzer: C/C++ maintainability analysis.

    - Per-file analysis:
      - LOC (total / code / comment / blank)
      - Comment ratio
      - Cyclomatic complexity (heuristic)
      - Halstead volume (approximate)
      - Maintainability Index (SEI-style, scaled to 0–100)
      - Doxygen documentation of functions
      - Header guards and #pragma once
      - Legal header detection (simple heuristics)
      - #endif comment checks
      - Banned / future-banned APIs
      - Formatting issues: long lines, tabs, trailing whitespace, TODO/FIXME
      - Single-line if detection
      - Switch default checks

    - Aggregated metrics:
      - Average / min / max MI
      - Documentation ratios
      - Banned API issues
      - Header guard / legal header issues
      - Lists of problematic files and hotspots

    API:
      analyze(file_cache, dependency_graph=None) -> {
         "score": float,
         "grade": str,
         "metrics": {...},
         "issues": [str, ...]
      }
    """

    # Regexes / patterns
    _BANNED_API_PATTERNS = [
        re.compile(r"\bstrcpy\b"),
        re.compile(r"\bstrcat\b"),
        re.compile(r"\bstrncpy\b"),
        re.compile(r"\bstrncat\b"),
        re.compile(r"\bsprintf\b"),
        re.compile(r"\bvsprintf\b"),
        re.compile(r"\bgets\b"),
        re.compile(r"\bstrtok\b"),
        re.compile(r"\bscanf\b"),
    ]

    _FUTURE_BANNED_API_PATTERNS = [
        re.compile(r"\bmemcpy\b"),
        re.compile(r"\bmemmove\b"),
        re.compile(r"\bmemset\b"),
        re.compile(r"\bstrn?cmp\b"),
    ]

    _TODO_PATTERN = re.compile(r"\b(TODO|FIXME|XXX)\b", re.IGNORECASE)
    _LONG_LINE_LIMIT = 120
    _TAB_PATTERN = re.compile(r"\t")
    _TRAILING_WS_PATTERN = re.compile(r"[ \t]+$")

    # Very simplified function signature heuristic
    _FUNC_SIG_RE = re.compile(
        r"""^[^\n;{}]*\b[A-Za-z_][A-Za-z0-9_]*\s*  # return type-ish
            \b[A-Za-z_][A-Za-z0-9_]*\s*           # function name
            \([^;{}]*\)\s*                        # params
            (const\s*)?[\{;]                      # const + brace or semicolon
        """,
        re.MULTILINE | re.VERBOSE,
    )

    # Doxygen styles
    _DOXY_BLOCK_RE = re.compile(r"/\*\*[\s\S]*?\*/", re.MULTILINE)
    _DOXY_LINE_RE = re.compile(r"^\s*///", re.MULTILINE)

    # Comment / string patterns
    _BLOCK_COMMENT_RE = re.compile(r"/\*[\s\S]*?\*/")
    _LINE_COMMENT_RE = re.compile(r"//.*?$", re.MULTILINE)
    _STRING_RE = re.compile(r'"([^"\\]|\\.)*"|\'([^\'\\]|\\.)*\'')

    def __init__(
        self,
        codebase_path: str,
        project_root: Optional[str] = None,
        debug: bool = False,
    ) -> None:
        self.codebase_path = Path(codebase_path)
        self.project_root = Path(project_root) if project_root else self.codebase_path
        self.debug = debug

    # ---------------------------------------------------------------------
    # Public API
    # ---------------------------------------------------------------------

    def analyze(
        self,
        file_cache: List[Dict[str, Any]],
        dependency_graph: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        """
        Perform maintainability analysis on a file cache + dependency graph.

        file_cache: entries from StaticAnalyzerAgent file discovery, each with:
            {
              "file_name": str,
              "file_relative_path": str,
              "suffix": str,
              "language": "c" | "cpp" | "header" | "unknown",
              "source": str,
              ...
            }

        dependency_graph: optional graph for cross-checks (unused for now, but
                          included for future enhancements).

        Returns:
            {
              "score": float,
              "grade": str,
              "metrics": {...},
              "issues": [str, ...]
            }
        """
        per_file_metrics: List[Dict[str, Any]] = []

        for entry in file_cache:
            suffix = entry.get("suffix", "").lower()
            if suffix not in {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".hxx"}:
                continue

            rel_path = entry.get("file_relative_path") or entry.get("rel_path") or entry.get("path")
            source = entry.get("source", "")

            try:
                file_metrics = self._analyze_single_file(rel_path, suffix, source)
                per_file_metrics.append(file_metrics)
            except Exception as e:
                if self.debug:
                    print(f"[MaintainabilityAnalyzer] Error analyzing {rel_path}: {e}")

        aggregated, issues, score = self._aggregate_metrics(per_file_metrics)
        grade = self._score_to_grade(score)

        return {
            "score": round(score, 1),
            "grade": grade,
            "metrics": aggregated,
            "issues": issues,
        }

    # ---------------------------------------------------------------------
    # Per-file analysis
    # ---------------------------------------------------------------------

    def _analyze_single_file(self, rel_path: str, suffix: str, source: str) -> Dict[str, Any]:
        lines = source.splitlines()
        total_lines = len(lines)

        # Line classification
        loc_total, loc_code, loc_comment, loc_blank = self._classify_lines(lines)
        comment_ratio = loc_comment / max(1, total_lines)

        # Strip comments and strings for subsequent metrics
        code_no_comments, code_no_comments_or_strings = self._strip_comments_and_strings(source)

        # Cyclomatic complexity
        cyclomatic = self._compute_cyclomatic_complexity(code_no_comments_or_strings)

        # Halstead volume
        halstead_volume = self._compute_halstead_volume(code_no_comments_or_strings)

        # Maintainability Index
        mi = self._compute_maintainability_index(
            volume=halstead_volume,
            complexity=cyclomatic,
            loc=loc_code if loc_code > 0 else total_lines,
        )

        # Doxygen and function docs
        func_count, documented_funcs = self._compute_doxygen_stats(source, code_no_comments)
        doc_ratio = documented_funcs / max(1, func_count)

        # Header guards / legal header / endif comments (for headers only)
        has_header_guard = False
        has_pragma_once = False
        header_guard_issue = ""
        legal_header_issue = ""
        endif_comment_issues: List[str] = []
        if suffix in {".h", ".hh", ".hpp", ".hxx"}:
            has_header_guard, has_pragma_once = self._check_header_guard(lines)
            if not (has_header_guard or has_pragma_once):
                header_guard_issue = "Missing header guard or #pragma once"

            legal_header_issue = self._check_legal_header(lines)
            endif_comment_issues = self._check_endif_comments(lines)

        # Banned / future-banned APIs
        banned_hits, future_banned_hits = self._scan_banned_apis(code_no_comments_or_strings)

        # Formatting: long lines, tabs, trailing whitespace, TODO/FIXME
        formatting_issues = self._scan_formatting(lines)

        # Single-line if detection
        single_line_ifs = self._detect_single_line_ifs(code_no_comments_or_strings)

        # Switch default checks
        switch_missing_default = self._check_switch_default(code_no_comments_or_strings)

        file_metrics: Dict[str, Any] = {
            "file": rel_path,
            "suffix": suffix,
            "total_lines": total_lines,
            "code_lines": loc_code,
            "comment_lines": loc_comment,
            "blank_lines": loc_blank,
            "comment_ratio": comment_ratio,
            "cyclomatic_complexity": cyclomatic,
            "halstead_volume": halstead_volume,
            "maintainability_index": mi,
            "function_count": func_count,
            "documented_functions": documented_funcs,
            "documentation_ratio": doc_ratio,
            "has_header_guard": has_header_guard,
            "has_pragma_once": has_pragma_once,
            "header_guard_issue": header_guard_issue,
            "legal_header_issue": legal_header_issue,
            "endif_comment_issues": endif_comment_issues,
            "banned_api_hits": banned_hits,
            "future_banned_api_hits": future_banned_hits,
            "formatting_issues": formatting_issues,
            "single_line_ifs": single_line_ifs,
            "switch_missing_default": switch_missing_default,
        }

        return file_metrics

    # ---------------------------------------------------------------------
    # Core metrics: LOC / comments
    # ---------------------------------------------------------------------

    def _classify_lines(self, lines: List[str]) -> Tuple[int, int, int, int]:
        total = len(lines)
        code_lines = 0
        comment_lines = 0
        blank_lines = 0

        in_block_comment = False

        for line in lines:
            stripped = line.strip()
            if not stripped:
                blank_lines += 1
                continue

            # Very rough comment classification to compute counts
            # (We use a more precise stripper for actual parsing)
            if in_block_comment:
                comment_lines += 1
                if "*/" in stripped:
                    in_block_comment = False
                continue

            if stripped.startswith("/*"):
                comment_lines += 1
                if "*/" not in stripped:
                    in_block_comment = True
                continue

            if stripped.startswith("//"):
                comment_lines += 1
                continue

            # Mixed code/comment lines count as code here
            code_lines += 1

        return total, code_lines, comment_lines, blank_lines

    # ---------------------------------------------------------------------
    # Comment / string stripping
    # ---------------------------------------------------------------------

    def _strip_comments_and_strings(self, source: str) -> Tuple[str, str]:
        """
        Returns:
          (code_without_comments, code_without_comments_or_strings)

        We preserve newlines to keep line counts stable.
        """
        # Remove block comments but preserve line breaks
        def _block_replacer(match: re.Match) -> str:
            text = match.group(0)
            # Keep the same number of newlines
            newline_count = text.count("\n")
            return "\n" * newline_count

        no_block = self._BLOCK_COMMENT_RE.sub(_block_replacer, source)
        # Remove line comments
        no_line = self._LINE_COMMENT_RE.sub("", no_block)

        # code_without_comments
        code_no_comments = no_line

        # Strip strings (keep length/newlines)
        def _string_replacer(match: re.Match) -> str:
            s = match.group(0)
            return '"' + " " * (len(s) - 2) + '"' if len(s) >= 2 else '""'

        no_strings = self._STRING_RE.sub(_string_replacer, code_no_comments)
        code_no_comments_or_strings = no_strings

        return code_no_comments, code_no_comments_or_strings

    # ---------------------------------------------------------------------
    # Cyclomatic complexity
    # ---------------------------------------------------------------------

    def _compute_cyclomatic_complexity(self, code: str) -> int:
        """
        Heuristic cyclomatic complexity based on decision points.
        """
        # Normalize "else if" → "if" to avoid double counting
        code = re.sub(r"\belse\s+if\b", "if", code)

        complexity = 1  # baseline

        decision_keywords = [
            r"\bif\b",
            r"\bfor\b",
            r"\bwhile\b",
            r"\bcase\b",
            r"\bdefault\b",
            r"\bcatch\b",
        ]

        for kw in decision_keywords:
            complexity += len(re.findall(kw, code))

        # ternary operator '?'
        complexity += len(re.findall(r"\?", code))

        # logical AND/OR
        complexity += len(re.findall(r"&&|\|\|", code))

        # Ensure >= 1
        if complexity < 1:
            complexity = 1

        return complexity

    # ---------------------------------------------------------------------
    # Halstead volume (approximate)
    # ---------------------------------------------------------------------

    def _compute_halstead_volume(self, code: str) -> float:
        """
        Approximate Halstead Volume using 
        sets of operators and operands identified by simple heuristics.
        """
        # Very rough operators / operands sets
        operator_tokens = set(
            [
                "+",
                "-",
                "*",
                "/",
                "%",
                "++",
                "--",
                "==",
                "!=",
                "<",
                "<=",
                ">",
                ">=",
                "&&",
                "||",
                "!",
                "&",
                "|",
                "^",
                "~",
                "<<",
                ">>",
                "=",
                "+=",
                "-=",
                "*=",
                "/=",
                "%=",
                "&=",
                "|=",
                "^=",
                "<<=",
                ">>=",
                "?",
                ":",
                "->",
                ".",
                ",",
                ";",
            ]
        )

        # Tokenize in a crude way
        tokens = re.findall(r"[A-Za-z_][A-Za-z0-9_]*|==|!=|<=|>=|&&|\|\||<<=|>>=|<<|>>|->|[-+*/%=!<>&^|?:;,.]", code)

        operators: List[str] = []
        operands: List[str] = []

        for t in tokens:
            if t in operator_tokens:
                operators.append(t)
            else:
                operands.append(t)

        n1 = len(set(operators))
        n2 = len(set(operands))
        N1 = len(operators)
        N2 = len(operands)

        n = n1 + n2
        N = N1 + N2

        if n == 0 or N == 0:
            return 0.0

        volume = N * self._safe_log2(n)
        return float(volume)

    @staticmethod
    def _safe_log2(x: float) -> float:
        return math.log2(x) if x > 0 else 0.0

    @staticmethod
    def _safe_ln(x: float) -> float:
        return math.log(x) if x > 0 else 0.0

    # ---------------------------------------------------------------------
    # Maintainability Index
    # ---------------------------------------------------------------------

    def _compute_maintainability_index(self, volume: float, complexity: int, loc: int) -> float:
        """
        SEI-style Maintainability Index, scaled to [0, 100].

        Base formula:
          MI = 171 - 5.2 * ln(V) - 0.23 * G - 16.2 * ln(LOC)
        """
        V = max(1.0, volume)
        G = max(1, complexity)
        L = max(1, loc)

        mi_raw = 171.0 - 5.2 * self._safe_ln(V) - 0.23 * G - 16.2 * self._safe_ln(L)
        mi_scaled = mi_raw * (100.0 / 171.0)

        # Clamp
        mi_scaled = max(0.0, min(100.0, mi_scaled))

        return mi_scaled

    # ---------------------------------------------------------------------
    # Doxygen / function documentation
    # ---------------------------------------------------------------------

    def _compute_doxygen_stats(
        self, original_source: str, code_no_comments: str
    ) -> Tuple[int, int]:
        """
        Estimate number of functions and how many have Doxygen docs.
        """
        func_matches = list(self._FUNC_SIG_RE.finditer(code_no_comments))
        func_count = len(func_matches)

        # Doxygen block and line comments in original source
        block_docs = list(self._DOXY_BLOCK_RE.finditer(original_source))
        line_docs = list(self._DOXY_LINE_RE.finditer(original_source))

        documented_funcs = 0

        # Very rough heuristic: if there is a doxygen block or a set of /// lines
        # immediately before a function signature, count it as documented.
        for fm in func_matches:
            start = fm.start()
            # Look back a window
            context_start = max(0, start - 500)
            context = original_source[context_start:start]

            has_block = bool(re.search(r"/\*\*[\s\S]*?\*/\s*$", context, re.MULTILINE))
            has_line = bool(re.search(r"(^\s*///.*\n)+\s*$", context, re.MULTILINE))

            if has_block or has_line:
                documented_funcs += 1

        return func_count, documented_funcs

    # ---------------------------------------------------------------------
    # Header guards, legal header, #endif comments
    # ---------------------------------------------------------------------

    def _check_header_guard(self, lines: List[str]) -> Tuple[bool, bool]:
        """
        Return (has_header_guard, has_pragma_once).
        We only scan top ~50 lines.
        """
        has_guard = False
        has_pragma_once = False

        for i, line in enumerate(lines[:50]):
            stripped = line.strip()
            if not stripped:
                continue
            if stripped.startswith("//") or stripped.startswith("/*"):
                # skip comments at top
                continue
            if stripped.lower().startswith("#pragma once"):
                has_pragma_once = True
                break
            if stripped.startswith("#ifndef") and (i + 1) < len(lines):
                next_strip = lines[i + 1].strip()
                if next_strip.startswith("#define"):
                    has_guard = True
                break
            # first non-comment, non-guard line
            break

        return has_guard, has_pragma_once

    def _check_legal_header(self, lines: List[str]) -> str:
        """
        Simple heuristic for legal/copyright header.
        Returns issue string or "".
        """
        text_top = "\n".join(lines[:40])
        # Extremely simple heuristics. Adapt to your org’s standard phrases.
        patterns = [
            r"Copyright",
            r"SPDX-License-Identifier",
            r"All rights reserved",
            r"Redistribution and use",
        ]
        for pat in patterns:
            if re.search(pat, text_top, re.IGNORECASE):
                return ""
        return "Missing or unrecognized legal / license header"

    def _check_endif_comments(self, lines: List[str]) -> List[str]:
        """
        Look for '#endif' lines and check for trailing comments.
        """
        issues: List[str] = []
        for idx, line in enumerate(lines, start=1):
            stripped = line.strip()
            if stripped.startswith("#endif"):
                # If no comment at all, consider it an issue (configurable)
                if "//" not in stripped and "/*" not in stripped:
                    issues.append(f"Line {idx}: #endif without trailing comment")
        return issues

    # ---------------------------------------------------------------------
    # Banned APIs
    # ---------------------------------------------------------------------

    def _scan_banned_apis(self, code: str) -> Tuple[List[str], List[str]]:
        banned_hits: List[str] = []
        future_hits: List[str] = []

        for pat in self._BANNED_API_PATTERNS:
            for _m in pat.finditer(code):
                banned_hits.append(pat.pattern)

        for pat in self._FUTURE_BANNED_API_PATTERNS:
            for _m in pat.finditer(code):
                future_hits.append(pat.pattern)

        return banned_hits, future_hits

    # ---------------------------------------------------------------------
    # Formatting: long lines, tabs, trailing whitespace, TODO/FIXME
    # ---------------------------------------------------------------------

    def _scan_formatting(self, lines: List[str]) -> Dict[str, Any]:
        long_lines: List[int] = []
        tabs: List[int] = []
        trailing_ws: List[int] = []
        todos: List[int] = []

        for idx, line in enumerate(lines, start=1):
            if len(line) > self._LONG_LINE_LIMIT:
                long_lines.append(idx)
            if self._TAB_PATTERN.search(line):
                tabs.append(idx)
            if self._TRAILING_WS_PATTERN.search(line):
                trailing_ws.append(idx)
            if self._TODO_PATTERN.search(line):
                todos.append(idx)

        return {
            "long_lines": long_lines,
            "tabs": tabs,
            "trailing_whitespace": trailing_ws,
            "todo_lines": todos,
        }

    # ---------------------------------------------------------------------
    # Single-line ifs and switch default
    # ---------------------------------------------------------------------

    def _detect_single_line_ifs(self, code: str) -> List[int]:
        """
        Heuristic: detect 'if (...) statement;' on a single line (without { }).
        """
        single_line_ifs: List[int] = []
        for idx, line in enumerate(code.splitlines(), start=1):
            stripped = line.strip()
            # Quick filter
            if "if" not in stripped:
                continue
            # Heuristic: 'if (...) ...;' and no '{'
            if re.search(r"\bif\s*\([^)]*\)\s*[^{};]+;", stripped) and "{" not in stripped:
                single_line_ifs.append(idx)
        return single_line_ifs

    def _check_switch_default(self, code: str) -> bool:
        """
        Returns True if any switch statement is missing a default label.
        """
        # Very rough: for each "switch", see if there is a "default:" after it
        # in a limited range.
        missing_default = False

        for m in re.finditer(r"\bswitch\s*\([^)]*\)", code):
            start_idx = m.end()
            # Look ahead a window of text
            window = code[start_idx : start_idx + 2000]
            # If there's a closing brace before 'default', treat as missing
            brace_pos = window.find("}")
            default_pos = window.find("default:")
            if default_pos == -1 or (brace_pos != -1 and brace_pos < default_pos):
                missing_default = True

        return missing_default

    # ---------------------------------------------------------------------
    # Aggregation across files
    # ---------------------------------------------------------------------

    def _aggregate_metrics(
        self, per_file: List[Dict[str, Any]]
    ) -> Tuple[Dict[str, Any], List[str], float]:
        issues: List[str] = []

        if not per_file:
            return (
                {
                    "files": [],
                    "avg_mi": 0.0,
                    "min_mi": 0.0,
                    "max_mi": 0.0,
                    "avg_complexity": 0.0,
                    "avg_comment_ratio": 0.0,
                    "total_banned_apis": 0,
                    "total_future_banned_apis": 0,
                },
                ["No C/C++ files analyzed."],
                0.0,
            )

        mi_values = [f["maintainability_index"] for f in per_file]
        cyclomatic_values = [f["cyclomatic_complexity"] for f in per_file]
        comment_ratios = [f["comment_ratio"] for f in per_file]

        avg_mi = sum(mi_values) / len(mi_values)
        min_mi = min(mi_values)
        max_mi = max(mi_values)

        avg_complexity = sum(cyclomatic_values) / len(cyclomatic_values)
        avg_comment_ratio = sum(comment_ratios) / len(comment_ratios)

        total_banned = sum(len(f["banned_api_hits"]) for f in per_file)
        total_future_banned = sum(len(f["future_banned_api_hits"]) for f in per_file)

        # Hotspots: worst MI and highest complexity
        worst_mi_files = sorted(per_file, key=lambda x: x["maintainability_index"])[:10]
        highest_complexity_files = sorted(
            per_file, key=lambda x: x["cyclomatic_complexity"], reverse=True
        )[:10]

        # Build top-level issues
        if avg_mi < 50:
            issues.append(
                f"Average maintainability index is low ({avg_mi:.1f}). Significant refactoring may be needed."
            )
        if total_banned > 0:
            issues.append(f"Detected {total_banned} usage(s) of banned C APIs.")
        if total_future_banned > 0:
            issues.append(
                f"Detected {total_future_banned} usage(s) of potentially problematic APIs."
            )

        # Header/guard issues
        header_guard_issues = [
            f for f in per_file if f["header_guard_issue"] or f["legal_header_issue"]
        ]
        if header_guard_issues:
            issues.append(
                f"{len(header_guard_issues)} header files have missing header guards or legal headers."
            )

        # Switch default issues
        switch_issues = [f for f in per_file if f["switch_missing_default"]]
        if switch_issues:
            issues.append(
                f"{len(switch_issues)} file(s) contain switch statements missing default labels."
            )

        # Aggregate metrics object
        metrics: Dict[str, Any] = {
            "files": per_file,
            "avg_mi": avg_mi,
            "min_mi": min_mi,
            "max_mi": max_mi,
            "avg_complexity": avg_complexity,
            "avg_comment_ratio": avg_comment_ratio,
            "total_banned_apis": total_banned,
            "total_future_banned_apis": total_future_banned,
            "worst_mi_files": [f["file"] for f in worst_mi_files],
            "highest_complexity_files": [f["file"] for f in highest_complexity_files],
            "header_guard_issues_files": [f["file"] for f in header_guard_issues],
            "switch_missing_default_files": [f["file"] for f in switch_issues],
        }

        # Score heuristic
        score = self._compute_aggregate_score(avg_mi, total_banned, avg_complexity)

        return metrics, issues, score

    def _compute_aggregate_score(
        self, avg_mi: float, total_banned: int, avg_complexity: float
    ) -> float:
        """
        Simple heuristic for global maintainability score: 0–100.
        - Base from avg MI
        - Penalties for banned APIs
        - Penalties for very high average complexity
        """
        score = avg_mi  # MI already on 0–100 scale

        # Banned APIs are serious
        if total_banned > 0:
            score -= min(20.0, 2.0 * total_banned)

        # Complexity (soft penalty)
        if avg_complexity > 10:
            score -= min(15.0, (avg_complexity - 10) * 1.0)

        score = max(0.0, min(100.0, score))
        return score

    @staticmethod
    def _score_to_grade(score: float) -> str:
        if score >= 90:
            return "A"
        if score >= 80:
            return "B"
        if score >= 70:
            return "C"
        if score >= 60:
            return "D"
        return "F"